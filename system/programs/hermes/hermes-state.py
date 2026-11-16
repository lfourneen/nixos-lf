#!/usr/bin/env python3
"""Create the Hermes state layout and verify root-managed paths.

Runs as root during activation. The state root belongs to root and only
fixed writable subdirectories belong to the service user. Every
component is therefore opened relative to a directory descriptor with
O_NOFOLLOW. A symlink or a hardlinked file in a root-managed path stops
activation; activation never follows or repairs such a path.

The migration that normalizes an existing tree writes a manifest with the
old owners and modes into a private backup directory and is recorded by a
version marker, so later activations do not touch the data again.
"""

import argparse
import errno
import grp
import os
import pwd
import stat
import sys
import time

STATE_ROOT = "/var/lib/hermes"

# The only entries this script creates, with their final modes.
LAYOUT = (
    (".hermes", 0o700),
    ("home", 0o700),
    ("workspace", 0o2770),
    (".local", 0o700),
    (".config", 0o700),
    (".cache", 0o700),
    (".playwright-profiles", 0o700),
)
RUNTIME_SUBDIRS = ("cron", "sessions", "logs", "memories", "plugins", "skills")
RUNTIME_MODE = 0o700

# The service and desktop group can traverse to the shared workspace.
ROOT_MODE = 0o750
GITCONFIG = ".gitconfig"
BACKUP = ".backup"
BACKUP_MODE = 0o700
MANIFEST = "manifest.txt"
MANIFEST_MODE = 0o600
MARKER = ".state-version"
MARKER_VERSION = 4
MARKER_MODE = 0o644

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class StateError(Exception):
    pass


def fail(message):
    raise StateError(message)


def lstat(parent_fd, name, path):
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        fail(f"{path}: cannot stat: {exc.strerror}")


