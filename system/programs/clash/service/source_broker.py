"""Root-only source registration and publication. Secrets never enter argv/logs."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
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
    (path / 'review.json').unlink(missing_ok=True)
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


def gui_choice(settings, root):
    """The GUI supplies only an identifier, never endpoints or configuration."""
    import importlib.util
    spec = importlib.util.spec_from_file_location('source_worker', settings['worker'])
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    try:
        fd = os.open(os.environ.get('LF_SOURCE_GUI_INDEX', settings['gui_index']),
                     os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
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
        return None
    matches = []
    for path in (root / 'sources').iterdir():
        if path.is_symlink() or not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', path.name):
            continue
        registry = json.loads(read_private(path / 'registry.json'))
        if registry.get('gui-profile') == identity:
            matches.append(path.name)
    if len(matches) > 1:
        raise ValueError('ambiguous GUI mapping')
    return source_path(root, matches[0]) if matches else None


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
    for name in ('current', 'approval.json', 'review.json', 'status.json'):
        (path / name).unlink(missing_ok=True)
    if not (root / 'selected').is_symlink():
        select(root, path)


def publish_fetch(settings, result, phase=None, reason=None, http_status=None, *, at=None):
    try:
        _publish_fetch(settings, result, phase, reason, http_status, at)
    except (OSError, ValueError, KeyError, TypeError):
        pass  # A missing diagnostic directory must not block the downloader.


def _publish_fetch(settings, result, phase, reason, http_status, at):
    """Separate download outcome from the runtime's import/health decision."""
    if not settings.get('stageSettings'):
        return
    state = Path(json.loads(Path(settings['stageSettings']).read_text())['state'])
    if not state.is_dir():
        return
    value = dict(at=time.time() if at is None else at, boot_id=(state / 'boot-id').read_text().strip(), result=result)
    if phase in ('fetch', 'parse', 'render', 'pin-addresses', 'worker'):
        value['phase'] = phase
    if reason in FETCH_REASONS:
        value['reason'] = reason
    if type(http_status) is int and 100 <= http_status <= 599:
        value['http_status'] = http_status
    atomic(state / 'source-fetch.json', (json.dumps(value) + '\n').encode(), mode=0o644)


def refresh(settings, root, *, downloaded=None):
    publish_fetch(settings, 'fetching')
    try:
        generation = refresh_generation(settings, root)
        at = time.time()
        if downloaded is not None:
            downloaded(generation, at)
    except SourceFailure as error:
        publish_fetch(settings, 'failed', error.phase, error.reason, error.http_status)
        raise
    except RequestChanged:
        publish_fetch(settings, 'discarded')
        raise
    except Exception:
        publish_fetch(settings, 'failed', 'worker', 'worker failure')
        raise
    publish_fetch(settings, 'downloaded', at=at)


