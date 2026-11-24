"""Inspect declared local skill roots without loading skills or following unknown links."""

import argparse
from contextlib import contextmanager
from itertools import islice
import json
import os
from pathlib import Path
import re
import stat

import yaml


DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
MAX_METADATA = 8192
MAX_FILE = 512 * 1024
MAX_ENTRIES = 8192
MAX_DEPTH = 12
HM_GENERATION = re.compile(r"^[0-9a-z]{32}-home-manager-files$")
SUPPORT_DIRS = {"assets", "references", "scripts", "templates"}


@contextmanager
def open_directory(path):
    fd = os.open("/", DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def read_regular(parent, name, limit, *, prefix=False):
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("not a plain file")
    with os.fdopen(os.open(name, FILE, dir_fd=parent), "rb") as stream:
        current = os.fstat(stream.fileno())
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("file changed during inspection")
        content = stream.read(limit + 1)
    if len(content) > limit and not prefix:
        raise ValueError("file exceeds inspection limit")
    return content


def managed_link(parent, name, target, root):
    actual = Path(os.readlink(name, dir_fd=parent))
    expected = Path(target)
    if actual == expected:
        return expected.is_dir()
    parts = actual.parts
    # Home Manager adds exactly one hop through its immutable generation tree.
    if (parts[:3] != ("/", "nix", "store") or len(parts) < 5
            or not HM_GENERATION.fullmatch(parts[3])
            or tuple(parts[4:]) != (*Path(root).parts, name)):
        return False
    with open_directory(actual.parent) as generation:
        return Path(os.readlink(name, dir_fd=generation)) == expected and expected.is_dir()


def compare_bundle(parent, source):
    expected = {entry.name: entry for entry in source.iterdir()}
    with os.scandir(parent) as entries:
        names = {entry.name for entry in islice(entries, len(expected) + 1)}
    if names != expected.keys():
        return False
    for name, entry in expected.items():
        if entry.is_dir():
            child = os.open(name, DIRECTORY, dir_fd=parent)
            try:
                if not compare_bundle(child, entry):
                    return False
            finally:
                os.close(child)
        elif read_regular(parent, name, MAX_FILE) != entry.read_bytes():
            return False
    return True


def check_managed(parent, name, policy, root):
    owner = os.fstat(parent).st_uid
    info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if info.st_uid != owner:
        return "owner-mismatch"
    if name in policy.get("links", {}):
        return "managed" if (
            stat.S_ISLNK(info.st_mode) and managed_link(parent, name, policy["links"][name], root)
        ) else "link-mismatch"
    if not stat.S_ISDIR(info.st_mode):
        return "unexpected-entry"
    child = os.open(name, DIRECTORY, dir_fd=parent)
    try:
        if os.fstat(child).st_mode & 0o022:
            return "writable-by-others"
        if name in policy.get("private", []):
            private = os.stat("SKILL.md", dir_fd=child, follow_symlinks=False)
            return "private" if (
                stat.S_ISREG(private.st_mode) and private.st_nlink == 1
                and private.st_uid == owner and not private.st_mode & 0o037
            ) else "private-permissions"
        source = Path(policy["copies"][name])
        return "managed-copy" if compare_bundle(child, source) else "content-mismatch"
    finally:
        os.close(child)


def skill_name(parent, filename, fallback):
    lines = read_regular(parent, filename, MAX_METADATA, prefix=True).splitlines()
    if not lines or lines[0].rstrip() != b"---":
        return fallback
    end = next((i for i, line in enumerate(lines[1:], 1) if line.rstrip() == b"---"), None)
    if end is None:
        raise ValueError("incomplete frontmatter")
    metadata = yaml.safe_load(b"\n".join(lines[1:end]))
    name = metadata.get("name") if isinstance(metadata, dict) else None
    return name if isinstance(name, str) else fallback


def inspect_root(path, policy, relative, names, expected_owner):
    results = []
    managed = set(policy.get("links", {})) | set(policy.get("copies", {})) | set(policy.get("private", []))
    budget = MAX_ENTRIES

    def record(entry, status):
        results.append({"entry": entry, "status": status})

    def scan(parent, parts):
        nonlocal budget
        if len(parts) > MAX_DEPTH:
            record("/".join(parts), "scan-limit")
            return
        with os.scandir(parent) as entries:
            filenames = sorted(entry.name for entry in islice(entries, budget))
        is_bundle = parts and "SKILL.md" in filenames
        for name in filenames:
            if budget <= 0:
                return
            budget -= 1
            if not parts and (name in managed or name.startswith(".")):
                continue
            if is_bundle and name in SUPPORT_DIRS:
                continue
            entry = "/".join((*parts, name))
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    status = "possible-name-collision" if Path(name).stem in names else "uninspected-link"
                    record(entry, status)
                elif stat.S_ISDIR(info.st_mode):
                    child = os.open(name, DIRECTORY, dir_fd=parent)
                    try:
                        scan(child, (*parts, name))
                    finally:
                        os.close(child)
                elif name.endswith(".md"):
                    fallback = parts[-1] if is_bundle and name == "SKILL.md" else Path(name).stem
                    if is_bundle and name != "SKILL.md":
                        continue
                    declared_name = skill_name(parent, name, fallback)
                    record(entry, "name-collision" if {fallback, declared_name} & names else "unmanaged")
            except (OSError, ValueError, yaml.YAMLError, RecursionError):
                record(entry, "unreadable")

    try:
        with open_directory(path) as parent:
            info = os.fstat(parent)
            if expected_owner is not None and info.st_uid != expected_owner:
                record(".", "owner-mismatch")
            if info.st_mode & 0o022:
                record(".", "writable-by-others")
            for name in sorted(managed):
                try:
                    record(name, check_managed(parent, name, policy, relative))
                except FileNotFoundError:
                    record(name, "missing")
                except (OSError, ValueError):
                    record(name, "unreadable")
            scan(parent, ())
            if budget <= 0:
                record(".", "scan-limit")
    except FileNotFoundError:
        record(".", "missing" if managed else "absent")
    except OSError:
        record(".", "unreadable")
    return results


def project_dirs(start, relative_roots):
    directories = (start, *start.parents)
    boundary = next((i for i, directory in enumerate(directories)
                     if os.path.lexists(directory / ".git")), 0)
    for directory in directories[:boundary + 1]:
        yield from (directory / relative for relative in relative_roots)


def hermes_sources(config):
    if not config:
        return {}
    path = Path(config)
    try:
        with open_directory(path.parent) as parent:
            values = yaml.safe_load(read_regular(parent, path.name, MAX_FILE))
        skills = values.get("skills", {}) if isinstance(values, dict) else None
        if not isinstance(skills, dict):
            return {"config": "unreadable"}
    except (OSError, ValueError, yaml.YAMLError, RecursionError):
        return {"config": "unreadable"}
    return {
        "project-discovery": skills.get("project_discovery") is not False,
        "trusted-projects-configured": bool(skills.get("trusted_project_dirs")),
        "external-roots-configured": bool(skills.get("external_dirs")),
        "create-root-configured": bool(skills.get("create_dir")),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("settings", type=Path)
    parser.add_argument("--project", type=Path, help="include skill roots for this workspace")
    args = parser.parse_args()
    settings = json.loads(args.settings.read_text())
    roots = {Path(path): policy for path, policy in settings["roots"].items()}
    if args.project:
        for root in project_dirs(Path(os.path.abspath(args.project)), settings["projectRoots"]):
            roots.setdefault(root, {})
    home = Path(settings["home"])
    scans = {}
    for root, policy in roots.items():
        under_home = root.is_relative_to(home)
        relative = str(root.relative_to(home)) if under_home else None
        owner = os.geteuid() if under_home else 0 if root.is_relative_to("/etc") else None
        scans[str(root)] = inspect_root(root, policy, relative, set(settings["names"]), owner)
    print(json.dumps({
        "roots": scans,
        "environment-overrides": [
            key for key, value in settings["environment"].items()
            if key in os.environ and os.environ[key] != value
        ],
        "hermes-config": hermes_sources(settings.get("hermesConfig")),
    }, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