def open_dir(parent_fd, name, path, device):
    try:
        fd = os.open(name, DIR_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            fail(f"{path}: not a plain directory; activation does not follow symlinks")
        fail(f"{path}: cannot open: {exc.strerror}")
    st = os.fstat(fd)
    if st.st_dev != device:
        os.close(fd)
        fail(f"{path}: on a different filesystem than the state root")
    return fd, st


def check_directory(parent_fd, name, path, device):
    st = lstat(parent_fd, name, path)
    if st is None:
        return None
    if stat.S_ISLNK(st.st_mode):
        fail(f"{path}: unexpected symlink; remove it manually and rerun activation")
    if not stat.S_ISDIR(st.st_mode):
        fail(f"{path}: unexpected non-directory entry")
    fd, _st = open_dir(parent_fd, name, path, device)
    os.close(fd)
    return st


def walk_backup(fd, path, device, uid, gid, depth):
    if depth > 8:
        fail(f"{path}: backup tree is nested too deeply")
    for name in sorted(os.listdir(fd)):
        child = os.path.join(path, name)
        st = lstat(fd, name, child)
        if st is None:
            fail(f"{child}: vanished during validation")
        if st.st_dev != device:
            fail(f"{child}: on a different filesystem than the state root")
        if stat.S_ISLNK(st.st_mode):
            fail(f"{child}: unexpected symlink inside the backup directory")
        if stat.S_ISDIR(st.st_mode):
            if (st.st_uid, st.st_gid) != (uid, gid):
                fail(f"{child}: backup entry is not owned by the activation user")
            child_fd, _st = open_dir(fd, name, child, device)
            try:
                walk_backup(child_fd, child, device, uid, gid, depth + 1)
            finally:
                os.close(child_fd)
        elif stat.S_ISREG(st.st_mode):
            if st.st_nlink != 1:
                fail(f"{child}: hardlinked file inside the backup directory")
            if (st.st_uid, st.st_gid) != (uid, gid):
                fail(f"{child}: backup entry is not owned by the activation user")
        else:
            fail(f"{child}: unexpected file type inside the backup directory")


def validate(root_fd, root, uid, gid, euid, egid, device):
    for name, _mode in LAYOUT:
        check_directory(root_fd, name, os.path.join(root, name), device)

    hermes_path = os.path.join(root, ".hermes")
    hermes_st = lstat(root_fd, ".hermes", hermes_path)
    if hermes_st is not None:
        hermes_fd, _st = open_dir(root_fd, ".hermes", hermes_path, device)
        try:
            for name in RUNTIME_SUBDIRS:
                check_directory(
                    hermes_fd, name, os.path.join(hermes_path, name), device
                )
        finally:
            os.close(hermes_fd)

    gitconfig = os.path.join(root, GITCONFIG)
    st = lstat(root_fd, GITCONFIG, gitconfig)
    if st is not None:
        if stat.S_ISLNK(st.st_mode):
            fail(f"{gitconfig}: unexpected symlink; remove it manually and rerun activation")
        if not stat.S_ISREG(st.st_mode):
            fail(f"{gitconfig}: expected a regular file")
        if st.st_dev != device:
            fail(f"{gitconfig}: on a different filesystem than the state root")
        if st.st_nlink != 1:
            fail(f"{gitconfig}: hardlinked file; activation refuses to write through it")

    backup = os.path.join(root, BACKUP)
    st = lstat(root_fd, BACKUP, backup)
    if st is not None:
        if stat.S_ISLNK(st.st_mode):
            fail(f"{backup}: unexpected symlink; remove it manually and rerun activation")
        if not stat.S_ISDIR(st.st_mode):
            fail(f"{backup}: expected a directory")
        backup_fd, backup_st = open_dir(root_fd, BACKUP, backup, device)
        try:
            if (backup_st.st_uid, backup_st.st_gid) != (euid, egid):
                fail(f"{backup}: not owned by the activation user")
            os.fchmod(backup_fd, BACKUP_MODE)
            walk_backup(backup_fd, backup, device, euid, egid, 0)
        finally:
            os.close(backup_fd)

    read_marker(root_fd, os.path.join(root, MARKER), euid, egid, device)


def read_marker(root_fd, path, euid, egid, device):
    st = lstat(root_fd, MARKER, path)
    if st is None:
        return None
    if stat.S_ISLNK(st.st_mode):
        fail(f"{path}: unexpected symlink; remove it manually and rerun activation")
    if not stat.S_ISREG(st.st_mode):
        fail(f"{path}: expected a regular file")
    if st.st_dev != device:
        fail(f"{path}: on a different filesystem than the state root")
    if st.st_nlink != 1:
        fail(f"{path}: hardlinked file; activation refuses to use it")
    if (st.st_uid, st.st_gid) != (euid, egid):
        fail(f"{path}: not owned by the activation user")
    try:
        fd = os.open(MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=root_fd)
    except OSError as exc:
        fail(f"{path}: cannot open: {exc.strerror}")
    try:
        os.fchmod(fd, MARKER_MODE)
        data = os.read(fd, 64)
    finally:
        os.close(fd)
    try:
        version = int(data.strip())
    except ValueError:
        fail(f"{path}: not a state version")
    if version < 1 or version > MARKER_VERSION:
        fail(
            f"{path}: state version {version} is not supported by this module; "
            "review the activation migration before changing it"
        )
    return version


def snapshot(root_fd, root, device):
    entries = [(root, os.fstat(root_fd))]
    for name, _mode in LAYOUT:
        path = os.path.join(root, name)
        st = lstat(root_fd, name, path)
        if st is not None:
            entries.append((path, st))
    hermes_path = os.path.join(root, ".hermes")
    hermes_st = lstat(root_fd, ".hermes", hermes_path)
    if hermes_st is not None:
        hermes_fd, _st = open_dir(root_fd, ".hermes", hermes_path, device)
        try:
            for name in RUNTIME_SUBDIRS:
                path = os.path.join(hermes_path, name)
                st = lstat(hermes_fd, name, path)
                if st is not None:
                    entries.append((path, st))
        finally:
            os.close(hermes_fd)
    for name in (GITCONFIG, MARKER, BACKUP):
        path = os.path.join(root, name)
        st = lstat(root_fd, name, path)
        if st is not None:
            entries.append((path, st))
    return entries


def write_manifest(directory_fd, directory, entries, uid, gid):
    lines = ["# path\tuid\tgid\tmode\tnlink\ttype"]
    for path, st in entries:
        if stat.S_ISDIR(st.st_mode):
            kind = "directory"
        elif stat.S_ISREG(st.st_mode):
            kind = "file"
        else:
            kind = "other"
        lines.append(
            f"{path}\t{st.st_uid}\t{st.st_gid}\t"
            f"{stat.S_IMODE(st.st_mode):04o}\t{st.st_nlink}\t{kind}"
        )
    payload = ("\n".join(lines) + "\n").encode()
    try:
        fd = os.open(
            MANIFEST,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            MANIFEST_MODE,
            dir_fd=directory_fd,
        )
    except OSError as exc:
        fail(f"{os.path.join(directory, MANIFEST)}: cannot create: {exc.strerror}")
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, MANIFEST_MODE)
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def write_backup(root_fd, root, device, euid, egid, entries):
    backup = os.path.join(root, BACKUP)
    try:
        os.mkdir(BACKUP, BACKUP_MODE, dir_fd=root_fd)
    except FileExistsError:
        pass
    backup_fd, backup_st = open_dir(root_fd, BACKUP, backup, device)
    try:
        if (backup_st.st_uid, backup_st.st_gid) != (euid, egid):
            fail(f"{backup}: not owned by the activation user")
        os.fchmod(backup_fd, BACKUP_MODE)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        name = stamp
        for attempt in range(100):
            try:
                os.mkdir(name, BACKUP_MODE, dir_fd=backup_fd)
                break
            except FileExistsError:
                name = f"{stamp}.{attempt + 1}"
        else:
            fail(f"{backup}: cannot allocate a backup directory")
        directory = os.path.join(backup, name)
        directory_fd, _st = open_dir(backup_fd, name, directory, device)
        try:
            os.fchown(directory_fd, euid, egid)
            os.fchmod(directory_fd, BACKUP_MODE)
            write_manifest(directory_fd, directory, entries, euid, egid)
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        os.fsync(backup_fd)
    finally:
        os.close(backup_fd)


