#!/usr/bin/env python3
"""Stage a root-owned, authorized core snapshot; verification needs no root.

Only config/geodata are immutable inputs. cache.db is an explicit symlink to
the service user's writable state and is excluded from the snapshot digest.
"""

import fcntl
import grp
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time


ASSETS = {
    'Country.mmdb': ('country.mmdb', 'geoip.db', 'geoip.metadb'),
    'GeoIP.dat': ('geoip.dat',),
    'GeoSite.dat': ('geosite.dat',),
    'ASN.mmdb': ('asn.mmdb',),
    'BundleMRS.7z': ('bundlemrs.7z',),
}
FILES = ('clash-verge.yaml', *ASSETS)
MAX_BYTES = 256 * 1024 * 1024


def private_dir(path, gid):
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('snapshot directory is a symlink')
    os.chown(path, os.geteuid(), gid)
    path.chmod(0o750)


def write_private(path, data, gid, mode=0o640):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        os.fchown(stream.fileno(), os.geteuid(), gid)
        os.fchmod(stream.fileno(), mode)
        stream.write(data)


def read_input(directory, relative, uid):
    """Open one desktop-owned regular file without following symlinks/races."""
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('configuration must be inside the GUI data directory')
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        source = os.open(relative.parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(source, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_uid != uid or before.st_size > MAX_BYTES:
                raise ValueError('input must be a bounded regular file owned by the desktop user')
            data = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
            if len(data) > MAX_BYTES or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('input changed while being copied')
            return data
    finally:
        os.close(fd)


def config_input(settings, config):
    relative = Path(os.path.abspath(config)).relative_to(settings['gui_data'])
    data = read_input(settings['gui_data'], relative, settings['desktop_uid'])
    # The parser rejects duplicate YAML keys. Requiring one canonical controller
    # field prevents a hidden second field from replacing this binding.
    text = data.decode('utf-8')
    text, count = re.subn(r'^external-controller-unix:.*$',
                         'external-controller-unix: /run/clash-verge-rev/core/mihomo.sock', text,
                         flags=re.MULTILINE)
    if count != 1:
        raise ValueError('profile must contain one external-controller-unix field')
    return text.encode()


def snapshot_digest(directory):
    directory = Path(directory)
    document = {}
    for name in FILES:
        path = directory / name
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            if name == 'clash-verge.yaml':
                raise ValueError('snapshot configuration is missing')
            document[name] = None
            continue
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('snapshot input is not a regular file')
            document[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()


def verify(directory, digest_file, auth_file):
    expected = Path(auth_file).read_text().strip()
    if not re.fullmatch('[0-9a-f]{64}', expected) or Path(digest_file).read_text().strip() != expected:
        raise ValueError('snapshot authorization is missing or inconsistent')
    if snapshot_digest(directory) != expected:
        raise ValueError('authorized snapshot digest mismatch')
    return expected


def atomic_authority(path, digest):
    temporary = path.with_name(path.name + '.new')
    temporary.unlink(missing_ok=True)
    write_private(temporary, (digest + '\n').encode(), os.getegid(), mode=0o600)
    temporary.replace(path)


def stage(settings, config, validate):
    runtime, state = Path(settings['runtime']), Path(settings['state'])
    gid = settings['core_gid']
    snapshots = runtime / 'snapshots'
    effective = runtime / 'effective'
    auth, digest = state / 'authorized-config', state / 'config-digest'
    private_dir(snapshots, gid)
    # All launchers and proxy-control share this lock. Failed imports consume
    # the window too. A restarted core cannot reuse a still-open import window.
    with (state / 'authorization.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (state / 'intent').read_text().strip() != 'proxy':
            raise ValueError('start Clash with clash-on; proxy intent is required')
        window = state / 'authorize-window'
        authorized = False
        try:
            age = time.time() - window.stat().st_mtime
            authorized = 0 <= age <= 300
        except FileNotFoundError:
            pass
        window.unlink(missing_ok=True)
        candidate_config = config_input(settings, config)
        if not authorized:
            verify(effective, digest, auth)
            if candidate_config != (effective / 'clash-verge.yaml').read_bytes():
                raise ValueError('configuration changed; run clash-on to authorize it')
            # Never import desktop databases on an ordinary restart.
            validate(effective)
            verify(effective, digest, auth)
            return

        candidate = Path(tempfile.mkdtemp(prefix='.candidate-', dir=snapshots))
        try:
            private_dir(candidate, gid)
            write_private(candidate / 'clash-verge.yaml', candidate_config, gid)
            names = os.listdir(settings['gui_data'])
            for target, aliases in ASSETS.items():
                matches = [name for name in names if name.lower() in aliases]
                if len(matches) > 1:
                    raise ValueError('ambiguous database names: ' + target)
                if matches:
                    write_private(candidate / target,
                                  read_input(settings['gui_data'], matches[0], settings['desktop_uid']), gid)
            (candidate / 'cache.db').symlink_to(Path(settings['data']) / 'cache.db')
            before = snapshot_digest(candidate)
            validate(candidate)
            if snapshot_digest(candidate) != before:
                raise ValueError('validator changed immutable snapshot inputs')
            if (state / 'intent').read_text().strip() != 'proxy':
                raise ValueError('proxy intent was revoked during validation')
            destination = snapshots / before
            if destination.exists():
                if snapshot_digest(destination) != before:
                    raise ValueError('stored snapshot was modified')
                shutil.rmtree(candidate)
            else:
                candidate.rename(destination)
            link = runtime / '.effective.new'
            link.unlink(missing_ok=True)
            link.symlink_to(destination)
            # Upgrade old deployments which used a real effective directory.
            if effective.exists() and not effective.is_symlink():
                shutil.rmtree(effective)
            link.replace(effective)
            atomic_authority(auth, before)
            atomic_authority(digest, before)
            for old in snapshots.iterdir():
                if old != destination:
                    shutil.rmtree(old)
        finally:
            if candidate.exists():
                shutil.rmtree(candidate)


def main():
    if sys.argv[1] == 'verify':
        verify(*sys.argv[2:])
        return
    if os.geteuid() != 0:
        raise ValueError('staging requires the root helper')
    settings = json.loads(Path(sys.argv[2]).read_text())
    settings['core_gid'] = grp.getgrnam('clash-core').gr_gid
    real, config = sys.argv[3:]
    log = Path(settings['runtime']) / 'validator.log'
    def validate(directory):
        # Parsing does not need TUN/mark capabilities. Keep the untrusted parser
        # entirely unprivileged, and keep diagnostics inaccessible to other UIDs.
        log.unlink(missing_ok=True)
        with log.open('wb') as stream:
            log.chmod(0o600)
            result = subprocess.run([
                settings['setpriv'], '--reuid=clash-core', '--regid=clash-core', '--clear-groups',
                '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all', '--no-new-privs',
                real, '-t', '-d', str(directory), '-f', str(directory / 'clash-verge.yaml')],
                stdout=stream, stderr=stream, timeout=60)
        if result.returncode:
            raise ValueError('core rejected configuration; inspect root-only validator.log and rerun clash-on')
    stage(settings, config, validate)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('verge-mihomo: ' + str(error), file=sys.stderr)
        sys.exit(78)
