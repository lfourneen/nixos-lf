#!/usr/bin/env python3
"""Stage a root-owned, authorized core snapshot; verification needs no root.

Only config/geodata are immutable inputs. cache.db is an explicit symlink to
the service user's writable state and is excluded from the snapshot digest.
"""

import fcntl
import grp
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
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


def config_input(settings, config, data=None):
    relative = Path(os.path.abspath(config)).relative_to(settings['gui_data'])
    if data is None:
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


def snapshot_metadata(directory):
    resolved = Path(directory).resolve()
    owner = resolved.stat()
    if not stat.S_ISDIR(owner.st_mode) or owner.st_uid != os.geteuid() or owner.st_mode & 0o022:
        raise ValueError('snapshot directory ownership or permissions changed')
    result = {'directory': str(resolved),
              'directory_identity': [owner.st_dev, owner.st_ino, owner.st_uid, owner.st_mode]}
    for name in FILES:
        path = Path(directory) / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            result[name] = None
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError('snapshot permissions changed')
        result[name] = [info.st_dev, info.st_ino, info.st_uid, info.st_gid,
                        info.st_mode, info.st_size, info.st_ctime_ns, info.st_mtime_ns]
    return result


class FullVerificationRequired(ValueError):
    pass


def verify(directory, digest_file, auth_file, fast=False, cached_only=False):
    expected = Path(auth_file).read_text().strip()
    if not re.fullmatch('[0-9a-f]{64}', expected) or Path(digest_file).read_text().strip() != expected:
        raise ValueError('snapshot authorization is missing or inconsistent')
    cache = Path(auth_file).with_name('snapshot-verification.json')
    metadata = snapshot_metadata(directory)
    now = time.monotonic()
    try:
        boot = boot_id(Path(auth_file).parent)
    except (OSError, ValueError):
        # The helper's restricted /proc can omit boot_id. Full verification
        # remains available; cache reuse requires an observable boot identity.
        boot = None
    previous = {}
    try:
        info = cache.lstat()
        if stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o022:
            previous = json.loads(cache.read_text())
    except (OSError, ValueError):
        pass
    if fast and boot and previous.get('digest') == expected and previous.get('metadata') == metadata \
            and previous.get('boot') == boot and 0 <= now - previous.get('at', -100) < 60:
        return expected
    if cached_only:
        raise FullVerificationRequired('full snapshot verification required')
    if snapshot_digest(directory) != expected or snapshot_metadata(directory) != metadata:
        raise ValueError('authorized snapshot digest mismatch')
    # Only root-owned snapshots can use the periodic full-hash cache in the
    # production health loop. Unit fixtures use their current owner as well.
    record = dict(digest=expected, metadata=metadata, at=now, boot=boot,
                  full_verifications=previous.get('full_verifications', 0) + 1,
                  hashed_bytes=previous.get('hashed_bytes', 0) +
                      sum(value[5] for value in metadata.values() if isinstance(value, list) and len(value) == 8))
    temporary = cache.with_name(cache.name + '.new.' + str(os.getpid()))
    temporary.unlink(missing_ok=True)
    write_private(temporary, json.dumps(record).encode(), os.getegid())
    temporary.replace(cache)
    return expected


def atomic_authority(path, digest):
    temporary = path.with_name(path.name + '.new')
    temporary.unlink(missing_ok=True)
    write_private(temporary, (digest + '\n').encode(), os.getegid(), mode=0o600)
    temporary.replace(path)


def provider_cache(directory, data):
    # Provider contents are mutable core state, alongside cache.db. The root
    # snapshot keeps this exact link; the service user owns only its target.
    link = Path(directory) / 'proxies'
    target = Path(data) / 'proxies'
    if link.is_symlink():
        if link.readlink() != target:
            raise ValueError('unexpected provider cache link')
    elif link.exists():
        raise ValueError('provider cache must use the managed data directory')
    else:
        link.symlink_to(target)


