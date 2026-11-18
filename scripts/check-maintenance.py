#!/usr/bin/env python3
"""Check source pins and compare installed or official update metadata."""
import argparse
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import sys
import tempfile
import urllib.request
import urllib.parse


SCHEMA = 1
KINDS = {
    'nix-flake-pin',
    'runtime-auto-update',
    'appimage-pin',
    'extension-auto-update',
    'flatpak-runtime',
    'docker-registry',
    'plugin',
    'cache-key',
}
REQUIRED = (
    'id', 'owner', 'kind', 'upstream', 'update_mode', 'current', 'signature',
    'fallback', 'max_patch_delay_days', 'review_command', 'pins',
)
ITEM_FIELDS = set(REQUIRED) | {'network_ref', 'flake', 'nix_release'}
CURRENT_FIELDS = {'version', 'summary'}
PIN_FIELDS = {'file', 'pattern'}
FLAKE_FIELDS = {'input', 'rev', 'version'}
NETWORK_DEFAULT_FIELDS = {'owner', 'kind', 'update_mode', 'signature', 'fallback', 'review_command', 'pins'}
TOP_FIELDS = {'schema', 'network_sources', 'network_defaults', 'items', 'lock_graph_sha256'}
ID_PATTERN = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
NIX_RELEASE_ASSIGNMENT = re.compile(r'^[ \t]*(version|hash)[ \t]*=[ \t]*"([^"\r\n]+)"[ \t]*;', re.MULTILINE)
INVENTORY = Path('maintenance/inventory.json')
FLAKE_LOCK = Path('flake.lock')


def add_error(errors, code, message, **context):
    error = {'code': code, 'message': message}
    error.update(context)
    errors.append(error)


def is_text(value):
    return isinstance(value, str) and bool(value.strip())


def repository_file(root, value, errors, code, label, item=None):
    """Resolve a non-empty repository-relative path, refusing escapes."""
    if not is_text(value):
        add_error(errors, code, f'{label} must be a non-empty repository-relative path', item=item)
        return None
    relative = Path(value)
    if relative.is_absolute() or '..' in relative.parts:
        add_error(errors, code, f'{label} escapes the repository: {value!r}', item=item)
        return None
    try:
        resolved = (root / relative).resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        add_error(errors, code, f'{label} escapes the repository: {value!r}', item=item)
        return None
    if not resolved.is_file():
        add_error(errors, code, f'{label} is not a file: {value!r}', item=item)
        return None
    return resolved


def read_json(path, errors, code, label, item=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError) as error:
        add_error(errors, code, f'cannot read {label}: {error}', item=item)
    except json.JSONDecodeError as error:
        add_error(errors, code, f'invalid JSON in {label}: {error}', item=item)
    return None


def check_current(item, item_id, errors):
    current = item.get('current')
    if not isinstance(current, dict):
        add_error(errors, 'current', f'{item_id}: current must be an object', item=item_id)
        return
    extra = set(current) - CURRENT_FIELDS
    if extra:
        add_error(errors, 'current', f'{item_id}: unknown current field(s): {", ".join(sorted(extra))}', item=item_id)
    if not is_text(current.get('summary')):
        add_error(errors, 'current', f'{item_id}: current.summary must be a non-empty string', item=item_id)
    if 'version' in current and not is_text(current['version']):
        add_error(errors, 'current', f'{item_id}: current.version must be a non-empty string', item=item_id)


