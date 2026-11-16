#!/usr/bin/env python3
"""Create or adopt the root-only Age identity used for system secrets.

sops decrypts with the identity selected by sops.age.keyFile. A key under a
desktop home is readable by every process of that user, so the system services
and the desktop share one decryption identity.

Two modes are supported. `--adopt` copies an existing identity, normally
~/.config/sops/age/keys.txt, to the root-only location. Its public recipient
does not change, so no ciphertext or .sops.yaml edit is needed; access changes
only because the key now lives outside the desktop home and the old copy can be
removed. `--generate` creates a new identity; `--write-sops-config` adds its
recipient to the creation rules that cover system secrets, and existing
ciphertext still needs re-encryption with the old identity.

The private key is never printed, copied into the Nix store, or passed on a
command line. `--print-recipient` only reports the public key of an existing
file.
"""

import argparse
import os
from pathlib import Path
import pwd
import re
import shutil
import stat
import subprocess
import sys
import time

DEFAULT_DIR = "/persist/secrets/age"
DEFAULT_KEY = "system.key"
MAX_IDENTITY_BYTES = 64 * 1024
DEFAULT_SOPS_CONFIG = Path(__file__).resolve().parents[1] / ".sops.yaml"
SECRET_SAMPLES = (
    "system/secrets/secrets.yaml",
    "system/secrets/soul.yaml",
    "system/secrets/skill.yaml",
    "system/secrets/proxy.yaml",
)


class KeyError_(RuntimeError):
    """A path or tool does not meet the safety requirements."""


def _reject_symlink_components(path):
    """Refuse a path whose existing components include a symlink."""
    current = Path(path).absolute()
    parts = list(current.parents)
    for candidate in reversed(parts[:-1]):
        if candidate == Path("/"):
            continue
        try:
            info = os.lstat(candidate)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise KeyError_(f"{candidate} is a symlink; refusing to write a key below it")


def _require_private_dir(path):
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        raise KeyError_(f"{path} is not a directory")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise KeyError_(f"{path} is group- or world-writable; use mode 0700")
    if os.geteuid() == 0 and (info.st_uid != 0 or info.st_gid != 0):
        raise KeyError_(f"{path} is not owned by root; refusing to store a root key there")


def desktop_identity():
    """Return the invoking desktop user's default sops identity."""
    user = os.environ.get("SUDO_USER")
    if not user and os.environ.get("SUDO_UID"):
        user = pwd.getpwuid(int(os.environ["SUDO_UID"])).pw_name
    if not user or user == "root":
        raise KeyError_("--adopt-desktop needs the desktop user; run it through sudo or pass --adopt PATH")
    try:
        home = pwd.getpwnam(user).pw_dir
    except KeyError:
        raise KeyError_(f"desktop user does not exist: {user}") from None
    return Path(home) / ".config" / "sops" / "age" / "keys.txt"


def _age_keygen(age_keygen=None):
    tool = age_keygen or shutil.which("age-keygen")
    if not tool:
        raise KeyError_("age-keygen is not on PATH")
    return tool


def _recipient(key_path, age_keygen=None):
    tool = _age_keygen(age_keygen)
    result = subprocess.run(
        [tool, "-y", str(key_path)], capture_output=True, text=True, check=False
    )
    recipient = result.stdout.strip()
    if result.returncode == 0 and recipient.startswith("age1"):
        return recipient

    marker = "# public key: "
    with key_path.open("r", encoding="utf-8") as handle:
        for _ in range(5):
            line = handle.readline()
            if not line:
                break
            if line.startswith(marker):
                found = line[len(marker):].strip()
                if found.startswith("age1"):
                    return found
    raise KeyError_(f"{key_path} has no readable age public key")


def _prepare_directory(directory):
    _reject_symlink_components(directory)

    # The key must live on the encrypted persistent filesystem. Never create
    # /persist itself: a directory on the ephemeral root would look valid until
    # the next reboot and then lose every secret that depends on it.
    if directory == Path("/persist") or Path("/persist") in directory.parents:
        if not os.path.ismount("/persist"):
            raise KeyError_(
                "/persist is not mounted; start the persistent filesystem before installing the system key"
            )

    missing = []
    current = directory
    while not current.exists():
        missing.append(current)
        current = current.parent
    if not current.is_dir():
        raise KeyError_(f"{current} is not a directory")
    for path in reversed(missing):
        path.mkdir(mode=0o700)
        os.chmod(path, 0o700)
        if os.geteuid() == 0:
            os.chown(path, 0, 0)

    _require_private_dir(directory)
    os.chmod(directory, 0o700)