def ensure_dir(parent_fd, name, path, mode, uid, gid, device):
    st = lstat(parent_fd, name, path)
    if st is None:
        try:
            os.mkdir(name, mode, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except OSError as exc:
            fail(f"{path}: cannot create directory: {exc.strerror}")
        st = lstat(parent_fd, name, path)
        if st is None:
            fail(f"{path}: disappeared while it was created")
    if stat.S_ISLNK(st.st_mode):
        fail(f"{path}: unexpected symlink; remove it manually and rerun activation")
    if not stat.S_ISDIR(st.st_mode):
        fail(f"{path}: unexpected non-directory entry")
    fd, _st = open_dir(parent_fd, name, path, device)
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
    finally:
        os.close(fd)


def write_marker(root_fd, path, euid, egid):
    try:
        os.unlink(MARKER, dir_fd=root_fd)
    except FileNotFoundError:
        pass
    except OSError as exc:
        fail(f"{path}: cannot replace old marker: {exc.strerror}")
    try:
        fd = os.open(
            MARKER,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            MARKER_MODE,
            dir_fd=root_fd,
        )
    except OSError as exc:
        fail(f"{path}: cannot create marker: {exc.strerror}")
    try:
        os.fchown(fd, euid, egid)
        os.fchmod(fd, MARKER_MODE)
        os.write(fd, f"{MARKER_VERSION}\n".encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def managed_parent_paths(files):
    """Return the fixed parent directories of relative managed file paths."""
    parents = set()
    for name in files:
        parts = name.split("/")
        if len(parts) > 33 or any(part in ("", ".", "..") for part in parts) or any(
            ord(char) < 32 or ord(char) == 127 for char in name
        ):
            fail(f"invalid managed file path: {name!r}")
        parents.update(tuple(parts[:end]) for end in range(1, len(parts)))
    return sorted(parents)


def managed_parent_entries(root_fd, root, device, parents, uid=None, gid=None):
    """Inspect or prepare declared parent paths without traversing their contents."""
    path = os.path.join(root, ".hermes")
    if lstat(root_fd, ".hermes", path) is None:
        return []
    hermes_fd, _st = open_dir(root_fd, ".hermes", path, device)
    entries = []
    try:
        for parts in parents:
            parent_fd = os.dup(hermes_fd)
            child = path
            try:
                for name in parts:
                    child = os.path.join(child, name)
                    if uid is not None:
                        ensure_dir(parent_fd, name, child, RUNTIME_MODE, uid, gid, device)
                    if lstat(parent_fd, name, child) is None:
                        break
                    child_fd, _st = open_dir(parent_fd, name, child, device)
                    os.close(parent_fd)
                    parent_fd = child_fd
                else:
                    entries.append((child, os.fstat(parent_fd)))
            finally:
                os.close(parent_fd)
    finally:
        os.close(hermes_fd)
    return entries


def make_layout(root_fd, root, uid, gid, device):
    try:
        config_fd = os.open(GITCONFIG, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o640, dir_fd=root_fd)
    except FileExistsError:
        pass
    else:
        with os.fdopen(config_fd, "wb") as handle:
            os.fchown(handle.fileno(), os.geteuid(), gid)
            handle.write(b"[user]\n\tname = Hermes Agent\n\temail = hermes@local.domain\n")
            handle.flush()
            os.fsync(handle.fileno())
    hermes_path = os.path.join(root, ".hermes")
    ensure_dir(root_fd, ".hermes", hermes_path, LAYOUT[0][1], uid, gid, device)
    hermes_fd, _st = open_dir(root_fd, ".hermes", hermes_path, device)
    try:
        for name in RUNTIME_SUBDIRS:
            ensure_dir(
                hermes_fd,
                name,
                os.path.join(hermes_path, name),
                RUNTIME_MODE,
                uid,
                gid,
                device,
            )
    finally:
        os.close(hermes_fd)
    for name, mode in LAYOUT[1:]:
        ensure_dir(root_fd, name, os.path.join(root, name), mode, uid, gid, device)
    os.fchown(root_fd, os.geteuid(), gid)
    os.fchmod(root_fd, ROOT_MODE)


def resolve_ids(user, group):
    try:
        uid = pwd.getpwnam(user).pw_uid
    except KeyError:
        fail(f"user {user!r} does not exist")
    try:
        gid = grp.getgrnam(group).gr_gid
    except KeyError:
        fail(f"group {group!r} does not exist")
    return uid, gid


def open_root(root, uid, gid):
    if root in ("/", "/var", "/var/lib", "/etc", "/home", "/root", "/tmp", "/persist"):
        fail("refusing a system directory as the Hermes state root")
    parent_fd = os.open("/", DIR_FLAGS)
    try:
        parts = root.strip("/").split("/")
        for part in parts[:-1]:
            child_fd = os.open(part, DIR_FLAGS, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = child_fd
        try:
            fd = os.open(parts[-1], DIR_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError:
            os.mkdir(parts[-1], ROOT_MODE, dir_fd=parent_fd)
            fd = os.open(parts[-1], DIR_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            fail(f"{root}: not a plain directory; activation does not follow symlinks")
        fail(f"{root}: cannot open state root: {exc.strerror}")
    finally:
        os.close(parent_fd)
    original = os.fstat(fd)
    os.fchown(fd, os.geteuid(), gid)
    os.fchmod(fd, ROOT_MODE)
    return fd, original


def apply(root, user, group, managed_files=()):
    parents = managed_parent_paths(managed_files)
    uid, gid = resolve_ids(user, group)
    euid, egid = os.geteuid(), os.getegid()
    root = os.path.abspath(root)
    root_fd, original_root = open_root(root, uid, gid)
    try:
        device = os.fstat(root_fd).st_dev
        validate(root_fd, root, uid, gid, euid, egid, device)
        version = read_marker(root_fd, os.path.join(root, MARKER), euid, egid, device)
        parent_entries = managed_parent_entries(root_fd, root, device, parents)
        if version is not None and version == MARKER_VERSION:
            make_layout(root_fd, root, uid, gid, device)
            managed_parent_entries(root_fd, root, device, parents, uid, gid)
            return
        entries = snapshot(root_fd, root, device)
        entries[0] = (root, original_root)
        entries = list(dict(entries + parent_entries).items())
        write_backup(root_fd, root, device, euid, egid, entries)
        make_layout(root_fd, root, uid, gid, device)
        managed_parent_entries(root_fd, root, device, parents, uid, gid)
        write_marker(root_fd, os.path.join(root, MARKER), euid, egid)
    finally:
        os.close(root_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Create and verify the root-managed Hermes state layout."
    )
    parser.add_argument("--root", default=STATE_ROOT)
    parser.add_argument("--user", default="hermes")
    parser.add_argument("--group", default="hermes")
    parser.add_argument("--managed-file", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        apply(args.root, args.user, args.group, args.managed_file)
    except StateError as exc:
        print(f"hermes-state: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"hermes-state: {exc.filename}: {exc.strerror}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