class RefreshError(ValueError):
    """Only these fixed reasons may cross into public refresh state."""


REASONS = {'configuration invalid', 'input unreadable', 'validation timeout',
           'snapshot mismatch', 'blocked by audit', 'audit indeterminate',
           'unknown', 'node', 'tun', 'dns'}
PIN_TTL = 3600
HISTORY = 20
AUDIT_CATEGORIES = frozenset((
    'yaml_structure', 'private_server', 'certificate', 'empty_nodes', 'group_reference',
    'hosts_override', 'provider_change', 'rule_provider_change', 'sensitive_route',
    'protected_route', 'route_unknown', 'rule_volume', 'node_volume', 'host_concentration',
    'plaintext_credentials', 'broad_route', 'audit_error'))


def boot_id(state=Path('/run/proxy-mode')):
    path = Path(state) / 'boot-id'
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'r') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError('invalid boot identity permissions')
        value = stream.read(80).strip()
    if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value):
        raise ValueError('invalid boot identity')
    return value


def read_record(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_record(path, value, mode=0o644):
    temporary = path.with_name(path.name + '.new')
    temporary.unlink(missing_ok=True)
    write_private(temporary, (json.dumps(value, sort_keys=True) + '\n').encode(),
                  os.getegid(), mode=mode)
    temporary.replace(path)


def clean_findings(findings):
    """Only the fixed audit vocabulary and small counters may be published."""
    if not isinstance(findings, dict):
        return None
    result = {'verdict': findings.get('verdict')}
    if result['verdict'] not in ('pass', 'warn', 'deny', 'unknown'):
        return None
    for field in ('deny', 'warn', 'unknown'):
        counters = findings.get(field)
        if not isinstance(counters, dict):
            return None
        kept = {}
        for key, value in counters.items():
            if key not in AUDIT_CATEGORIES or type(value) is not int or not 0 <= value <= 10000:
                return None
            kept[str(key)] = value
        result[field] = kept
    return result


def audit(state, result, policy, digest='', previous_digest='', reason=None, snapshot_at=0,
          findings=None, event_at=None):
    if result not in ('accepted', 'verifying') and reason is None:
        reason = 'unknown'
    record = dict(at=time.time() if event_at is None else event_at, boot_id=boot_id(state), result=result, policy=policy,
                  digest=digest, previous_digest=previous_digest,
                  reason=reason if reason in REASONS or reason is None else 'unknown', snapshot_at=snapshot_at)
    findings = clean_findings(findings)
    if findings is not None:
        record['audit'] = findings
    previous = read_record(state / 'profile-refresh.json')
    if all(previous.get(key) == record[key] for key in ('at', 'boot_id', 'result', 'digest')):
        return previous
    last_accepted = previous.get('last_accepted_at')
    if type(last_accepted) not in (int, float) or not 0 <= last_accepted <= record['at']:
        last_accepted = 0
    if result == 'accepted':
        last_accepted = record['at']
    record['last_accepted_at'] = last_accepted
    history = previous.get('history')
    history = history if isinstance(history, list) else []
    entry = {key: record[key] for key in ('at', 'result', 'reason', 'digest', 'snapshot_at')}
    entry['policy'] = record['policy']
    if findings is not None:
        entry['audit'] = findings
    history = (history + [entry])[-HISTORY:]
    record['history'] = history
    atomic_record(state / 'profile-refresh.json', record)
    # Only fixed vocabulary and hashes reach the helper journal.
    print('verge-mihomo: profile refresh ' + result + ' · ' + (record['reason'] or 'ok') +
          ' · ' + digest[:12], file=sys.stderr)
    return record


def run_audit(settings, directory, baseline):
    """Run the offline content audit as the unprivileged core identity.

    A worker failure is never a pass: it is reported as audit_error so the
    enforce policy can refuse the candidate.
    """
    policy = settings.get('audit_policy', 'off')
    if policy == 'off':
        return None
    error = {'verdict': 'deny', 'deny': {'audit_error': 1}, 'warn': {},
             'unknown': {}}
    setpriv = settings.get('setpriv')
    prefix = [setpriv, '--reuid=clash-core', '--regid=clash-core', '--clear-groups',
              '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all',
              '--no-new-privs'] if setpriv else []
    argv = prefix + [settings.get('audit_python', ''), settings.get('audit_tool', ''),
                     settings.get('audit_settings', ''),
                     str(Path(directory) / 'clash-verge.yaml')]
    if not all((settings.get('audit_python'), settings.get('audit_tool'),
                settings.get('audit_settings'))):
        return error
    if baseline is not None:
        argv += ['--baseline', str(Path(baseline) / 'clash-verge.yaml')]
    timeout = min(10, settings.get('_prepare_deadline', time.monotonic() + 10) - time.monotonic())
    if timeout <= 0:
        return error
    try:
        result = subprocess.run(argv, capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return error
    if result.returncode != 0 or len(result.stdout) > 65536:
        return error
    try:
        value = json.loads(result.stdout)
    except ValueError:
        return error
    if not isinstance(value, dict) or value.get('verdict') not in (
            'pass', 'warn', 'deny', 'unknown'):
        return error
    for field in ('deny', 'warn', 'unknown'):
        counters = value.get(field)
        if not isinstance(counters, dict) or any(
                key not in AUDIT_CATEGORIES or type(count) is not int or not 0 <= count <= 10000
                for key, count in counters.items()):
            return error
    return value


def atomic_text(path, text, mode=0o644):
    temporary = path.with_name(path.name + '.new')
    temporary.unlink(missing_ok=True)
    write_private(temporary, text.encode(), os.getegid(), mode=mode)
    temporary.replace(path)


def publish_effective(runtime, state):
    """Publish the content digest of the running normalized YAML."""
    try:
        data = (Path(runtime) / 'effective' / 'clash-verge.yaml').read_bytes()
    except OSError:
        return
    atomic_text(Path(state) / 'effective-config-sha256', hashlib.sha256(data).hexdigest() + '\n')
    atomic_record(Path(state) / 'effective-config.json',
                  dict(boot_id=boot_id(state), yaml_digest=hashlib.sha256(data).hexdigest()))


def pin_candidate(state, digest):
    atomic_record(state / 'profile-refresh-pin.json',
                  dict(digest=digest, at=time.time(), ttl=PIN_TTL))


def pinned(state, digest):
    path = state / 'profile-refresh-pin.json'
    pin = read_record(path)
    if pin.get('digest') == digest and pin.get('ttl') == PIN_TTL \
            and isinstance(pin.get('at'), (int, float)) and 0 <= time.time() - pin['at'] < PIN_TTL:
        return True
    path.unlink(missing_ok=True)
    return False


def clear_refresh(state, runtime=None):
    state = Path(state)
    commit = state / 'profile-refresh-commit.json'
    if commit.exists():
        if (state / 'intent').read_text().strip() == 'proxy':
            if runtime is None:
                raise ValueError('snapshot recovery is required before reset')
            complete_commit(Path(runtime), state)
        else:
            commit.unlink()
    (state / 'profile-refresh-restart.json').unlink(missing_ok=True)
    for name in ('profile-refresh-pin.json', 'profile-refresh-verifying.json'):
        (Path(state) / name).unlink(missing_ok=True)
    # Do not leave a public verifying verdict with no pending transaction.
    path = Path(state) / 'profile-refresh.json'
    if read_record(path).get('result') == 'verifying':
        path.unlink(missing_ok=True)


def switch_snapshot(runtime, state, destination, digest):
    link = runtime / '.effective.new'
    link.unlink(missing_ok=True)
    link.symlink_to(destination)
    effective = runtime / 'effective'
    # Compatibility with the older, real-directory snapshot layout.
    if effective.exists() and not effective.is_symlink():
        effective.rename(runtime / 'snapshots' / ('legacy-' + str(time.time_ns())))
    link.replace(effective)
    atomic_authority(state / 'authorized-config', digest)
    atomic_authority(state / 'config-digest', digest)
    verify(effective, state / 'config-digest', state / 'authorized-config')
    publish_effective(runtime, state)


def prune_snapshots(snapshots, keep):
    for old in snapshots.iterdir():
        if old not in keep:
            shutil.rmtree(old)


def checked_snapshot(runtime, value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('invalid snapshot identity')
    directory = runtime / 'snapshots' / value
    if directory.is_symlink():
        raise ValueError('snapshot directory is a symlink')
    snapshot_metadata(directory)
    if snapshot_digest(directory) != value:
        raise ValueError('snapshot mismatch')
    return directory


def remember_settled(runtime, state, value, snapshot_at):
    directory = checked_snapshot(runtime, value)
    # Separate root-owned authority for an older settled snapshot; the main
    # authority always describes the currently selected effective snapshot.
    authority = directory / 'settled-authorization'
    atomic_authority(authority, value)
    verify(directory, authority, authority)
    atomic_record(state / 'profile-refresh-settled.json',
                  dict(boot_id=boot_id(state), digest=value, snapshot_at=snapshot_at), mode=0o600)


def settled_snapshot(runtime, state, current):
    path = state / 'profile-refresh-settled.json'
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    if info is not None and (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                             or info.st_mode & 0o077):
        raise ValueError('invalid settled authority permissions')
    record = read_record(path)
    if info is not None and not record:
        raise ValueError('invalid settled authority')
    if not record:
        # Migrate an already healthy, accepted deployment, not an unobserved
        # first import or another candidate from the probation window.
        previous = read_record(state / 'profile-refresh.json')
        if previous.get('boot_id') == boot_id(state) and previous.get('result') == 'accepted' \
                and previous.get('digest') == current and current:
            remember_settled(runtime, state, current, previous['snapshot_at'])
            record = read_record(path)
        else:
            return None, '', 0
    if record.get('boot_id') != boot_id(state):
        raise ValueError('settled authority belongs to a different boot')
    directory = checked_snapshot(runtime, record.get('digest'))
    authority = directory / 'settled-authorization'
    verify(directory, authority, authority)
    return directory, record['digest'], record['snapshot_at']


def complete_commit(runtime, state):
    """Replay an intent journal after interruption between atomic replacements."""
    path = state / 'profile-refresh-commit.json'
    if not path.exists():
        return
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('invalid snapshot transaction permissions')
    commit = read_record(path)
    if commit.get('boot_id') != boot_id(state) or commit.get('kind') not in ('import', 'rollback'):
        raise ValueError('invalid snapshot transaction')
    if (state / 'intent').read_text().strip() != 'proxy':
        raise ValueError('proxy intent was revoked during commit')
    directory = checked_snapshot(runtime, commit.get('target'))
    record = commit['record']
    switch_snapshot(runtime, state, directory, commit['target'])
    pending = state / 'profile-refresh-verifying.json'
    if commit['kind'] == 'import':
        atomic_record(pending, record, mode=0o600)
        audit(state, 'verifying', record['policy'], record['digest'], record['previous_digest'],
              snapshot_at=record['snapshot_at'], findings=record.get('audit'), event_at=record['at'])
    else:
        if not pinned(state, record['digest']):
            pin_candidate(state, record['digest'])
        audit(state, 'rolled_back', record['policy'], record['digest'], record['previous_digest'],
              record['rollback_reason'], record['previous_snapshot_at'], record.get('audit'),
              event_at=commit['at'])
        atomic_record(state / 'profile-refresh-restart.json',
                      dict(boot_id=boot_id(state), digest=commit['target']), mode=0o600)
        pending.unlink(missing_ok=True)
    path.unlink()


def commit_snapshot(runtime, state, target, record, kind='import'):
    checked_snapshot(runtime, target)
    atomic_record(state / 'profile-refresh-commit.json',
                  dict(boot_id=boot_id(state), kind=kind, target=target, record=record, at=time.time()), mode=0o600)
    complete_commit(runtime, state)


def stage(settings, config, validate):
    runtime, state = Path(settings['runtime']), Path(settings['state'])
    gid = settings['core_gid']
    policy = settings.get('refresh_policy', 'strict')
    if policy not in ('strict', 'auto'):
        raise ValueError('unknown refresh policy')
    snapshots, effective = runtime / 'snapshots', runtime / 'effective'
    auth, digest = state / 'authorized-config', state / 'config-digest'
    pending_path = state / 'profile-refresh-verifying.json'
    private_dir(snapshots, gid)
    with (state / 'authorization.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (state / 'intent').read_text().strip() != 'proxy':
            raise ValueError('start Clash with clash-on; proxy intent is required')
        complete_commit(runtime, state)
        window = state / 'authorize-window'
        authorized = False
        try:
            age = time.time() - window.stat().st_mtime
            authorized = 0 <= age <= 300
        except FileNotFoundError:
            pass
        window.unlink(missing_ok=True)
        if authorized:
            clear_refresh(state, runtime)

        previous, previous_digest, previous_at = None, '', 0
        # Never fall back to an unverified/tampered snapshot. Explicit grants
        # can repair an absent authority, just as in strict mode before auto.
        if not authorized or (auth.exists() and digest.exists()):
            if effective.exists() or auth.exists() or digest.exists():
                previous_digest = verify(effective, digest, auth)
                previous = effective.resolve()
                previous_at = read_record(state / 'profile-refresh.json').get('snapshot_at') or previous.stat().st_mtime
        settled, settled_digest, settled_at = settled_snapshot(runtime, state, previous_digest)
        candidate = None
        candidate_digest = ''
        validated = False
        restarting = False
        findings = None
        phase = 'input unreadable'
        try:
            # Keep a fingerprint even when UTF-8/controller normalization fails.
            relative = Path(os.path.abspath(config)).relative_to(settings['gui_data'])
            raw = read_input(settings['gui_data'], relative, settings['desktop_uid'])
            candidate_digest = hashlib.sha256(raw).hexdigest()
            if not authorized and policy == 'auto' and previous is not None \
                    and read_record(state / 'profile-refresh-pin.json').get('digest') == candidate_digest \
                    and pinned(state, candidate_digest):
                provider_cache(effective, settings['data'])
                publish_effective(runtime, state)
                return
            phase = 'configuration invalid'
            candidate_config = config_input(settings, config, raw)
            if not authorized and previous is not None:
                if candidate_config == (effective / 'clash-verge.yaml').read_bytes():
                    restarting = True
                    provider_cache(effective, settings['data'])
                    validate(effective)
                    verify(effective, digest, auth)
                    publish_effective(runtime, state)
                    return
                if policy == 'strict':
                    audit(state, 'refused', policy, candidate_digest, previous_digest,
                          snapshot_at=previous_at)
                    raise RefreshError('configuration changed; run clash-on to authorize it')
            if not authorized and policy == 'strict':
                raise RefreshError('snapshot authorization is missing')

            candidate = Path(tempfile.mkdtemp(prefix='.candidate-', dir=snapshots))
            private_dir(candidate, gid)
            write_private(candidate / 'clash-verge.yaml', candidate_config, gid)
            phase = 'input unreadable'
            names = os.listdir(settings['gui_data'])
            for target, aliases in ASSETS.items():
                matches = [name for name in names if name.lower() in aliases]
                if len(matches) > 1:
                    raise ValueError('ambiguous database names: ' + target)
                if matches:
                    write_private(candidate / target,
                                  read_input(settings['gui_data'], matches[0], settings['desktop_uid']), gid)
            (candidate / 'cache.db').symlink_to(Path(settings['data']) / 'cache.db')
            provider_cache(candidate, settings['data'])
            candidate_digest = snapshot_digest(candidate)
            if not authorized and policy == 'auto' and pinned(state, candidate_digest):
                # Preserve rolled_back and the original sanitized rejection on
                # GUI/helper restarts; do not extend the pin's TTL.
                verify(effective, digest, auth)
                provider_cache(effective, settings['data'])
                publish_effective(runtime, state)
                return
            phase = 'configuration invalid'
            validate(candidate)
            phase = 'snapshot mismatch'
            if snapshot_digest(candidate) != candidate_digest:
                raise RefreshError('snapshot mismatch')
            destination = snapshots / candidate_digest
            if destination.exists() and snapshot_digest(destination) != candidate_digest:
                raise RefreshError('snapshot mismatch')
            findings = run_audit(settings, candidate, settled)
            if findings is not None and settings.get('audit_policy') == 'enforce':
                deny = findings.get('deny') or {}
                unknown = findings.get('unknown') or {}
                if 'audit_error' in deny or 'route_unknown' in unknown:
                    raise RefreshError('audit indeterminate')
                if deny:
                    raise RefreshError('blocked by audit')
            if snapshot_digest(candidate) != candidate_digest:
                raise RefreshError('snapshot mismatch')
            validated = True
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            reason = ('validation timeout' if isinstance(error, subprocess.TimeoutExpired) else
                      str(error) if isinstance(error, RefreshError) and str(error) in REASONS else
                      'input unreadable' if isinstance(error, OSError) else phase)
            if policy == 'strict' or restarting:
                if read_record(state / 'profile-refresh.json').get('result') != 'refused':
                    audit(state, 'refused', policy, candidate_digest, previous_digest,
                          reason, previous_at, findings)
                raise
            if previous is None:
                audit(state, 'refused', policy, candidate_digest, reason=reason)
                raise RefreshError(reason) from None
            verify(effective, digest, auth)
            provider_cache(effective, settings['data'])
            if candidate_digest:
                pin_candidate(state, candidate_digest)
            audit(state, 'fallback', policy, candidate_digest, previous_digest, reason, previous_at,
                  findings)
            return
        finally:
            # Successful candidates are retained below; failed/unchanged ones
            # are removed even when parsing timed out or a pin short-circuited.
            if candidate is not None and not validated:
                shutil.rmtree(candidate)
                candidate = None

        try:
            if (state / 'intent').read_text().strip() != 'proxy':
                raise ValueError('proxy intent was revoked during validation')
            destination = snapshots / candidate_digest
            if destination.exists():
                if snapshot_digest(destination) != candidate_digest:
                    raise ValueError('stored snapshot was modified')
                provider_cache(destination, settings['data'])
            else:
                candidate.rename(destination)
            stamp = time.time()
            transaction = dict(at=stamp, boot_id=boot_id(state), policy=policy, digest=candidate_digest,
                               previous_digest=settled_digest, snapshot_at=stamp, audit=findings,
                               previous=str(settled) if settled else None,
                               previous_snapshot_at=settled_at, monotonic_seconds=time.monotonic())
            commit_snapshot(runtime, state, candidate_digest, transaction)
            prune_snapshots(snapshots, {destination, settled})
        finally:
            if candidate is not None and candidate.exists():
                shutil.rmtree(candidate)


def verify_refresh(settings, restart=None):
    """Settle or roll back one boot-scoped transaction, under operator + auth locks."""
    runtime, state = Path(settings['runtime']), Path(settings['state'])
    pending_path = state / 'profile-refresh-verifying.json'
    with (state / 'operator.lock').open('a') as operator:
        fcntl.flock(operator, fcntl.LOCK_EX)
        with (state / 'authorization.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            complete_commit(runtime, state)
            recovery = state / 'profile-refresh-restart.json'
            record = read_record(pending_path)
            if recovery.exists():
                record = None
            elif not record or record.get('boot_id') != boot_id(state):
                return
            if record and time.monotonic() - record['monotonic_seconds'] < 60:
                return
            if (state / 'intent').read_text().strip() != 'proxy':
                clear_refresh(state, runtime)
                return
            if not record:
                # The authority restore committed, but a previous restart was
                # interrupted or failed. Retain this marker until recovery.
                recovery_record = read_record(recovery)
                if recovery_record.get('boot_id') != boot_id(state):
                    raise ValueError('invalid core recovery record')
                if verify(runtime / 'effective', state / 'config-digest', state / 'authorized-config') != recovery_record.get('digest'):
                    raise ValueError('core recovery authority changed')
            else:
                verify_pending_refresh(settings, record)
                if not recovery.exists():
                    return
        if restart is None:
            restart_core(settings)
        else:
            restart()
        with (state / 'authorization.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            recovery.unlink(missing_ok=True)


def verify_pending_refresh(settings, record):
    runtime, state = Path(settings['runtime']), Path(settings['state'])
    pending_path = state / 'profile-refresh-verifying.json'
    current = verify(runtime / 'effective', state / 'config-digest', state / 'authorized-config')
    if current != record['digest']:
        raise ValueError('pending snapshot authority changed')
    health = read_record(state / 'health.json')
    mode = read_record(state / 'state.json')
    healthy = (mode.get('mode') == 'proxy' and health.get('ok') is True
               and health.get('boot_id') == boot_id(state) and health.get('digest') == current
               and record['at'] <= health.get('at', 0) <= time.time()
               and time.time() - health['at'] <= 60)
    previous = Path(record['previous']) if record.get('previous') else None
    if healthy:
        remember_settled(runtime, state, current, record['snapshot_at'])
        audit(state, 'accepted', record['policy'], current, record['previous_digest'],
              snapshot_at=record['snapshot_at'], findings=record.get('audit'))
        pending_path.unlink()
        (state / 'profile-refresh-pin.json').unlink(missing_ok=True)
        prune_snapshots(runtime / 'snapshots', {(runtime / 'effective').resolve(), previous})
        return
    reason = mode.get('reason', '').split(':')[0] or health.get('reason')
    reason = ('tun' if reason in ('tun', 'no-tun', 'core-gone', 'config') else
              'dns' if reason == 'dns' else 'node')
    pin_candidate(state, current)
    if previous is None:
        # No history exists on first import: C continues to protect the
        # host. There is no safe snapshot we could invent here.
        audit(state, 'refused', record['policy'], current, reason=reason,
              snapshot_at=record['snapshot_at'])
        pending_path.unlink()
        return
    snapshots = runtime / 'snapshots'
    if previous.parent != snapshots or previous.name != record['previous_digest']:
        raise ValueError('invalid rollback snapshot location')
    if snapshot_digest(previous) != record['previous_digest']:
        raise ValueError('rollback snapshot digest mismatch')
    snapshot_metadata(previous)
    authority = previous / 'settled-authorization'
    verify(previous, authority, authority)
    transaction = dict(record, rollback_reason=reason)
    commit_snapshot(runtime, state, record['previous_digest'], transaction, kind='rollback')


def service_request(settings, method, endpoint, payload=None, timeout=15):
    connection = http.client.HTTPConnection('localhost', timeout=timeout)
    connection.sock = socket.socket(socket.AF_UNIX)
    connection.sock.settimeout(timeout)
    try:
        connection.sock.connect(str(Path(settings['runtime']) / 'service.sock'))
        connection.request(method, endpoint, json.dumps(payload) if payload is not None else None,
                           headers={'Content-Type': 'application/json', 'X-IPC-Magic':
                               'A thing of beauty is a joy for ever. Its loveliness increases; it will never pass into nothingness.'})
        response = connection.getresponse()
        data = json.loads(response.read(65536))
        if response.status != 200 or not isinstance(data, dict) or data.get('code') != 0:
            raise ValueError('managed helper rejected core recovery')
        return data.get('data') or {}
    finally:
        connection.close()


def restart_core(settings):
    subprocess.run([settings['systemctl'], 'restart', 'clash-verge.service'], check=True, timeout=30)
    deadline = time.monotonic() + 18
    # A missing or stale desired-state must not leave only the helper running.
    # The IPC request uses fixed root-supplied paths, never a GUI-supplied core.
    while True:
        try:
            status = service_request(settings, 'GET', '/status', timeout=1)
            break
        except (OSError, ValueError, http.client.HTTPException):
            if time.monotonic() >= deadline:
                raise ValueError('managed helper recovery timed out') from None
            time.sleep(0.05)
    pid = status.get('core_pid')
    if type(pid) is not int or pid <= 0:
        payload = dict(core_config=dict(core_path=settings['launcher'], config_dir=settings['gui_data'],
                       config_path=str(Path(settings['gui_data']) / 'clash-verge.yaml'),
                       core_ipc_path='/run/clash-verge-rev/core/mihomo.sock'),
                       log_config=dict(directory='/var/lib/clash-verge-service/logs',
                                       max_log_size=10485760, max_log_files=8))
        service_request(settings, 'POST', '/clash/start', payload)
        pid = service_request(settings, 'GET', '/status').get('core_pid')
    members = Path('/sys/fs/cgroup/system.slice/clash-verge.service/cgroup.procs').read_text().split()
    if type(pid) is not int or str(pid) not in members:
        raise ValueError('recovered core identity is unavailable')
    with socket.socket(socket.AF_UNIX) as probe:
        probe.settimeout(1)
        probe.connect(str(Path(settings['runtime']) / 'core/mihomo.sock'))


def main():
    if sys.argv[1] == 'verify':
        verify(*sys.argv[2:5], fast='--fast' in sys.argv[5:])
        return
    if os.geteuid() != 0:
        raise ValueError('staging requires the root helper')
    if sys.argv[1] == 'initialize-boot':
        state = Path(sys.argv[2])
        state.mkdir(parents=True, exist_ok=True)
        info = state.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('invalid boot identity directory')
        value = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value):
            raise ValueError('invalid kernel boot identity')
        atomic_text(state / 'boot-id', value + '\n')
        boot_id(state)
        return
    settings = json.loads(Path(sys.argv[2]).read_text())
    settings['core_gid'] = grp.getgrnam('clash-core').gr_gid
    if sys.argv[1] == 'verify-refresh':
        settings['launcher'] = sys.argv[3]
        verify_refresh(settings)
        return
    if sys.argv[1] == 'clear-refresh':
        with (Path(settings['state']) / 'authorization.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            clear_refresh(settings['state'], settings['runtime'])
        return
    real, config = sys.argv[3:]
    # The helper's fixed readiness deadline is fifteen seconds. Reserve time
    # for fallback/core startup instead of allowing a candidate parser to run
    # beyond that deadline and strand a healthy previous snapshot.
    settings['_prepare_deadline'] = time.monotonic() + 11
    log = Path(settings['runtime']) / 'validator.log'
    def validate(directory):
        # Parsing does not need TUN/mark capabilities. Keep the untrusted parser
        # entirely unprivileged, and keep diagnostics inaccessible to other UIDs.
        log.unlink(missing_ok=True)
        remaining = settings['_prepare_deadline'] - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired('managed validation', 11)
        with log.open('wb') as stream:
            log.chmod(0o600)
            result = subprocess.run([
                settings['setpriv'], '--reuid=clash-core', '--regid=clash-core', '--clear-groups',
                '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all', '--no-new-privs',
                real, '-t', '-d', str(directory), '-f', str(directory / 'clash-verge.yaml')],
                stdout=stream, stderr=stream, timeout=remaining)
        if result.returncode:
            raise RefreshError('configuration invalid')
    stage(settings, config, validate)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('verge-mihomo: ' + str(error), file=sys.stderr)
        sys.exit(78)