def check_pins(root, item, item_id, errors, stats):
    pins = item.get('pins')
    if not isinstance(pins, list):
        add_error(errors, 'pins', f'{item_id}: pins must be a list', item=item_id)
        return
    stats['pins'] += len(pins)
    for pin in pins:
        if not isinstance(pin, dict) or set(pin) != PIN_FIELDS:
            add_error(errors, 'pins', f'{item_id}: each pin must contain exactly file and pattern', item=item_id)
            continue
        filename = pin['file']
        pattern = pin['pattern']
        if not is_text(pattern):
            add_error(errors, 'pin-pattern', f'{item_id}: pin pattern must be a non-empty string', item=item_id,
                      file=filename if is_text(filename) else None)
            continue
        path = repository_file(root, filename, errors, 'pin-file', 'pin file', item=item_id)
        if path is None:
            continue
        try:
            contents = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError) as error:
            add_error(errors, 'pin-read', f'{item_id}: cannot read pin file {filename!r}: {error}',
                      item=item_id, file=filename)
            continue
        if pattern not in contents:
            add_error(errors, 'pin-stale',
                      f'{item_id}: stale pin; {filename!r} no longer contains {pattern!r}',
                      item=item_id, file=filename, pattern=pattern)


def check_nix_release(root, item, item_id, errors, stats):
    release = item['nix_release']
    if item.get('kind') != 'appimage-pin' or not isinstance(release, dict) or set(release) != {'file', 'hash_count'}:
        add_error(errors, 'nix-release', f'{item_id}: appimage-pin nix_release needs file and hash_count', item=item_id)
        return
    hash_count = release['hash_count']
    if type(hash_count) is not int or hash_count < 1:
        add_error(errors, 'nix-release', f'{item_id}: hash_count must be a positive integer', item=item_id)
        return
    current = item.get('current')
    if isinstance(current, dict) and 'version' in current:
        add_error(errors, 'nix-release', f'{item_id}: read the version from nix_release.file', item=item_id)

    filename = release['file']
    path = repository_file(root, filename, errors, 'nix-release', 'nix_release.file', item=item_id)
    if path is None:
        return
    try:
        assignments = NIX_RELEASE_ASSIGNMENT.findall(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError) as error:
        add_error(errors, 'nix-release', f'{item_id}: cannot read {filename!r}: {error}', item=item_id)
        return
    versions = [value for key, value in assignments if key == 'version']
    hashes = [value for key, value in assignments if key == 'hash']
    if len(versions) != 1 or not re.fullmatch(r'[0-9][0-9A-Za-z.+_-]*', versions[0]):
        add_error(errors, 'nix-release', f'{item_id}: expected one literal version in {filename!r}', item=item_id)
    elif len(hashes) != hash_count or any(not re.fullmatch(r'sha256-[A-Za-z0-9+/]{43}=', value) for value in hashes):
        add_error(errors, 'nix-release',
                  f'{item_id}: expected {hash_count} literal SHA-256 SRI hash(es) in {filename!r}', item=item_id)
    else:
        stats['nix_releases'][item_id] = {'file': filename, 'version': versions[0], 'hashes': hashes}


def resolve_flake_input(lock, input_name, errors, item_id):
    if not isinstance(lock, dict):
        add_error(errors, 'flake-lock', f'{item_id}: flake.lock must contain an object', item=item_id)
        return None
    nodes = lock.get('nodes')
    if not isinstance(nodes, dict):
        add_error(errors, 'flake-lock', f'{item_id}: flake.lock has no nodes object', item=item_id)
        return None
    root = nodes.get('root')
    inputs = root.get('inputs') if isinstance(root, dict) else None
    if not isinstance(inputs, dict):
        add_error(errors, 'flake-lock', f'{item_id}: flake.lock has no root inputs', item=item_id)
        return None
    node_name = inputs.get(input_name)
    if not isinstance(node_name, str):
        add_error(errors, 'flake-input',
                  f'{item_id}: {input_name!r} is not a direct flake.lock root input', item=item_id)
        return None
    node = nodes.get(node_name)
    if not isinstance(node, dict):
        add_error(errors, 'flake-lock', f'{item_id}: flake.lock input node {node_name!r} is missing', item=item_id)
        return None
    locked = node.get('locked')
    if not isinstance(locked, dict):
        add_error(errors, 'flake-lock', f'{item_id}: flake.lock input {input_name!r} is not locked', item=item_id)
        return None
    return locked


