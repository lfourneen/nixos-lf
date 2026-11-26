"""Retract the legacy Gost environment from the system Flatpak override."""

import configparser
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


STORE_ITEM = r'[0-9a-z]{32}-'
STATE_NAME = re.compile(STORE_ITEM + r'flatpak-state\.json')
GENERATION = re.compile(r'system-(\d+)-link')
RECEIPT = '.nixos-lf-flatpak-proxy-migrated'
LIMIT = 1024 * 1024


def private_directory(path, owner):
    info = path.lstat()
    return stat.S_ISDIR(info.st_mode) and info.st_uid == owner and not info.st_mode & 0o022


def store_text(path, store, owner):
    path = path.resolve(strict=True)
    if not path.is_relative_to(store):
        raise ValueError('reference outside the store')
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_mode & 0o222
            or info.st_size > LIMIT):
        raise ValueError('invalid store input')
    return path.read_text()


def declared_environment(path, store, owner):
    if path.parent != store or not STATE_NAME.fullmatch(path.name) or path.is_symlink():
        raise ValueError('invalid Flatpak state reference')
    record = json.loads(store_text(path, store, owner))
    if not isinstance(record, dict) or record.get('version') != 2:
        raise ValueError('unknown Flatpak state schema')
    environment = record['overrides']['settings']['global']['Environment']
    if not isinstance(environment, dict):
        raise ValueError('invalid Flatpak environment declaration')
    return environment


def recorded_environment(record, store, owner):
    info = record.lstat()
    if not stat.S_ISLNK(info.st_mode) or info.st_uid != owner or not private_directory(record.parent, owner):
        raise ValueError('invalid Flatpak state root')
    return declared_environment(record.readlink(), store, owner)


def deployed_environment(profiles, current_system, store, owner, keys):
    """Follow the newest retained system deployment that declared proxy keys."""
    if not private_directory(profiles, owner):
        return {}
    active_info = current_system.lstat()
    if not stat.S_ISLNK(active_info.st_mode) or active_info.st_uid != owner:
        return {}
    active = current_system.resolve(strict=True)
    prefix = re.escape(str(store) + '/')
    exec_start = re.compile(r'^ExecStart=(' + prefix + STORE_ITEM + r'flatpak-managed-install)$', re.M)
    new_state = re.compile(r'^NEW_STATE=\$\(' + prefix + STORE_ITEM + r'coreutils[^/\s]*/bin/cat '
                           r'(' + prefix + STATE_NAME.pattern + r')\)$', re.M)
    roots = []
    for path in profiles.iterdir():
        number = GENERATION.fullmatch(path.name)
        if not number:
            continue
        try:
            info = path.lstat()
            target = path.readlink()
            if (stat.S_ISLNK(info.st_mode) and info.st_uid == owner and target.parent == store
                    and re.fullmatch(STORE_ITEM + r'nixos-system-.+', target.name)):
                roots.append((int(number[1]), target))
        except OSError:
            continue
    # If this is a temporary activation, its next normal boot may point at a
    # different system profile. In that case history cannot establish order.
    ceiling = max((number for number, path in roots if path == active), default=-1)
    for number, target in sorted(roots, reverse=True):
        if number > ceiling:
            continue
        try:
            unit = store_text(target / 'etc/systemd/system/flatpak-managed-install.service', store, owner)
            installers = exec_start.findall(unit)
            if len(installers) != 1:
                continue
            declarations = new_state.findall(store_text(Path(installers[0]), store, owner))
            if len(declarations) != 1:
                continue
            environment = declared_environment(Path(declarations[0]), store, owner)
            if keys & environment.keys():
                return environment
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return {}


def read_override(path, owner):
    def stamp(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        original = os.fstat(stream.fileno())
        if (not stat.S_ISREG(original.st_mode) or original.st_uid != owner or original.st_mode & 0o022
                or original.st_size > LIMIT):
            raise ValueError('invalid global override ownership or size')
        content = stream.read(LIMIT + 1)
        if len(content) > LIMIT or stamp(os.fstat(stream.fileno())) != stamp(original):
            raise ValueError('global override changed during read')
    return content, stamp(original)


def prune_environment(content, expected, owned):
    text = content.decode()
    parsed = configparser.ConfigParser(interpolation=None, delimiters=('=',), default_section='', strict=True)
    parsed.optionxform = str
    parsed.read_string(text)
    live = parsed['Environment'] if 'Environment' in parsed else {}
    eligible = {key for key, value in expected.items() if owned.get(key) == live.get(key) == value}
    removed = set()
    remaining = []
    environment = False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith('['):
            environment = line.strip() == '[Environment]'
        key, sep, value = line.rstrip('\r\n').partition('=')
        if environment and sep and key in eligible and value == expected[key]:
            removed.add(key)
        else:
            remaining.append(line)
    preserved = (expected.keys() & live.keys()) - removed
    if preserved:
        print('Flatpak proxy migration preserved unverified or edited keys: ' + ', '.join(sorted(preserved)),
              file=sys.stderr)
    return ''.join(remaining).encode(), removed


def migrate(path, record, profiles, current_system, store, expected, owner=0):
    if not expected or not path.parent.exists():
        return
    if not private_directory(path.parent, owner) or not private_directory(path.parent.parent, owner):
        raise ValueError('invalid override directory')
    try:
        content, original = read_override(path, owner)
    except FileNotFoundError:
        return
    receipt = path.parent.parent / RECEIPT
    try:
        environment = recorded_environment(record, store, owner)
        if (not expected.keys() & environment.keys() and not os.path.lexists(receipt)):
            environment = deployed_environment(profiles, current_system, store, owner, expected.keys())
    except (OSError, ValueError, KeyError, TypeError):
        environment = {}
    updated, removed = prune_environment(content, expected, environment)
    if removed:
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, prefix='.proxy-override-', delete=False) as output:
            temporary = Path(output.name)
            try:
                _, _, mode, uid, gid, *_ = original
                os.fchown(output.fileno(), uid, gid)
                os.fchmod(output.fileno(), stat.S_IMODE(mode))
                output.write(updated)
                output.close()
                if read_override(path, owner) != (content, original):
                    raise ValueError('global override changed before migration')
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        print('Flatpak proxy migration retracted: ' + ', '.join(sorted(removed)))
    # A known transition consumes historical ownership even if the operator
    # had already edited the conflicting values. A later explicit deployment
    # still takes precedence over this receipt and can transition again.
    if any(environment.get(key) == value for key, value in expected.items()):
        try:
            fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)


if __name__ == '__main__':
    try:
        migrate(Path('/var/lib/flatpak/overrides/global'), Path('/nix/var/nix/gcroots/flatpak-state.json'),
                Path('/nix/var/nix/profiles'), Path('/run/current-system'), Path('/nix/store'),
                json.loads(Path(sys.argv[1]).read_text()))
    except (OSError, ValueError, configparser.Error):
        print('Flatpak proxy migration could not complete for the system override', file=sys.stderr)
