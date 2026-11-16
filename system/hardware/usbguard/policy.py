#!/usr/bin/env python3
"""Adopt a store-backed USBGuard policy into private persistent state."""
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys


def config_from_unit(text, store=Path("/nix/store")):
    commands = []
    section = None
    for line in text.replace("\\\n", " ").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        key, separator, value = line.partition("=")
        if section == "Service" and separator and key.strip() == "ExecStart":
            if value.strip():
                commands.append(shlex.split(value.strip()))
            else:
                commands.clear()
    if len(commands) != 1:
        raise ValueError("cannot identify USBGuard ExecStart; specify --config PATH")
    command = commands[0]
    positions = [index for index, value in enumerate(command) if value == "-c"]
    if len(positions) != 1 or positions[0] + 1 >= len(command):
        raise ValueError("USBGuard ExecStart has no unique -c path; specify --config PATH")
    config = Path(command[positions[0] + 1]).resolve()
    if not config.is_relative_to(store):
        raise ValueError("USBGuard daemon configuration is outside the store; specify --config PATH")
    return config


def active_config(systemctl="systemctl"):
    result = subprocess.run(
        [systemctl, "cat", "--no-pager", "usbguard.service"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise ValueError("cannot read usbguard.service; specify --config PATH")
    return config_from_unit(result.stdout)


def adopt(config, destination, anchor=Path("/"), store=Path("/nix/store"), systemctl="systemctl"):
    parent = destination.parent
    relative = parent.relative_to(anchor)
    fd = os.open(anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative.parts:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, 0o700, dir_fd=fd)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            st = os.fstat(fd)
            if st.st_uid != os.geteuid() or st.st_mode & 0o022:
                raise ValueError("policy parent must be root owned and not writable by others")
        try:
            existing = os.open(destination.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            with os.fdopen(existing, "rb") as handle:
                st = os.fstat(handle.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_nlink != 1 or st.st_mode & 0o077 or st.st_size == 0:
                    raise ValueError("policy must be a nonempty root-owned private regular file")
            return
        if config is None:
            config = active_config(systemctl)
        lines = config.read_text().splitlines()
        sources = []
        for line in lines:
            key, separator, value = line.strip().partition("=")
            if separator and key.strip() == "RuleFile":
                values = shlex.split(value.strip())
                if len(values) != 1:
                    raise ValueError("RuleFile must contain one path")
                sources.append(values[0])
        if len(sources) != 1:
            raise ValueError("install a private policy before activation; no store-backed policy to adopt")
        source = Path(sources[0]).resolve()
        if not source.is_relative_to(store):
            raise ValueError("policy source escapes the store")
        with source.open("rb") as handle:
            st = os.fstat(handle.fileno())
            if st.st_uid != os.geteuid() or not stat.S_ISREG(st.st_mode) or st.st_size > 1024 * 1024:
                raise ValueError("invalid policy source")
            data = handle.read(1024 * 1024 + 1)
        if not data.strip():
            raise ValueError("refusing an empty policy")
        temp = f".rules.conf.pending-{os.getpid()}"
        out = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(out, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.rename(temp, destination.name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temp, dir_fd=fd)
            except FileNotFoundError:
                pass
    finally:
        os.close(fd)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: policy.py SYSTEMCTL")
    try:
        adopt(None, Path("/persist/usbguard/rules.conf"), systemctl=sys.argv[1])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"usbguard-policy: {error}", file=sys.stderr)
        raise SystemExit(1)