def check_flake(item, item_id, errors, stats, lock_state):
    flake = item.get('flake')
    if not isinstance(flake, dict):
        add_error(errors, 'flake', f'{item_id}: flake must be an object', item=item_id)
        return
    extra = set(flake) - FLAKE_FIELDS
    if extra:
        add_error(errors, 'flake', f'{item_id}: unknown flake field(s): {", ".join(sorted(extra))}', item=item_id)
    input_name = flake.get('input')
    if not is_text(input_name):
        add_error(errors, 'flake', f'{item_id}: flake.input must be a non-empty string', item=item_id)
        return
    expected = {field: flake[field] for field in ('rev', 'version') if field in flake}
    if not expected:
        add_error(errors, 'flake', f'{item_id}: flake needs a rev or version to compare', item=item_id)
        return
    for field, value in expected.items():
        if not is_text(value):
            add_error(errors, 'flake', f'{item_id}: flake.{field} must be a non-empty string', item=item_id)
            return
    stats['flake_inputs'] += 1
    if lock_state['failed']:
        return
    if lock_state['data'] is None:
        path = repository_file(lock_state['root'], FLAKE_LOCK.as_posix(), errors, 'flake-lock', 'flake.lock', item=item_id)
        if path is None:
            lock_state['failed'] = True
            return
        lock_state['data'] = read_json(path, errors, 'flake-lock', 'flake.lock', item=item_id)
        if lock_state['data'] is None:
            lock_state['failed'] = True
            return
    locked = resolve_flake_input(lock_state['data'], input_name, errors, item_id)
    if locked is None:
        return
    for field, expected_value in expected.items():
        actual = locked.get(field)
        if actual is None:
            add_error(errors, 'flake-mismatch',
                      f'{item_id}: flake.lock input {input_name!r} has no {field} to compare',
                      item=item_id, field=field)
        elif actual != expected_value:
            add_error(errors, 'flake-mismatch',
                      f'{item_id}: flake.lock input {input_name!r} {field} is {actual!r}, ledger records {expected_value!r}',
                      item=item_id, field=field, expected=expected_value, actual=actual)


def check_item(root, item, index, errors, stats, seen, network_defaults):
    if not isinstance(item, dict):
        add_error(errors, 'item', f'item #{index} must be an object')
        return
    if 'network_ref' in item:
        item = {**network_defaults, **item}
    raw_id = item.get('id')
    item_id = raw_id if is_text(raw_id) else f'<item #{index}>'
    if not is_text(raw_id):
        add_error(errors, 'id', f'{item_id}: id must be a non-empty string', item=item_id)
    elif not ID_PATTERN.fullmatch(raw_id):
        add_error(errors, 'id', f'{item_id}: id must use lowercase letters, digits and single hyphens', item=item_id)
    elif raw_id in seen:
        add_error(errors, 'duplicate-id', f'duplicate item id: {raw_id}', item=item_id)
    else:
        seen.add(raw_id)
    extra = set(item) - ITEM_FIELDS
    if extra:
        add_error(errors, 'item', f'{item_id}: unknown item field(s): {", ".join(sorted(extra))}', item=item_id)
    missing = [field for field in REQUIRED if field not in item]
    if missing:
        add_error(errors, 'required', f'{item_id}: missing required field(s): {", ".join(missing)}', item=item_id)
    for field in ('owner', 'upstream', 'update_mode', 'signature', 'fallback', 'review_command'):
        if field in item and not is_text(item[field]):
            add_error(errors, 'required', f'{item_id}: {field} must be a non-empty string', item=item_id)
    kind = item.get('kind')
    if not is_text(kind) or kind not in KINDS:
        add_error(errors, 'kind', f'{item_id}: unknown kind {kind!r}', item=item_id)
    delay = item.get('max_patch_delay_days')
    if not isinstance(delay, int) or isinstance(delay, bool) or delay < 0:
        add_error(errors, 'delay', f'{item_id}: max_patch_delay_days must be a non-negative integer', item=item_id)
    check_current(item, item_id, errors)
    check_pins(root, item, item_id, errors, stats)
    if 'nix_release' in item:
        check_nix_release(root, item, item_id, errors, stats)
    network_ref = item.get('network_ref')
    flake = item.get('flake')
    if 'network_ref' in item:
        if not is_text(network_ref):
            add_error(errors, 'network-ref', f'{item_id}: network_ref must be a non-empty string', item=item_id)
        if kind != 'nix-flake-pin':
            add_error(errors, 'network-ref',
                      f'{item_id}: only nix-flake-pin items may use network_ref', item=item_id)
        current = item.get('current')
        if isinstance(current, dict) and 'version' in current:
            add_error(errors, 'network-duplicate-version',
                      f'{item_id}: network_ref items must not copy a version; sources.json owns it', item=item_id)
        if flake is not None:
            add_error(errors, 'network-duplicate-flake',
                      f'{item_id}: network_ref items must not duplicate flake input data', item=item_id)
        if item.get('pins'):
            add_error(errors, 'network-duplicate-pin',
                      f'{item_id}: network_ref items must not copy source data in pins', item=item_id)
        if is_text(network_ref):
            stats['network_refs'] += 1
            stats['network_ref_list'].append((item_id, network_ref))
    elif kind == 'nix-flake-pin' and flake is None:
        add_error(errors, 'flake', f'{item_id}: nix-flake-pin needs flake or network_ref', item=item_id)
    if flake is not None and kind != 'nix-flake-pin':
        add_error(errors, 'flake', f'{item_id}: flake is only valid on nix-flake-pin items', item=item_id)
    if flake is not None:
        check_flake(item, item_id, errors, stats, stats['lock_state'])