def _write_identity(directory, key_name, identity):
    if key_name in ("", ".", "..") or "/" in key_name:
        raise KeyError_("--key-name must be a plain file name")
    _prepare_directory(directory)

    key_path = directory / key_name
    if os.path.lexists(key_path):
        raise KeyError_(f"{key_path} already exists; keep the existing key and back it up")

    temporary = directory / f".{key_name}.{os.getpid()}"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, identity)
            os.fsync(fd)
        finally:
            os.close(fd)
        if os.geteuid() == 0:
            os.chown(temporary, 0, 0)
        os.chmod(temporary, 0o600)
        os.replace(temporary, key_path)
        # Make the rename durable before the operator relies on the key.
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return key_path


def create_key(directory, key_name, allow_unprivileged=False, age_keygen=None):
    if os.geteuid() != 0 and not allow_unprivileged:
        raise KeyError_("run as root, or pass --allow-unprivileged for a throwaway directory")
    directory = Path(directory).absolute()

    tool = _age_keygen(age_keygen)
    result = subprocess.run([tool], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise KeyError_(f"age-keygen failed: {result.stderr.strip()}")
    identity = result.stdout
    if "AGE-SECRET-KEY-1" not in identity:
        raise KeyError_("age-keygen did not return an identity")

    key_path = _write_identity(directory, key_name, identity.encode("utf-8"))
    return key_path, _recipient(key_path, age_keygen)


def adopt_identity(source, directory, key_name, allow_unprivileged=False, age_keygen=None):
    if os.geteuid() != 0 and not allow_unprivileged:
        raise KeyError_("run as root, or pass --allow-unprivileged for a throwaway directory")
    raw_source = str(source)
    if raw_source.startswith("~") or "$" in raw_source:
        raise KeyError_(
            "source path was not expanded by the shell; pass an absolute path "
            "such as /home/<user>/Downloads/age.txt"
        )
    source = Path(raw_source).absolute()
    directory = Path(directory).absolute()

    try:
        info = os.lstat(source)
    except FileNotFoundError:
        raise KeyError_(f"source identity does not exist: {source}") from None
    if stat.S_ISLNK(info.st_mode):
        raise KeyError_(f"{source} is a symlink; point --adopt at the real identity file")
    if not stat.S_ISREG(info.st_mode):
        raise KeyError_(f"{source} is not a regular file")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise KeyError_(f"{source} is readable by group or others; run chmod 600 on it first")
    if info.st_size > MAX_IDENTITY_BYTES:
        raise KeyError_(f"{source} is larger than a single Age identity")

    with source.open("rb") as handle:
        identity = handle.read(MAX_IDENTITY_BYTES + 1)
    if len(identity) > MAX_IDENTITY_BYTES:
        raise KeyError_(f"{source} is larger than a single Age identity")
    if b"AGE-SECRET-KEY-1" not in identity:
        raise KeyError_(f"{source} does not contain an Age identity")

    target = directory / key_name
    if target == source:
        raise KeyError_(f"{source} is already the target identity path")
    key_path = _write_identity(directory, key_name, identity)
    return key_path, _recipient(key_path, age_keygen), source


def _yaml_value(value):
    value = value.strip()
    if value.startswith("#"):
        return ""
    if value and value[0] in "'\"":
        end = value.find(value[0], 1)
        if end != -1:
            return value[1:end]
    return value.split(" #", 1)[0].strip()


def _secret_rules(lines):
    """Return (start, end, indent) for creation rules matching system secrets."""
    rule = re.compile(r"^(?P<indent>\s*)(?:-\s+)?path_regex:\s*(?P<value>.+?)\s*$")
    blocks = []
    for index, line in enumerate(lines):
        match = rule.match(line)
        if not match:
            continue
        pattern = _yaml_value(match.group("value"))
        try:
            selected = any(re.search(pattern, sample) for sample in SECRET_SAMPLES)
        except re.error:
            continue
        if not selected:
            continue
        indent = len(match.group("indent"))
        end = len(lines)
        for later in range(index + 1, len(lines)):
            if re.match(r"^\s*-\s+", lines[later]) and (
                len(lines[later]) - len(lines[later].lstrip()) <= indent
            ):
                end = later
                break
        blocks.append((index, end, indent))
    return blocks


def add_sops_recipient(config_path, recipient):
    """Add an Age recipient to every creation rule covering system secrets."""
    config_path = Path(config_path).absolute()
    try:
        info = os.lstat(config_path)
    except FileNotFoundError:
        raise KeyError_(f"sops config does not exist: {config_path}") from None
    if stat.S_ISLNK(info.st_mode):
        raise KeyError_(f"{config_path} is a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise KeyError_(f"{config_path} is not a regular file")

    original = config_path.read_bytes()
    lines = original.decode("utf-8").splitlines(keepends=True)
    blocks = _secret_rules(lines)
    if not blocks:
        raise KeyError_(
            "no creation rule in "
            f"{config_path} matches system/secrets/*.yaml; add the recipient manually"
        )

    inserted = False
    for start, end, _indent in sorted(blocks, reverse=True):
        age_index = None
        age_indent = 0
        for index in range(start, end):
            match = re.match(r"^(\s*)(?:-\s+)?age:\s*(.*)$", lines[index])
            if match:
                age_index = index
                age_indent = len(match.group(1))
                if match.group(2).strip():
                    raise KeyError_(
                        f"{config_path} uses an inline age list; add the recipient manually"
                    )
                break
        if age_index is None:
            raise KeyError_(
                f"no age: list covers system secrets in {config_path}; add the recipient manually"
            )

        item_indent = None
        insert_at = age_index + 1
        for index in range(age_index + 1, end):
            stripped = lines[index].strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = re.match(r"^(\s*)-\s", lines[index])
            if match:
                item_indent = len(match.group(1))
                insert_at = index + 1
                continue
            if len(lines[index]) - len(lines[index].lstrip()) <= age_indent:
                break

        block = "".join(lines[start:end])
        if f'"{recipient}"' in block or f"'{recipient}'" in block or recipient in block:
            continue
        if item_indent is None:
            item_indent = age_indent + 4
        lines.insert(insert_at, " " * item_indent + f'- "{recipient}"\n')
        inserted = True

    if not inserted:
        return False, None

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = config_path.with_name(f"{config_path.name}.bak-{stamp}-{os.getpid()}")
    with backup.open("xb") as handle:
        handle.write(original)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(backup, 0o600)
    if os.geteuid() == 0:
        os.chown(backup, 0, 0)

    payload = "".join(lines).encode("utf-8")
    temporary = config_path.with_name(f".{config_path.name}.{os.getpid()}")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, payload)
            os.fsync(fd)
            os.fchmod(fd, stat.S_IMODE(info.st_mode))
            if os.geteuid() == 0:
                os.fchown(fd, info.st_uid, info.st_gid)
        finally:
            os.close(fd)
        os.replace(temporary, config_path)
        directory_fd = os.open(config_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    return True, backup


def _print_next_steps(key_path, recipient, adopted_from=None, sops_result=None):
    print(f"public recipient: {recipient}")
    print()
    if adopted_from is not None:
        print(f"adopted {adopted_from} -> {key_path} (0600 root:root)")
        print("The recipient is unchanged, so no .sops.yaml edit or ciphertext")
        print("re-encryption is needed for this step.")
        print()
        print("Next steps:")
        print("  1. Rebuild and verify sops-install-secrets and the services that")
        print("     consume secrets.")
        print(f"  2. Remove {adopted_from} after the check; keeping that copy")
        print("     leaves the key readable by the desktop identity.")
        print(f"  3. Keep an offline backup of {key_path}. Losing it makes the")
        print("     system secrets unrecoverable.")
        print()
        print("Moving the key does not revoke access obtained before the move;")
        print("rotate exposed credentials separately.")
        return

    print(f"created {key_path} (0600 root:root)")
    print()
    if sops_result is not None:
        config, backup, changed = sops_result
        if changed:
            print(f"added the recipient to {config} (backup: {backup})")
        else:
            print(f"recipient already present in {config}")
        print()
        print("Next steps:")
        print(f"  1. Back up {key_path} offline. Losing it makes the system secrets")
        print("     unrecoverable.")
        print("  2. If the existing ciphertext uses another identity, re-encrypt it")
        print("     before removing that identity:")
        print("       for f in system/secrets/{secrets,soul,skill,proxy}.yaml; do")
        print("         sudo SOPS_AGE_KEY_FILE=<old-key> sops updatekeys \"$f\"")
        print("       done")
        print("  3. Rebuild and verify sops-install-secrets and the services that")
        print("     consume secrets.")
        print("  4. Edit system secrets as root with SOPS_AGE_KEY_FILE=<new-key>.")
        return

    print("This is a new identity. Next steps:")
    print(f"  1. Back up {key_path} offline. Losing it makes the system secrets")
    print("     unrecoverable.")
    print("  2. Add the recipient to .sops.yaml for system/secrets/*.yaml.")
    print("     Use --write-sops-config to do this automatically.")
    print("  3. Re-encrypt with `sops updatekeys` while the old key still decrypts.")
    print("  4. The option is enabled by default. Add the recipient and")
    print("     re-encrypt before rebuilding; set it to false to fall back.")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--directory", default=DEFAULT_DIR, help=f"key directory (default {DEFAULT_DIR})")
    parser.add_argument("--key-name", default=DEFAULT_KEY, help=f"key file name (default {DEFAULT_KEY})")
    parser.add_argument("--allow-unprivileged", action="store_true", help="testing only: allow a non-root user in a throwaway directory")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--adopt", metavar="SOURCE", help="copy an existing identity and keep its recipient")
    actions.add_argument("--adopt-desktop", action="store_true", help="copy the invoking desktop user's default identity")
    actions.add_argument("--generate", action="store_true", help="create a new identity and print its recipient")
    actions.add_argument("--print-recipient", action="store_true", help="print the public recipient of an existing key and exit")
    parser.add_argument("--write-sops-config", action="store_true", help="add a generated recipient to .sops.yaml")
    parser.add_argument("--sops-config", type=Path, metavar="PATH", help="sops config path (default repository .sops.yaml)")
    args = parser.parse_args(argv)
    if not (args.adopt or args.adopt_desktop or args.generate or args.print_recipient):
        parser.error("choose --adopt SOURCE, --adopt-desktop, --generate, or --print-recipient")
    if args.write_sops_config and not args.generate:
        parser.error("--write-sops-config is only used with --generate")
    if args.sops_config is not None and not args.write_sops_config:
        parser.error("--sops-config requires --write-sops-config")

    directory = Path(args.directory).absolute()
    key_path = directory / args.key_name
    try:
        if args.print_recipient:
            if not key_path.is_file() or key_path.is_symlink():
                raise KeyError_(f"{key_path} does not exist or is not a regular file")
            _require_private_dir(directory)
            print(_recipient(key_path))
            return 0

        if args.adopt or args.adopt_desktop:
            source_path = args.adopt if args.adopt else desktop_identity()
            key_path, recipient, source = adopt_identity(
                source_path, directory, args.key_name, args.allow_unprivileged
            )
            _print_next_steps(key_path, recipient, adopted_from=source)
        else:
            key_path, recipient = create_key(
                directory, args.key_name, args.allow_unprivileged
            )
            if args.write_sops_config:
                config = (args.sops_config or DEFAULT_SOPS_CONFIG).absolute()
                try:
                    changed, backup = add_sops_recipient(config, recipient)
                except (KeyError_, OSError) as error:
                    print(f"created {key_path} (0600 root:root)")
                    print(f"public recipient: {recipient}")
                    print(f"error: {error}", file=sys.stderr)
                    print("Add the recipient to .sops.yaml manually before enabling the option.", file=sys.stderr)
                    return 1
                _print_next_steps(
                    key_path, recipient, sops_result=(config, backup, changed)
                )
            else:
                _print_next_steps(key_path, recipient)
    except (KeyError_, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