@contextmanager
def download_window(settings):
    lock = settings.get('_fetch_lock')
    if lock is None:
        yield
        return
    # Never queue another download while holding the publication lock: the
    # first worker must reacquire that lock before it can finish.
    with (Path(settings['root']) / 'download.lock').open('a') as download:
        try:
            fcntl.flock(download, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise TimeoutError('another source download is in progress') from None
        fcntl.flock(lock, fcntl.LOCK_UN)
        try:
            yield
        finally:
            acquire(lock)


def authorized_sources(settings):
    """Registered sources allowed to drop unlisted insecure leaves on import.

    Missing, unreadable or invalid input authorizes nothing, so the import
    stays strict rather than silently sanitizing.
    """
    path = settings.get('insecure_sources')
    if not path:
        return set()
    try:
        lines = Path(path).read_text(encoding='utf-8').split('\n')
    except (OSError, UnicodeError):
        return set()
    values = {line.strip() for line in lines} - {''}
    return values if all(re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', value) for value in values) else set()


def renderer_settings(settings):
    return dict(forced_domains=settings['forced_domains'], probe_domains=settings.get('probe_domains', []),
                remote_only_catchall=settings.get('remote_only_catchall', False))


def refresh_generation(settings, root):
    raw = read_private(root / 'registry.json')
    registry = json.loads(raw)
    policy_bytes = Path(settings['policy']).read_bytes()
    routing = Path(settings['routing_state']).read_bytes() if settings.get('routing_state') else None
    request = json.dumps(dict(registry=registry, policy=json.loads(policy_bytes),
                              egress_id=json.loads(routing).get('egress_requested') if routing else None,
                              resolver=settings.get('resolver'),
                              sanitize_insecure=registry['id'] in authorized_sources(settings),
                              **renderer_settings(settings))).encode()
    argv = [settings['setpriv'], '--reuid=clash-fetch', '--regid=clash-fetch', '--clear-groups',
            '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all', '--no-new-privs',
            settings['python'], '-I', settings['worker']]
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (48 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    with download_window(settings):
        with tempfile.TemporaryFile() as output:
            process = subprocess.run(argv, input=request, stdout=output, stderr=subprocess.DEVNULL,
                                    timeout=50, env={'PATH': '', 'SSL_CERT_FILE': settings['ca']}, preexec_fn=limits)
            output.seek(0)
            data = output.read(48 * 1024 * 1024 + 1)
    check = settings.get('_before_publish')
    if check is not None:
        check()
    # Downloading grants no authority. Registration and selection can change
    # while the worker runs, so reject its result before touching generations.
    if read_private(root / 'registry.json') != raw or (settings.get('_fetch_lock') is not None
            and selected(Path(settings['root'])) != settings.get('_expected_selection', root)):
        raise ValueError('source registration or selection changed during download')
    if Path(settings['policy']).read_bytes() != policy_bytes or (routing is not None
            and Path(settings['routing_state']).read_bytes() != routing):
        raise ValueError('routing changed during download')
    if len(data) > 48 * 1024 * 1024:
        raise ValueError('source worker refused')
    result = json.loads(data)
    if isinstance(result, dict) and result.get('error') in ('fetch', 'parse', 'render', 'pin-addresses', 'worker'):
        reason = result.get('reason') if result.get('reason') in FETCH_REASONS else 'worker failure'
        raise SourceFailure('source ' + result['error'] + ' refused (' + reason + ')',
                            result['error'], reason, result.get('http_status'))
    if process.returncode or not isinstance(result, dict) or 'yaml' not in result:
        raise ValueError('source worker refused')
    config = result['yaml'].encode()
    if len(config) > 32 * 1024 * 1024:
        raise ValueError('rendered configuration size')
    dropped = result.get('dropped')
    if type(dropped) is not int or not 0 <= dropped <= 10000:
        raise ValueError('renderer metadata')
    manifest = dict(schema=1, source_id=registry['id'], revision=digest(raw),
                    egress_id=result.get('egress_id'),
                    responses=result['responses'], worker=digest(Path(settings['worker']).read_bytes()),
                    policy=digest(policy_bytes),
                    renderer_settings=digest(json.dumps(renderer_settings(settings), sort_keys=True).encode()),
                    yaml=digest(config), dropped=dropped, assets={})
    generations = root / 'generations'
    generations.mkdir(mode=0o700, exist_ok=True)
    # Bound retained downloaded data. Stage takes source.lock while copying.
    candidate = Path(tempfile.mkdtemp(prefix='.pending-', dir=generations))
    try:
        atomic(candidate / 'clash-verge.yaml', config)
        if 'source_data' in result:
            source_data = result['source_data'].encode()
            if len(source_data) > 32 * 1024 * 1024:
                raise ValueError('source data size')
            atomic(candidate / 'source-data.json', source_data)
            manifest['source_data'] = digest(source_data)
        for name, path in settings['assets'].items():
            content = Path(path).read_bytes()
            if len(content) > 256 * 1024 * 1024:
                raise ValueError('asset size')
            atomic(candidate / name, content)
            manifest['assets'][name] = digest(content)
        identity = digest(json.dumps(manifest, sort_keys=True).encode())
        atomic(candidate / 'source-manifest.json', json.dumps(manifest, sort_keys=True).encode())
        if check is not None:
            check()
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
        return destination
    finally:
        if candidate.exists():
            shutil.rmtree(candidate)


def local_source(settings, root, target):
    """Verify accepted source content before using its nodes or original rules."""
    stage_settings = json.loads(Path(settings['stageSettings']).read_text())
    spec = importlib.util.spec_from_file_location('source_stage', stage_settings['snapshot_tool'])
    stage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage)
    worker_spec = importlib.util.spec_from_file_location('local_source_worker', settings['worker'])
    worker = importlib.util.module_from_spec(worker_spec)
    worker_spec.loader.exec_module(worker)
    registry = read_private(root / 'registry.json')
    source_id = json.loads(registry)['id']
    revision = digest(registry)
    candidates = []
    for folder in (root / 'approved', root / 'approved/rules', root / 'approved/proxy-only'):
        try:
            record = json.loads(read_private(folder / 'settled.json'))
        except FileNotFoundError:
            continue
        identity = record.get('digest')
        if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{64}', identity):
            raise ValueError('invalid accepted source identity')
        source = folder / identity
        stage.snapshot_metadata(source)
        if source.is_symlink() or stage.snapshot_digest(source) != identity:
            raise ValueError('accepted source was modified')
        manifest = json.loads(read_private(source / 'source-manifest.json'))
        if manifest.get('source_id') != source_id:
            raise ValueError('accepted source identity differs')
        if manifest.get('revision') != revision:
            continue
        candidates.append((record['snapshot_at'], source, manifest))
    chosen = max(candidates, key=lambda item: ('source_data' in item[2], item[0]), default=None)
    if target == 'rules' and (chosen is None or 'source_data' not in chosen[2]):
        try:
            directory, manifest = stage.current_generation(root)
        except (OSError, ValueError):
            if chosen is None:
                raise
        else:
            if chosen is None or 'source_data' in manifest:
                chosen = (0, directory, manifest)
    if chosen is None:
        raise ValueError('proxy-only requires an accepted pinned local source')
    _, source, old_manifest = chosen
    if 'source_data' in old_manifest or (source / 'source-data.json').exists():
        source_data = read_private(source / 'source-data.json', 32 * 1024 * 1024)
        if old_manifest.get('source_data') != digest(source_data):
            raise ValueError('source content baseline differs')
        base = json.loads(source_data)
    else:
        # Recover original rules only when the injected prefix is unambiguous.
        document = worker.load(read_private(source / 'clash-verge.yaml', 32 * 1024 * 1024))
        render = renderer_settings(settings)
        if (document.get('lf-private-targets', {}).get('proxy-only') or render['remote_only_catchall']
                or old_manifest.get('renderer_settings') != digest(json.dumps(render, sort_keys=True).encode())):
            raise ValueError('original Rule data is unavailable')
        base = {key: document[key] for key in ('proxies', 'proxy-groups', 'rules', 'hosts') if key in document}
        base['proxy-groups'] = [group for group in base['proxy-groups'] if group.get('name') != 'lf-remote']
        prefix = ['IN-NAME,lf-strict,lf-remote'] + ['DOMAIN,' + name + ',lf-remote' for name in settings['forced_domains']]
        rules = base['rules']
        if rules[:len(prefix)] != prefix or len(rules) <= len(prefix):
            raise ValueError('unrecognized managed rule prefix')
        target = rules[-1].split(',')[-1]
        if rules[len(prefix)] in {'DOMAIN-SUFFIX,' + name + ',' + target for name in render['probe_domains']}:
            raise ValueError('ambiguous probe rule prefix')
        base['rules'] = rules[len(prefix):]
        source_data = json.dumps(base, sort_keys=True, ensure_ascii=False).encode()
    if not base.get('proxies') or any(not worker.public_address(node.get('server', '')) for node in base['proxies']):
        raise ValueError('source requires pinned public endpoints')
    return worker, base, source_data, source, old_manifest


def render_local(settings, root, target, previous):
    """Render a fixed policy from accepted local nodes without network access."""
    worker, base, source_data, source, old_manifest = local_source(settings, root, target)
    policy = Path(settings['policy']).read_bytes()
    routing = json.loads(Path(settings['routing_state']).read_text()) if settings.get('routing_state') else {}
    config = worker.render(base, {}, json.loads(policy), **renderer_settings(settings),
                           egress_id=routing.get('egress_requested'))
    egress_id = worker.selected_egress(worker.load(config))
    manifest = dict(old_manifest, schema=2, policy=digest(policy),
                    egress_id=egress_id,
                    yaml=digest(config), source_data=digest(source_data), worker=digest(Path(settings['worker']).read_bytes()),
                    renderer_settings=digest(json.dumps(renderer_settings(settings), sort_keys=True).encode()),
                    routing=target, local_routing=True, assets={})
    generations = root / 'generations'
    generations.mkdir(mode=0o700, exist_ok=True)
    candidate = Path(tempfile.mkdtemp(prefix='.routing-', dir=generations))
    try:
        atomic(candidate / 'clash-verge.yaml', config)
        atomic(candidate / 'source-data.json', source_data)
        for name in settings['assets']:
            data = read_private(source / name, 256 * 1024 * 1024)
            if old_manifest.get('assets', {}).get(name) != digest(data):
                raise ValueError('source asset mismatch')
            atomic(candidate / name, data)
            manifest['assets'][name] = digest(data)
        atomic(candidate / 'source-manifest.json', json.dumps(manifest, sort_keys=True).encode())
        identity = digest(json.dumps(manifest, sort_keys=True).encode())
        destination = generations / identity
        if destination.exists():
            if destination.is_symlink() or any(
                    read_private(destination / path.name, 256 * 1024 * 1024) != path.read_bytes()
                    for path in candidate.iterdir()):
                raise ValueError('local source generation mismatch')
        else:
            candidate.rename(destination)
        temporary = root / '.current'
        temporary.unlink(missing_ok=True)
        temporary.symlink_to(Path('generations') / identity)
        temporary.replace(root / 'current')
    finally:
        if candidate.exists():
            shutil.rmtree(candidate)
    return egress_id


FETCH_REASONS = frozenset(('dns lookup failed', 'certificate rejected', 'tls failed',
    'connection timeout', 'connection refused', 'connection reset', 'network unreachable',
    'http rejected', 'encoding unsupported', 'redirect refused', 'destination refused',
    'response limit', 'yaml invalid', 'configuration unsupported', 'node resolution failed', 'worker failure'))


class SourceFailure(ValueError):
    def __init__(self, message, phase='worker', reason='worker failure', http_status=None):
        super().__init__(message)
        self.phase, self.reason, self.http_status = phase, reason, http_status


class RequestChanged(ValueError):
    pass


def review_module(settings):
    spec = importlib.util.spec_from_file_location('source_review', settings['review'])
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def acquire(stream, operation=fcntl.LOCK_EX):
    deadline = time.monotonic() + 30
    while True:
        try:
            fcntl.flock(stream, operation | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError('source operation is busy') from None
            time.sleep(.05)


def confirm_add(settings, root, lock, identity):
    catalog = settings['catalog']
    values = definitions(read_private(catalog), identity)
    data = registration(values)
    reviewed = review_definitions(root, values)[0]
    fcntl.flock(lock, fcntl.LOCK_UN)
    print(json.dumps(reviewed), flush=True)
    print('Register and verify this source? Mode A will request proxy operation. [y/N] ',
          file=sys.stderr, end='', flush=True)
    try:
        reply = input()
    except EOFError:
        reply = ''
    if reply.strip().lower() not in ('y', 'yes'):
        raise DefinitionError('source registration cancelled')
    acquire(lock)
    if definitions(read_private(catalog), identity) != values:
        raise DefinitionError('source definition changed during confirmation')
    current = review_definitions(root, values)[0]['previous']
    if current != reviewed['previous']:
        raise DefinitionError('source registration changed during confirmation')
    if current != reviewed['digest']:
        register(root, data, reviewed['digest'], current)


def prepare_manual(settings, root, lock, identity, revision):
    manual, stage, stage_settings = refresh_context(settings)
    stage_settings.update(launcher=settings['launcher'], guard_service='clash-source-guard.service')
    state = Path(stage_settings['state'])
    mode = stage.read_record(state / 'state.json').get('mode')
    if mode not in ('direct', 'proxy'):
        raise DefinitionError('source download requires Mode A or B')
    path = source_path(root, identity)
    if revision is not None and digest(read_private(path / 'registry.json')) != revision:
        raise DefinitionError('source revision changed')
    refresh(dict(settings, _fetch_lock=lock, _expected_selection=selected(root)), path)
    candidate = stage.snapshot_digest((path / 'current').resolve(strict=True))
    select(root, path)
    return manual, stage, stage_settings, candidate, mode


def refresh_context(settings):
    stage_settings = json.loads(Path(settings['stageSettings']).read_text())
    manual = load_module('source_manual_refresh', settings['manual_refresh'])
    return manual, manual.module('source_stage', stage_settings['snapshot_tool']), stage_settings


def gui_binding(settings, root, manual, stage, stage_settings):
    path = gui_choice(settings, root)
    if path is None or not (root / 'selected').is_symlink():
        return None
    previous = selected(root)
    if path == previous:
        return None
    return dict(boot_id=stage.boot_id(Path(stage_settings['state'])), source=path.name,
                revision=digest(read_private(path / 'registry.json')), previous=previous.name,
                previous_revision=digest(read_private(previous / 'registry.json')),
                selection=manual.selection_stamp(root / 'selected'))


def request_gui(settings, root):
    manual, stage, stage_settings = refresh_context(settings)
    state = Path(stage_settings['state'])
    path = state / 'source-gui-request.json'
    with manual.queue_lock(state):
        try:
            binding = gui_binding(settings, root, manual, stage, stage_settings)
        except (OSError, ValueError, KeyError):
            path.unlink(missing_ok=True)
            raise
        if binding is None:
            path.unlink(missing_ok=True)
        elif stage.read_record(path) != binding:
            stage.atomic_record(path, binding, mode=0o600)
    return binding is not None


def automatic_refresh(settings, root, lock, *, requested=False, wait=time.sleep):
    manual, stage, stage_settings = refresh_context(settings)
    state = Path(stage_settings['state'])
    with manual.download_job(state) as acquired:
        if acquired:
            run_automatic(settings, root, lock, manual, stage, stage_settings, requested, wait)


def run_automatic(settings, root, lock, manual, stage, stage_settings, requested, wait):
    state = Path(stage_settings['state'])
    gui_request = state / 'source-gui-request.json'
    gui = stage.read_record(gui_request)
    if gui:
        try:
            current_gui = gui_binding(settings, root, manual, stage, stage_settings)
        except (OSError, ValueError, KeyError):
            current_gui = None
        if current_gui != gui:
            with manual.queue_lock(state):
                gui_request.unlink(missing_ok=True)
            gui = {}
    if not manual.schedule_download(stage_settings, stage, consume=True, request=requested):
        return
    previous = selected(root)
    path = source_path(root, gui['source']) if gui else previous
    selection = manual.selection_stamp(root / 'selected')
    registration = read_private(path / 'registry.json')
    network = manual.network_mode(stage_settings, stage)
    epoch = manual.authorization_epoch(state)
    stable = stage.read_record(state / 'source-refresh-stable.json')

    def check():
        try:
            unchanged = (manual.network_mode(stage_settings, stage) == network
                and manual.authorization_epoch(state) == epoch
                and stage.read_record(state / 'source-refresh-stable.json') == stable
                and manual.selection_stamp(root / 'selected') == selection
                and read_private(path / 'registry.json') == registration
                and stage.read_record(gui_request) == gui
                and (not gui or gui_binding(settings, root, manual, stage, stage_settings) == gui))
        except (OSError, ValueError, KeyError):
            unchanged = False
        if not unchanged:
            raise RequestChanged('automatic download request changed')

    def downloaded(generation, at):
        if gui:
            select(root, path)
        if network[0] == 'proxy':
            manual.queue_application(stage_settings, stage, stage.snapshot_digest(generation), at)

    try:
        for attempt in range(3):
            try:
                check()
                with manual.queue_lock(state):
                    if manual.download_gate(stage_settings, stage) is not None:
                        raise RequestChanged('download protection changed')
                refresh(dict(settings, _fetch_lock=lock, _expected_selection=previous,
                             _before_publish=check), path, downloaded=downloaded)
                return
            except RequestChanged:
                return
            except (OSError, ValueError, subprocess.SubprocessError):
                if attempt == 2:
                    raise
                fcntl.flock(lock, fcntl.LOCK_UN)
                wait(15)
                acquire(lock)
    finally:
        if gui:
            with manual.queue_lock(state):
                if stage.read_record(gui_request) == gui:
                    gui_request.unlink(missing_ok=True)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    automatic = sub.add_parser('automatic')
    automatic.add_argument('--request', action='store_true')
    sub.add_parser('diff', help='read-only comparison of local source snapshots')
    choose = sub.add_parser('use')
    choose.add_argument('id')
    choose.add_argument('--revision')
    sub.add_parser('add', help='review, register, fetch and verify a source from the private catalog').add_argument('id')
    sub.add_parser('list')
    sub.add_parser('gui-select')
    revoke = sub.add_parser('revoke')
    revoke.add_argument('id')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError('root required')
    settings = json.loads(Path(args.settings).read_text())
    root = Path(settings['root'])
    state_root(root)
    review = review_module(settings) if args.action == 'diff' else None
    with (root / 'source.lock').open('a') as lock:
        if args.action in ('diff', 'add', 'use'):
            acquire(lock)
        else:
            fcntl.flock(lock, fcntl.LOCK_EX)
        if args.action == 'diff':
            state = Path(json.loads(Path(settings['stageSettings']).read_text())['state'])
            with (state / 'authorization.lock').open('a') as authorization:
                acquire(authorization)
                try:
                    source = selected(root) if (root / 'selected').is_symlink() else root
                    report = review.compare(settings, source, read_private)
                except review.ReviewError as error:
                    print(str(error), file=sys.stderr)
                    raise SystemExit(1)
            print(json.dumps(report, ensure_ascii=False))
            return
        migrate(root)
        if args.action == 'list':
            active = selected(root).name if (root / 'selected').is_symlink() else None
            stage_settings = json.loads(Path(settings['stageSettings']).read_text())
            mode = None
            if stage_settings.get('routing_state'):
                mode = json.loads(Path(stage_settings['routing_state']).read_text()).get('routing_requested')
                if mode not in ('rules', 'proxy-only'):
                    raise ValueError('unknown routing selection')
            def settled(path):
                folder = path / 'approved' / mode if mode else path / 'approved'
                return json.loads(read_private(folder / 'settled.json'))['digest'] if (folder / 'settled.json').exists() else None
            print(json.dumps({'selected': active, 'sources': [
                {'id': path.name, 'revision': digest(read_private(path / 'registry.json')),
                 'settled_digest': settled(path)}
                for path in sorted((root / 'sources').iterdir()) if re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', path.name) and not path.is_symlink()]}))
            return
        if args.action == 'gui-select':
            if request_gui(settings, root):
                print('registered source queued for download')
            return
        if args.action == 'revoke':
            path = source_path(root, args.id)
            if selected(root) == path:
                raise ValueError('select another registered source before revocation')
            shutil.rmtree(path)
            print('inactive source revoked; other sources retained')
            return
        if args.action == 'refresh':
            settings['_fetch_lock'] = lock
            refresh(settings, selected(root))
            print('source candidate prepared; runtime validation remains required')
            return
        if args.action == 'automatic':
            automatic_refresh(settings, root, lock, requested=args.request)
            return
        if args.action in ('review', 'register'):
            values = definitions(read_private(args.file), args.source_id)
            if args.action == 'review':
                reviews = review_definitions(root, values)
                print(json.dumps(reviews[0] if len(reviews) == 1 else {'sources': reviews}))
            else:
                register(root, registration(values), args.digest, args.previous)
                print('source registered; run clash-source refresh before clash-on')
            return
        if args.action == 'add':
            confirm_add(settings, root, lock, args.id)
        manual, stage, stage_settings, candidate, mode = prepare_manual(settings, root, lock, args.id,
            args.revision if args.action == 'use' else None)
    result = manual.verify_selection(stage_settings, stage, candidate, mode, acquire)
    print(json.dumps(dict(requested_source=args.id, **result)))
    if result['result'] != 'accepted':
        raise SystemExit(1)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Source operation interrupted.', file=sys.stderr)
        raise SystemExit(130)
    except TimeoutError:
        print('Source operation is busy. Wait for the current operation, then retry.', file=sys.stderr)
        raise SystemExit(1)
    except SourceFailure as error:
        print(str(error) + '; prior selection and runtime retained', file=sys.stderr)
        raise SystemExit(1)
    except DefinitionError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        print('source operation refused; check private source definition and registration', file=sys.stderr)
        raise SystemExit(1)