def check_network_refs(components, errors, stats):
    if components is None:
        return
    for item_id, network_ref in stats['network_ref_list']:
        if network_ref not in components:
            add_error(errors, 'network-ref-unknown',
                      f'{item_id}: sources.json has no component {network_ref!r}', item=item_id)
    counts = {name: 0 for name in components}
    for _, network_ref in stats['network_ref_list']:
        if network_ref in counts:
            counts[network_ref] += 1
    for name, count in sorted(counts.items()):
        if count == 0:
            add_error(errors, 'network-ref-missing',
                      f'sources.json component {name!r} is not referenced exactly once')
        elif count > 1:
            add_error(errors, 'network-ref-duplicate',
                      f'sources.json component {name!r} is referenced {count} times')


def lock_graph_digest(lock):
    return hashlib.sha256(json.dumps(lock.get('nodes'), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def check(root):
    root = root.resolve()
    errors = []
    stats = {
        'items': 0, 'pins': 0, 'network_components': 0,
        'network_refs': 0, 'network_ref_list': [], 'flake_inputs': 0, 'nix_releases': {},
        'lock_state': {'root': root, 'data': None, 'failed': False},
    }
    path = root / INVENTORY
    if not path.is_file():
        add_error(errors, 'inventory', f'missing {INVENTORY.as_posix()}')
        return result(errors, stats)
    inventory = read_json(path, errors, 'inventory', INVENTORY.as_posix())
    if not isinstance(inventory, dict):
        add_error(errors, 'inventory', 'inventory must be a JSON object')
        return result(errors, stats)
    schema = inventory.get('schema')
    if schema != SCHEMA:
        add_error(errors, 'schema', f'unsupported inventory schema {schema!r}; expected {SCHEMA}')
        return result(errors, stats)
    extra = set(inventory) - TOP_FIELDS
    if extra:
        add_error(errors, 'inventory', f'unknown top-level field(s): {", ".join(sorted(extra))}')
    network_defaults = inventory.get('network_defaults', {})
    if not isinstance(network_defaults, dict) or set(network_defaults) - NETWORK_DEFAULT_FIELDS:
        add_error(errors, 'inventory', 'network_defaults must contain only shared network item fields')
        network_defaults = {}
    if 'lock_graph_sha256' in inventory:
        lock = read_json(root / FLAKE_LOCK, errors, 'flake-lock', 'flake.lock')
        if isinstance(lock, dict):
            if lock_graph_digest(lock) != inventory['lock_graph_sha256']:
                add_error(errors, 'flake-graph', 'flake input graph changed; review all transitive inputs and update lock_graph_sha256')
    sources_path = repository_file(root, inventory.get('network_sources'), errors, 'network-sources',
                                   'network_sources', item=None)
    components = None
    if sources_path is not None:
        sources = read_json(sources_path, errors, 'network-sources', 'network_sources')
        if isinstance(sources, dict) and isinstance(sources.get('components'), dict) and sources['components']:
            components = sources['components']
            stats['network_components'] = len(components)
        elif isinstance(sources, dict):
            add_error(errors, 'network-sources', 'network_sources has no non-empty components object')
    items = inventory.get('items')
    if not isinstance(items, list) or not items:
        add_error(errors, 'items', 'inventory.items must be a non-empty list')
        items = []
    stats['items'] = len(items)
    seen = set()
    for index, item in enumerate(items):
        check_item(root, item, index, errors, stats, seen, network_defaults)
    check_network_refs(components, errors, stats)
    return result(errors, stats)


def result(errors, stats):
    public = {key: value for key, value in stats.items()
              if key not in ('network_ref_list', 'lock_state', 'nix_releases')}
    return {'ok': not errors, 'errors': errors, 'stats': public, 'nix_releases': stats['nix_releases']}


def replace_text(path, text):
    mode = stat.S_IMODE(path.stat().st_mode)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(text)
            handle.close()
            temporary.chmod(mode)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def write_pin_updates(root, original, updates):
    descriptor = os.open(root, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if any(path.read_text(encoding='utf-8') != text for path, text in original.items()):
            raise ValueError('maintenance inputs changed during evaluation, rerun the update')
        written = []
        try:
            for path, text in updates.items():
                replace_text(path, text)
                written.append(path)
        except OSError:
            for path in reversed(written):
                if path.read_text(encoding='utf-8') == updates[path]:
                    replace_text(path, original[path])
            raise
    finally:
        os.close(descriptor)


def update_pins(root, outcome, confirm=None):
    if any(error['code'] not in {'flake-graph', 'flake-mismatch'} for error in outcome['errors']):
        return outcome

    inventory_path = root / INVENTORY
    lock_path = root / FLAKE_LOCK
    original = {path: path.read_text(encoding='utf-8') for path in (inventory_path, lock_path)}
    inventory = json.loads(original[inventory_path])
    lock = json.loads(original[lock_path])
    sources_path = repository_file(root, inventory['network_sources'], outcome['errors'], 'network-sources',
                                   'network_sources')
    if sources_path is None:
        outcome['ok'] = False
        return outcome
    original[sources_path] = sources_path.read_text(encoding='utf-8')
    review = json.loads(original[sources_path])

    args = ['nix', 'eval', '--no-update-lock-file', '--json', '.#nixosConfigurations.lfour.config.my.proxy.upgradeSources']
    selected = json.loads(subprocess.check_output(args, cwd=root, text=True))
    plan = runpy.run_path(str(sources_path.with_name('upgrades.py')))['plan'](review, selected)

    changed_inputs = []
    errors = []
    for item in inventory['items']:
        if 'flake' not in item:
            continue
        pin = item['flake']
        locked = resolve_flake_input(lock, pin['input'], errors, item['id'])
        if locked is None:
            continue
        before = pin.copy()
        for field in pin.keys() & {'rev', 'version'}:
            value = locked.get(field)
            if not is_text(value):
                add_error(errors, 'flake-mismatch', f'{item["id"]}: flake.lock has no {field}', item=item['id'])
            else:
                pin[field] = value
        if before != pin:
            changed_inputs.append(pin['input'])
    if errors:
        outcome['errors'] = errors
        outcome['ok'] = False
        return outcome

    digest = lock_graph_digest(lock)
    graph_changed = 'lock_graph_sha256' in inventory and inventory['lock_graph_sha256'] != digest
    updates = {}
    if graph_changed:
        inventory['lock_graph_sha256'] = digest
    if changed_inputs or graph_changed:
        updates[inventory_path] = json.dumps(inventory, indent=2, ensure_ascii=False) + '\n'
    if plan['changed']:
        for name, record in review['sources'].items():
            record.update(selected[name])
        updates[sources_path] = json.dumps(review, indent=2, ensure_ascii=False) + '\n'
    diff = ''.join(line for path, text in updates.items() for line in difflib.unified_diff(
        original[path].splitlines(keepends=True), text.splitlines(keepends=True),
        fromfile=f'a/{path.relative_to(root)}', tofile=f'b/{path.relative_to(root)}',
    ))
    update = {
        'flake_inputs': sorted(set(changed_inputs)),
        'lock_graph_changed': graph_changed,
        'network_components': plan['changed'],
        'network_checks': plan['checks'],
        'files': [str(path.relative_to(root)) for path in updates],
        'diff': diff,
        'written': False,
        'declined': False,
    }
    if updates and confirm is not None:
        if confirm(diff):
            write_pin_updates(root, original, updates)
            outcome = check(root)
            update['written'] = True
        else:
            update['declined'] = True
    if not update['written']:
        outcome['errors'] = []
        outcome['ok'] = True
    outcome['pin_update'] = update
    return outcome


def confirm_pin_update(diff):
    print(diff, end='', file=sys.stderr)
    print('Update these JSON files? [y/N] ', end='', file=sys.stderr, flush=True)
    return sys.stdin.readline().strip().lower() in ('y', 'yes')


def command(args):
    try:
        value = subprocess.run(args, capture_output=True, text=True, timeout=20)
        if value.returncode == 0:
            return {"status": "observed", "value": value.stdout.strip()}
        return {"status": "unavailable", "exit_code": value.returncode}
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "unavailable"}


def observe(home):
    """Read installed metadata without opening credentials or changing apps."""
    data = {"firefox": {}, "flatpak": {}, "docker": {}, "plugins": {}}
    for index, path in enumerate(sorted((home / '.mozilla/firefox').glob('*/extensions.json'))):
        addons = json.loads(path.read_text()).get('addons', [])
        data['firefox'][str(index)] = sorted([
            {key: addon.get(key) for key in ('id', 'version', 'signedState', 'userPermissions')}
            for addon in addons if addon.get('type') == 'extension'
        ], key=lambda addon: addon['id'])
    for scope in ('user', 'system'):
        refs = command(['flatpak', 'list', '--' + scope, '--columns=ref,commit,origin'])
        data['flatpak'][scope] = refs
        if refs['status'] == 'observed':
            refs['permissions'] = {line.split()[0]: command([
                'flatpak', 'info', '--' + scope, '--show-permissions', line.split()[0]])
                for line in refs['value'].splitlines() if line.strip()}
    data['docker'] = command(['docker', 'image', 'ls', '--digests', '--no-trunc',
                              '--format', '{{json .}}'])
    data['docker']['signature_verification'] = 'not-performed'
    for label, base in [('dsh', home / '.dsh/profiles'),
                        ('noctalia', home / '.config/noctalia/plugins')]:
        entries = {}
        for path in sorted(base.glob('*/package.json')):
            package = json.loads(path.read_text())
            entries[path.parent.name] = {k: package.get(k) for k in ('version', 'dependencies')}
        for path in sorted(base.glob('*/.git')):
            entries.setdefault(path.parent.name, {})['commit'] = command([
                'git', '-C', str(path.parent), 'rev-parse', 'HEAD'])
        data['plugins'][label] = entries
    return data


def online_observations():
    observations = {}
    for slug in ('ublock-origin', 'kiss-translator'):
        url = 'https://addons.mozilla.org/api/v5/addons/addon/' + slug + '/'
        with urllib.request.urlopen(url, timeout=15) as response:
            if (urllib.parse.urlsplit(response.url).scheme != 'https'
                    or urllib.parse.urlsplit(response.url).hostname != 'addons.mozilla.org'):
                raise ValueError('unexpected metadata redirect')
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('addon metadata is too large')
        addon = json.loads(raw)
        version = addon['current_version']
        file = version['file']
        observations[slug] = {'version': version['version'],
                              'permissions': sorted(file.get('permissions', [])),
                              'hash': file['hash'], 'is_mozilla_signed': file.get('is_mozilla_signed')}
    return observations


def main(argv=None):
    parser = argparse.ArgumentParser(description='Validate the offline maintenance ledger.')
    parser.add_argument('--json', action='store_true', help='print machine-readable JSON')
    parser.add_argument('--online', action='store_true', help='observe official AMO metadata')
    parser.add_argument('--snapshot', type=Path, help='write private runtime observation JSON')
    parser.add_argument('--baseline', type=Path, help='compare observations with a reviewed snapshot')
    pin_modes = parser.add_mutually_exclusive_group()
    pin_modes.add_argument('--plan-pins', action='store_true', help='show pin diffs and affected network checks')
    pin_modes.add_argument('--sync-pins', action='store_true', help='show pin diffs and ask before writing')
    args = parser.parse_args(argv)
    if (args.plan_pins or args.sync_pins) and (args.online or args.snapshot or args.baseline):
        parser.error('pin updates and runtime observations are separate operations')
    root = Path(__file__).resolve().parent.parent
    outcome = check(root)
    if args.plan_pins or args.sync_pins:
        try:
            outcome = update_pins(root, outcome, confirm=confirm_pin_update if args.sync_pins else None)
        except (OSError, KeyError, ValueError, subprocess.CalledProcessError) as error:
            add_error(outcome['errors'], 'pin-update', str(error))
            outcome['ok'] = False
    if args.snapshot or args.baseline or args.online:
        try:
            observed = observe(Path.home()) if args.snapshot or args.baseline else {}
            if args.online:
                observed['amo'] = online_observations()
            if args.baseline:
                previous = json.loads(args.baseline.read_text())
                outcome['changed_sections'] = sorted(key for key in set(previous) | set(observed)
                                                     if previous.get(key) != observed.get(key))
                if outcome['changed_sections']:
                    add_error(outcome['errors'], 'runtime-drift', 'runtime metadata changed; review permissions and pins')
            if args.snapshot:
                descriptor = os.open(args.snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, 'w') as handle:
                    json.dump(observed, handle, indent=2, sort_keys=True)
            outcome['observations'] = observed
        except (OSError, ValueError, KeyError) as error:
            add_error(outcome['errors'], 'observation', str(error))
        outcome['ok'] = not outcome['errors']
    if args.json:
        print(json.dumps(outcome, indent=2, sort_keys=True))
    elif outcome['ok'] and 'pin_update' in outcome:
        update = outcome['pin_update']
        if update['declined']:
            print('Pin update cancelled')
        elif update['files']:
            if args.plan_pins:
                print(update['diff'], end='')
            print(f'{"Updated" if update["written"] else "Would update"}: ' + ', '.join(update['files']))
            print('Flake inputs: ' + (', '.join(update['flake_inputs']) or 'unchanged'))
            print('Network components: ' + (', '.join(update['network_components']) or 'unchanged'))
            if update['network_checks']:
                print('Network checks: ' + ' '.join(update['network_checks']))
        else:
            print('Flake and network pins are current')
    elif outcome['ok']:
        stats = outcome['stats']
        print(f'maintenance inventory: {stats["items"]} items, {stats["pins"]} pins, '
              f'{stats["network_refs"]}/{stats["network_components"]} network components, '
              f'{stats["flake_inputs"]} flake inputs')
    else:
        for error in outcome['errors']:
            print(f'check-maintenance: {error["message"]}', file=sys.stderr)
        print(f'check-maintenance: {len(outcome["errors"])} error(s)', file=sys.stderr)
    return 0 if outcome['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
