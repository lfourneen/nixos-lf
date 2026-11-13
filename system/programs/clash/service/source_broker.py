"""Root-only source registration and publication. Secrets never enter argv/logs."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import stat
import subprocess
import sys
import tempfile
import time


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_private(path, maximum=1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > maximum:
            raise ValueError('root-private regular file required')
        data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError('input size')
        return data


def atomic(path, data, mode=0o600):
    fd, name = tempfile.mkstemp(prefix='.new-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        Path(name).unlink(missing_ok=True)


def canonical(data):
    value = json.loads(data)
    if not isinstance(value, dict) or set(value) - {'id', 'subscription', 'providers', 'gui-profile'}:
        raise ValueError('registry fields')
    if not isinstance(value.get('id'), str) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', value['id']):
        raise ValueError('source id')
    if 'gui-profile' in value and (not isinstance(value['gui-profile'], str) or
            not re.fullmatch('[A-Za-z0-9_-]{1,128}', value['gui-profile'])):
        raise ValueError('GUI profile uid')
    if not isinstance(value.get('providers', {}), dict):
        raise ValueError('providers')
    specs = [value['subscription'], *value.get('providers', {}).values()]
    for spec in specs:
        if not isinstance(spec, dict) or set(spec) - {'url', 'headers', 'redirects', 'user-agent'}:
            raise ValueError('endpoint fields')
        if not isinstance(spec.get('url'), str) or not spec['url'].startswith('https://'):
            raise ValueError('https endpoint required')
        if not isinstance(spec.get('redirects', []), list) or len(spec.get('redirects', [])) > 4:
            raise ValueError('redirect registration')
        agent = spec.get('user-agent', 'clash.meta')
        if not isinstance(agent, str) or not 1 <= len(agent) <= 256 or any(not 32 <= ord(c) < 127 for c in agent):
            raise ValueError('user agent')
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


class DefinitionError(ValueError):
    pass


def definitions(data, identity=None):
    """Read a private catalog without changing any per-source revision format."""
    def unique_fields(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise DefinitionError('duplicate JSON field in source definition')
            value[key] = item
        return value
    value = json.loads(data, object_pairs_hook=unique_fields)
    entries = value['sources'] if isinstance(value, dict) and set(value) == {'sources'} else [value]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 256:
        raise DefinitionError('source catalog requires 1 to 256 definitions')
    result, mappings = {}, set()
    for entry in entries:
        raw = canonical(json.dumps(entry))
        source_id = entry['id']
        if source_id in result:
            raise DefinitionError('duplicate source id in source catalog')
        mapping = entry.get('gui-profile')
        if mapping and mapping in mappings:
            raise DefinitionError('duplicate GUI mapping in source catalog')
        if mapping:
            mappings.add(mapping)
        result[source_id] = raw
    if identity is not None:
        if identity not in result:
            raise DefinitionError('requested source id is absent from definition')
        return {identity: result[identity]}
    return result


def review_definitions(root, values):
    result = []
    for identity, data in values.items():
        path = root / 'sources' / identity
        if path.is_symlink():
            raise ValueError('source directory')
        current = digest(read_private(path / 'registry.json')) if (path / 'registry.json').exists() else 'none'
        result.append({'source_id': identity, 'digest': digest(data), 'previous': current})
    return result


def registration(values):
    if len(values) != 1:
        raise DefinitionError('source catalog contains multiple entries; register with --id SOURCE_ID')
    return next(iter(values.values()))


def state_root(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('invalid source state directory')


def source_path(root, identity):
    if not isinstance(identity, str) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', identity):
        raise ValueError('source id')
    path = root / 'sources' / identity
    if path.is_symlink() or not path.is_dir():
        raise ValueError('unregistered source')
    if json.loads(read_private(path / 'registry.json'))['id'] != identity:
        raise ValueError('source identity')
    return path


def selected(root):
    target = (root / 'selected').resolve(strict=True)
    if target.parent != root / 'sources' or source_path(root, target.name) != target:
        raise ValueError('invalid source selection')
    return target


def select(root, path):
    temporary = root / '.selected'
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(Path('sources') / path.name)
    temporary.replace(root / 'selected')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def migrate(root):
    """Copy the legacy single source before atomically selecting its new home."""
    (root / 'sources').mkdir(mode=0o700, exist_ok=True)
    if (root / 'selected').is_symlink() or not (root / 'registry.json').exists():
        return
    identity = json.loads(read_private(root / 'registry.json'))['id']
    if not isinstance(identity, str) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', identity):
        raise ValueError('legacy source id')
    destination = root / 'sources' / identity
    if not destination.exists():
        pending = Path(tempfile.mkdtemp(prefix='.migrate-', dir=root / 'sources'))
        try:
            for name in ('registry.json', 'generations', 'approval.json', 'status.json', 'approved'):
                old = root / name
                if old.is_dir() and not old.is_symlink():
                    shutil.copytree(old, pending / name)
                elif old.is_file() and not old.is_symlink():
                    shutil.copy2(old, pending / name)
            if (root / 'current').is_symlink():
                old = (root / 'current').resolve(strict=True)
                if old.parent != root / 'generations' or not re.fullmatch('[0-9a-f]{64}', old.name):
                    raise ValueError('legacy generation')
                (pending / 'current').symlink_to(Path('generations') / old.name)
            pending.rename(destination)
        finally:
            if pending.exists():
                shutil.rmtree(pending)
    select(root, source_path(root, identity))


def use(settings, root, identity, revision=None):
    path = source_path(root, identity)
    if revision is not None and digest(read_private(path / 'registry.json')) != revision:
        raise ValueError('source revision changed')
    if (root / 'selected').is_symlink() and selected(root) == path:
        return False
    # All fallible fetching precedes the one selection commit. Runtime audit
    # and health still gate activation, with the previous runtime as fallback.
    refresh(settings, path)
    select(root, path)
    return True


def gui_choice(settings, root):
    """The GUI supplies only an identifier, never endpoints or configuration."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('source_worker', settings['worker'])
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    fd = os.open(settings['gui_index'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_uid != settings['desktop_uid'] or before.st_size > 1024 * 1024:
            raise ValueError('GUI index permissions or size')
        data = stream.read(1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
    if len(data) > 1024 * 1024 or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError('GUI index changed')
    identity = worker.load(data).get('current')
    if not isinstance(identity, str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}', identity):
        return False
    matches = []
    for path in (root / 'sources').iterdir():
        if path.is_symlink() or not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', path.name):
            continue
        registry = json.loads(read_private(path / 'registry.json'))
        if registry.get('gui-profile') == identity:
            matches.append(path.name)
    if len(matches) > 1:
        raise ValueError('ambiguous GUI mapping')
    return use(settings, root, matches[0]) if matches else False


def register(root, data, expected, previous):
    identity = json.loads(data)['id']
    path = root / 'sources' / identity
    if path.is_symlink():
        raise ValueError('source directory')
    current = digest(read_private(path / 'registry.json')) if (path / 'registry.json').exists() else 'none'
    if expected != digest(data) or previous != current:
        raise ValueError('source approval does not match current revision and reviewed candidate')
    mapping = json.loads(data).get('gui-profile')
    for other in (root / 'sources').iterdir():
        if other != path and not other.is_symlink() and re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', other.name):
            if mapping and json.loads(read_private(other / 'registry.json')).get('gui-profile') == mapping:
                raise ValueError('GUI mapping already registered')
    state_root(path)
    atomic(path / 'registry.json', data)
    # A crash after registration cannot authorize the old download: stage also
    # binds its manifest to this exact registry revision.
    for name in ('current', 'approval.json', 'status.json'):
        (path / name).unlink(missing_ok=True)
    if not (root / 'selected').is_symlink():
        select(root, path)


def refresh(settings, root):
    raw = read_private(root / 'registry.json')
    registry = json.loads(raw)
    request = json.dumps(dict(registry=registry, policy=json.loads(Path(settings['policy']).read_text()),
                              forced_domains=settings['forced_domains'],
                              probe_domains=settings.get('probe_domains', []),
                              remote_only_catchall=settings.get('remote_only_catchall', False))).encode()
    argv = [settings['setpriv'], '--reuid=clash-fetch', '--regid=clash-fetch', '--clear-groups',
            '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all', '--no-new-privs',
            settings['python'], '-I', settings['worker']]
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (48 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    with tempfile.TemporaryFile() as output:
        process = subprocess.run(argv, input=request, stdout=output, stderr=subprocess.DEVNULL,
                                timeout=50, env={'PATH': '', 'SSL_CERT_FILE': settings['ca']}, preexec_fn=limits)
        output.seek(0)
        data = output.read(48 * 1024 * 1024 + 1)
    if len(data) > 48 * 1024 * 1024:
        raise ValueError('source worker refused')
    result = json.loads(data)
    if isinstance(result, dict) and result.get('error') in ('fetch', 'parse', 'render', 'pin-addresses', 'worker'):
        raise SourceFailure('source ' + result['error'] + ' refused')
    if process.returncode or not isinstance(result, dict) or 'yaml' not in result:
        raise ValueError('source worker refused')
    config = result['yaml'].encode()
    if len(config) > 32 * 1024 * 1024:
        raise ValueError('rendered configuration size')
    dropped = result.get('dropped')
    if type(dropped) is not int or not 0 <= dropped <= 10000:
        raise ValueError('renderer metadata')
    manifest = dict(schema=1, source_id=registry['id'], revision=digest(raw),
                    responses=result['responses'], worker=digest(Path(settings['worker']).read_bytes()),
                    policy=digest(Path(settings['policy']).read_bytes()),
                    renderer_settings=digest(json.dumps({'forced_domains': settings['forced_domains'],
                        'probe_domains': settings.get('probe_domains', []),
                        'remote_only_catchall': settings.get('remote_only_catchall', False)}, sort_keys=True).encode()),
                    yaml=digest(config), dropped=dropped, assets={})
    generations = root / 'generations'
    generations.mkdir(mode=0o700, exist_ok=True)
    # Bound retained downloaded data. Stage takes source.lock while copying.
    candidate = Path(tempfile.mkdtemp(prefix='.pending-', dir=generations))
    try:
        atomic(candidate / 'clash-verge.yaml', config)
        for name, path in settings['assets'].items():
            content = Path(path).read_bytes()
            if len(content) > 256 * 1024 * 1024:
                raise ValueError('asset size')
            atomic(candidate / name, content)
            manifest['assets'][name] = digest(content)
        identity = digest(json.dumps(manifest, sort_keys=True).encode())
        atomic(candidate / 'source-manifest.json', json.dumps(manifest, sort_keys=True).encode())
        destination = generations / identity
        if destination.exists():
            shutil.rmtree(candidate)
        else:
            candidate.rename(destination)
        temporary = root / '.current'
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(Path('generations') / identity)
        temporary.replace(root / 'current')
        # Crash-safe publication precedes cleanup; retain one previous source.
        old = sorted((p for p in generations.iterdir() if p != destination and not p.is_symlink()),
                     key=lambda p: p.stat().st_mtime, reverse=True)
        for path in old[1:]:
            shutil.rmtree(path)
        atomic(root / 'status.json', json.dumps(dict(source_id=registry['id'], revision=digest(raw),
               generation=identity, at=time.time(), state='fetched')).encode(), 0o644)
    finally:
        if candidate.exists():
            shutil.rmtree(candidate)


class SourceFailure(ValueError):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('settings')
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('review', 'register'):
        p = sub.add_parser(action)
        p.add_argument('file')
        p.add_argument('--id', dest='source_id', help='select one entry from a private source catalog')
        if action == 'register':
            p.add_argument('--digest', required=True)
            p.add_argument('--previous', required=True, help='current digest, or none for first registration')
    sub.add_parser('refresh')
    choose = sub.add_parser('use')
    choose.add_argument('id')
    choose.add_argument('--revision')
    sub.add_parser('list')
    sub.add_parser('gui-select')
    revoke = sub.add_parser('revoke')
    revoke.add_argument('id')
    approval = sub.add_parser('approve')
    approval.add_argument('--digest', required=True)
    approval.add_argument('--baseline', required=True)
    approval.add_argument('--categories', nargs='+', required=True,
                          choices=['node_identity', 'node_authentication', 'node_transport', 'sensitive_route'])
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError('root required')
    settings = json.loads(Path(args.settings).read_text())
    root = Path(settings['root'])
    state_root(root)
    with (root / 'source.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        migrate(root)
        if args.action == 'list':
            active = selected(root).name if (root / 'selected').is_symlink() else None
            print(json.dumps({'selected': active, 'sources': [
                {'id': path.name, 'revision': digest(read_private(path / 'registry.json')),
                 'settled_digest': json.loads(read_private(path / 'approved/settled.json'))['digest']
                    if (path / 'approved/settled.json').exists() else None}
                for path in sorted((root / 'sources').iterdir()) if re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', path.name) and not path.is_symlink()]}))
            return
        if args.action in ('use', 'gui-select'):
            changed = use(settings, root, args.id, args.revision) if args.action == 'use' else gui_choice(settings, root)
            if changed and settings.get('systemctl'):
                subprocess.run([settings['systemctl'], 'start', '--no-block', 'clash-source-refresh.service'], check=True, timeout=10)
            print('registered source selected; runtime validation remains required' if changed else 'source selection unchanged')
            return
        if args.action == 'revoke':
            path = source_path(root, args.id)
            if selected(root) == path:
                raise ValueError('select another registered source before revocation')
            shutil.rmtree(path)
            print('inactive source revoked; other sources retained')
            return
        if args.action in ('approve', 'refresh'):
            root = selected(root)
        if args.action == 'approve':
            stage = json.loads(Path(settings['stageSettings']).read_text())
            runtime_state = Path(stage['state'])
            with (runtime_state / 'authorization.lock').open('a') as authorization:
                fcntl.flock(authorization, fcntl.LOCK_EX)
                last = json.loads((runtime_state / 'profile-refresh.json').read_text())
                settled = json.loads(read_private(root / 'approved' / 'settled.json'))
                if (not re.fullmatch('[0-9a-f]{64}', args.digest) or last.get('digest') != args.digest or
                        last.get('result') not in ('fallback', 'refused') or settled.get('digest') != args.baseline):
                    raise ValueError('approval candidate or baseline changed')
                value = dict(candidate=args.digest, baseline=args.baseline,
                             revision=digest(read_private(root / 'registry.json')),
                             policy=digest(Path(stage['managed_policy']).read_bytes()),
                             categories=sorted(set(args.categories)), expires=time.time() + 3600)
                atomic(root / 'approval.json', json.dumps(value).encode())
            print('exact semantic difference approved for one hour; refresh to retry')
            return
        if args.action == 'refresh':
            refresh(settings, root)
            print('source candidate prepared; runtime validation remains required')
            return
        values = definitions(read_private(args.file), args.source_id)
        if args.action == 'review':
            reviews = review_definitions(root, values)
            print(json.dumps(reviews[0] if len(reviews) == 1 else {'sources': reviews}))
            return
        data = registration(values)
        register(root, data, args.digest, args.previous)
        print('source registered; run clash-source refresh before clash-on')


if __name__ == '__main__':
    try:
        main()
    except SourceFailure as error:
        print(str(error) + '; prior selection and runtime retained', file=sys.stderr)
        raise SystemExit(1)
    except DefinitionError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        print('source operation refused; check private source definition and registration', file=sys.stderr)
        raise SystemExit(1)
