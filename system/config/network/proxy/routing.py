"""Root-selected immutable routing policies and a verified outer-transport boundary."""
from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

MODES = ('rules', 'proxy-only')
FIELDS = ('routing_requested', 'routing_applied', 'routing_epoch', 'routing_policy_digest', 'routing_phase',
          'egress_requested', 'egress_epoch')


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def read(path):
    return json.loads(Path(path).read_text())


def sha(data):
    return hashlib.sha256(data).hexdigest()


def ip_tunnel(settings):
    return module(settings['ip_tunnel']['module'], 'routing_ip_tunnel')


def atomic(path, value, mode=0o644):
    path = Path(path)
    descriptor, name = tempfile.mkstemp(prefix='.routing-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(value, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        Path(name).replace(path)
    finally:
        Path(name).unlink(missing_ok=True)


def safe_file(path, private=False):
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & (0o077 if private else 0o022):
        raise ValueError('unsafe routing authority')
    return Path(path)


class StalePolicy(ValueError):
    """A boot-scoped selection cannot be used with the current artifacts."""


def selection(settings):
    state = Path(settings['state'])
    record = read(safe_file(state / 'routing.json'))
    record.setdefault('egress_requested', None)
    record.setdefault('egress_epoch', 0)
    target = record.get('routing_requested')
    active = state / 'routing-active'
    if (record.get('schema') != 1 or target not in MODES
            or record.get('routing_applied') not in (*MODES, None)
            or type(record.get('routing_epoch')) is not int or record['routing_epoch'] < 1
            or record.get('egress_requested') is not None and not re.fullmatch('[a-f0-9]{64}', str(record['egress_requested']))
            or type(record.get('egress_epoch', 0)) is not int or record.get('egress_epoch', 0) < 0
            or record.get('routing_phase') not in ('preparing', 'verifying', 'ready', 'failed')
            or record.get('boot_id') != (state / 'boot-id').read_text().strip()
            or not re.fullmatch('[a-f0-9]{64}', str(record.get('routing_policy_digest')))):
        raise ValueError('routing authority is missing or inconsistent')
    # Only artifacts from this generation can be used for forwarding. The
    # record still carries the epoch for explicit recovery if an old artifact
    # was garbage collected or the symlink update was interrupted.
    stale = (not active.is_symlink() or active.readlink() != Path(settings['artifacts'][target])
             or record['routing_policy_digest'] != sha(Path(settings['policies'][target]).read_bytes()))
    return record, stale


def binding(settings):
    record, stale = selection(settings)
    if stale:
        raise StalePolicy('routing policy changed; run clash-rules to recover')
    return record


def activate(settings, target):
    state = Path(settings['state'])
    temporary = state / '.routing-active'
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(settings['artifacts'][target])
    temporary.replace(state / 'routing-active')


def initialize(settings, mode=None):
    if not settings.get('enabled'):
        return
    state = Path(settings['state'])
    state.mkdir(mode=0o755, exist_ok=True)
    if not (state / 'boot-id').exists():
        (state / 'boot-id').write_text(Path('/proc/sys/kernel/random/boot_id').read_text())
    epoch, egress_epoch = 1, 0
    if (state / 'routing.json').exists() or (state / 'routing-active').is_symlink():
        record, stale = selection(settings)
        # In A there is no live B authority to carry across the switch. Rebind
        # the inactive selection to the new generation's safe default.
        if not stale or mode != 'direct':
            return
        epoch = record['routing_epoch'] + 1
        egress_epoch = record['egress_epoch'] + 1
    activate(settings, 'rules')
    atomic(state / 'routing.json', dict(schema=1, boot_id=(state / 'boot-id').read_text().strip(),
        routing_requested='rules', routing_applied=None, routing_epoch=epoch,
        routing_policy_digest=sha(Path(settings['policies']['rules']).read_bytes()), routing_phase='verifying',
        egress_requested=None, egress_epoch=egress_epoch))


@contextmanager
def transition_locks(settings):
    """The operator lock is held by proxy-control; acquire source, auth, policy."""
    source = read(settings['source_settings'])
    with ExitStack() as stack:
        for path in (Path(source['root']) / 'source.lock',
                     Path(settings['state']) / 'authorization.lock', Path(settings['state']) / 'lock'):
            stream = stack.enter_context(path.open('a'))
            fcntl.flock(stream, fcntl.LOCK_EX)
        yield source


def stop_ip_tunnel(settings):
    cleanup = settings['ip_tunnel_cleanup']
    if Path(cleanup['ownership']).exists():
        module(cleanup['module'], 'routing_ip_tunnel').stop(cleanup)


def begin(settings, target, egress_id=None, prepared=None):
    if target not in MODES:
        raise ValueError('invalid routing target')
    if egress_id is not None and (target != 'proxy-only' or not re.fullmatch('[a-f0-9]{64}', egress_id)):
        raise ValueError('invalid egress selection')
    with transition_locks(settings) as source_settings:
        current, stale = selection(settings)
        if stale and target != 'rules':
            raise StalePolicy('routing policy changed; run clash-rules to recover')
        stage = module(settings['stage_tool'], 'routing_stage')
        if prepared is not None:
            if stale or target != current['routing_requested'] or egress_id is not None:
                raise ValueError('prepared source routing changed')
            if target == 'proxy-only':
                broker = module(settings['source_tool'], 'routing_source')
                source = broker.selected(Path(source_settings['root']))
                broker.local_source(source_settings, source, target)
            stage.prepared_source(read(settings['stage_settings']), prepared)
        state = Path(settings['state'])
        (state / 'routing-failure.json').unlink(missing_ok=True)
        if (state / 'intent').read_text().strip() != 'proxy':
            raise ValueError('proxy intent required')
        stage.clear_refresh(state, settings['runtime'], keep_health_pin=True, discard_commit=True)
        record = dict(current, routing_requested=target, routing_epoch=current['routing_epoch'] + 1,
            egress_requested=None if stale else egress_id or current['egress_requested'],
            egress_epoch=current['egress_epoch'] + int(stale or target == 'proxy-only'),
            routing_policy_digest=sha(Path(settings['policies'][target]).read_bytes()), routing_phase='preparing')
        # The core has stopped and C is installed before the immutable policy changes.
        activate(settings, target)
        atomic(state / 'routing.json', record)
        for name in ('authorize-window', 'authorized-config', 'config-digest', 'effective-config-sha256',
                     'effective-config.json', 'health.json', 'source-refresh-stable.json', 'profile-refresh-settled.json'):
            (state / name).unlink(missing_ok=True)
        enforce(settings, 'blocked')
        if target == 'rules' or not settings.get('ip_tunnel'):
            stop_ip_tunnel(settings)
        if prepared is None:
            broker = module(settings['source_tool'], 'routing_source')
            source = broker.selected(Path(source_settings['root']))
            selected = broker.render_local(source_settings, source, target, current['routing_requested'])
            if target == 'proxy-only':
                if not isinstance(selected, str) or not re.fullmatch('[a-f0-9]{64}', selected):
                    raise ValueError('selected egress unavailable')
                record['egress_requested'] = selected
        if target == 'proxy-only' and settings.get('ip_tunnel'):
            ip_tunnel(settings).start(settings['ip_tunnel'])
        if prepared is not None:
            stage.atomic_text(state / 'authorize-window', prepared, mode=0o600)
        else:
            (state / 'authorize-window').touch(mode=0o600)
            (state / 'source-refresh-pending').touch(mode=0o600)
        atomic(state / 'routing.json', dict(record, routing_phase='verifying'))


def phase(settings, value):
    state = Path(settings['state'])
    with (state / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        atomic(state / 'routing.json', dict(binding(settings), routing_phase=value))


def applied(settings):
    """Called under the policy lock after a complete, epoch-bound health round."""
    state = Path(settings['state'])
    record, proof = binding(settings), read(state / 'health.json')
    authorization = module(settings['authorization_tool'], 'routing_authorization')
    if (record['routing_phase'] not in ('verifying', 'ready')
            or not authorization.full_routing_proof(proof, record['routing_requested'], settings.get('ip_tunnel'))
            or proof.get('boot_id') != record['boot_id']
            or any(proof.get(key) != record[key] for key in ('routing_epoch', 'routing_policy_digest'))
            or record['routing_requested'] == 'proxy-only' and any(proof.get(key) != record.get(key)
                for key in ('egress_requested', 'egress_epoch'))
            or proof.get('digest') != (state / 'config-digest').read_text().strip()):
        raise ValueError('routing requires a complete current health proof')
    if record['routing_applied'] == record['routing_requested'] and record['routing_phase'] == 'ready':
        return
    atomic(state / 'routing.json', dict(record, routing_applied=record['routing_requested'], routing_phase='ready'))


def endpoints(settings, record):
    """Only the currently authorized, policy-bound snapshot supplies node tuples."""
    state = Path(settings['state'])
    if not (state / 'authorized-config').exists():
        return []
    stage = module(settings['stage_tool'], 'routing_snapshot')
    directory = Path(settings['runtime']) / 'effective'
    stage.verify(directory, state / 'config-digest', state / 'authorized-config', fast=True)
    manifest = read(directory / 'source-manifest.json')
    if manifest.get('policy') != record['routing_policy_digest']:
        return []
    import yaml
    document = yaml.safe_load((directory / 'clash-verge.yaml').read_text())
    result = set()
    for node in document.get('proxies') or []:
        address = ipaddress.ip_address(node['server'])
        port = node['port']
        if not address.is_global or address.is_multicast or type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('invalid pinned outer transport')
        protocols = ('udp',) if node['type'] in ('tuic', 'hysteria2') else ('tcp', 'udp')
        result.update((address.version, str(address), protocol, port) for protocol in protocols)
    return sorted(result)


def tunnel_rules(settings, nodes, uplinks):
    tunnel = ip_tunnel(settings)
    policy = settings['ip_tunnel']['policy']
    values = tunnel.addresses(policy)
    address = values['endpointAddress']
    field = 'ip' if address.version == 4 else 'ip6'
    wg = json.dumps(policy['device'])
    port, listen_port, outer_mark = policy['endpointPort'], policy['listenPort'], policy['outerMark']
    outer = f'{field} daddr {address} udp sport {listen_port} udp dport {port} meta mark {outer_mark}'
    incoming = (f' iifname {uplinks} ct direction reply ct state established,related '
                f'ct original {field} daddr {address} ct protocol udp '
                f'ct original proto-src {listen_port} ct original proto-dst {port} return\n')
    outgoing = f' oifname {uplinks} {outer} return\n'
    exclusions = {
        4: settings['icmp_excluded']['ipv4'] + ['192.0.0.0/24', '198.18.0.0/15', settings['fake'], '224.0.0.0/3'],
        6: settings['icmp_excluded']['ipv6'] + ['::/96', '::ffff:0:0/96', 'ff00::/8',
            '64:ff9b::/96', '64:ff9b:1::/48', '2001::/32', '2002::/16'] + settings['nat64'],
    }
    for family, field, echo in [(4, 'ip', 'icmp'), (6, 'ip6', 'icmpv6')]:
        client = values[f'clientIpv{family}']
        incoming += (f' iifname {wg} ct direction reply ct state established,related '
                     f'ct reply {field} daddr {client} return\n')
        excluded = ', '.join(dict.fromkeys(exclusions[family]))
        outgoing += (f' oifname {wg} meta iif 0 {field} saddr {client} {field} daddr != {{ {excluded} }} '
                     f'{echo} type echo-request return\n')
    relay = values['relayAddress']
    eligible = {(relay.version, str(relay), protocol, policy['relayPort']) for protocol in ('tcp', 'udp')}
    return incoming, outgoing, sorted(set(nodes) & eligible), f' oifname {wg}', f' iifname {wg}'


def firewall(settings, mode):
    result = 'destroy table inet lf_routing\ntable inet lf_routing {\n'
    if mode == 'direct':
        return result + '}\n'
    try:
        record = binding(settings)
    except (OSError, ValueError, KeyError, TypeError):
        if mode != 'blocked':
            raise
        record = None
    strict = record is None or record['routing_requested'] == 'proxy-only'
    if not strict:
        return result + '}\n'
    nodes = endpoints(settings, record) if record is not None else []
    t, dev = settings['topology'], json.dumps(settings['interface'])
    uplinks = '{ ' + ', '.join(map(json.dumps, settings['uplinks'])) + ' }'
    guests = '{ ' + ', '.join(map(json.dumps, settings['guest_interfaces'])) + ' }'
    tunnel_in, tunnel_out, to_tunnel, from_tunnel = '', '', '', ''
    if record is not None and settings.get('ip_tunnel'):
        tunnel_in, tunnel_out, nodes, to_tunnel, from_tunnel = tunnel_rules(settings, nodes, uplinks)
    for family in (4, 6):
        elements = [f'{address} . {protocol} . {port}' for version, address, protocol, port in nodes if version == family]
        result += f'set nodes{family} {{ type ipv{family}_addr . inet_proto . inet_service;'
        if elements:
            result += ' elements = { ' + ', '.join(elements) + ' };'
        result += ' }\n'
    result += f'''chain lf_routing_input {{ type filter hook input priority -4; policy accept;
 iifname "lo" return
 iifname {dev} ct direction reply ct state established,related ct mark & 0x80000000 != 0 return
 iifname {dev} ip6 hoplimit 255 icmpv6 type {{ nd-neighbor-solicit, nd-neighbor-advert }} return
 iifname {uplinks} udp sport 67 udp dport 68 return
 iifname {uplinks} udp sport 547 udp dport 546 return
 iifname {uplinks} ip6 hoplimit 255 icmpv6 type {{ nd-router-advert, nd-neighbor-solicit, nd-neighbor-advert }} return
{tunnel_in}
'''
    if settings['guest_interfaces']:
        result += f' iifname {guests} meta l4proto {{ tcp, udp }} th dport {{ 53, 67 }} return\n'
    result += ' counter drop\n}\n'
    result += f'''chain lf_routing_output {{ type filter hook postrouting priority 101; policy accept;
 oifname "lo" return
 meta iif 0 oifname {dev} meta l4proto {{ tcp, udp }} return
{tunnel_out}
'''
    for guest in settings['guests']:
        sources = [value for value in guest['sources'] if ':' not in value]
        if sources:
            result += (f' iifname {json.dumps(guest["interface"])} oifname {dev} '
                'ip saddr { ' + ', '.join(sources) + ' } meta mark 0x233 meta l4proto { tcp, udp } return\n')
            result += (f' iifname {dev} oifname {json.dumps(guest["interface"])} '
                'ct direction reply ct state established,related ct mark & 0x80000000 != 0 return\n')
    for family in (4, 6):
        field = 'ip' if family == 4 else 'ip6'
        core = t[f'core{family}']
        result += (f' iifname {dev}{to_tunnel} ct original {field} saddr {core} ct direction original '
            f'meta l4proto {{ tcp, udp }} {field} daddr . meta l4proto . th dport @nodes{family} return\n')
        result += (f' oifname {dev}{from_tunnel} ct direction reply ct state established,related '
            f'ct original {field} saddr {core} ct original {field} daddr . ct protocol . ct original proto-dst @nodes{family} return\n')
    result += f''' oifname {uplinks} meta skuid {{ 0, {settings['dhcp_uid']} }} udp sport 68 udp dport 67 return
 oifname {uplinks} meta skuid {{ 0, {settings['dhcp_uid']} }} udp sport 546 udp dport 547 return
 oifname {uplinks} ip6 hoplimit 255 icmpv6 type {{ nd-router-solicit, nd-neighbor-solicit, nd-neighbor-advert }} return
 oifname {dev} ip6 hoplimit 255 icmpv6 type {{ nd-neighbor-solicit, nd-neighbor-advert }} return
'''
    if settings['guest_interfaces']:
        result += f' meta iif 0 oifname {guests} meta l4proto {{ tcp, udp }} th sport {{ 53, 67 }} return\n'
    return result + ' counter drop\n}\n}\n'


def table_view(settings, document):
    normalizer = module(settings['rules_tool'], 'routing_rules')
    objects, rules, elements = {}, {}, {}
    for entry in normalizer.unwrap(document).get('nftables', []):
        if len(entry) != 1:
            raise ValueError('invalid routing firewall observation')
        kind, item = next(iter(entry.items()))
        if kind not in ('table', 'chain', 'set', 'rule', 'element') or not isinstance(item, dict):
            continue
        if item.get('family') != 'inet' or (item.get('table') if kind != 'table' else item.get('name')) != 'lf_routing':
            continue
        value = normalizer.normalize(item)
        if kind == 'rule':
            rules.setdefault(item['chain'], []).append(value)
        elif kind == 'element':
            entries = value['elem']
            if isinstance(entries, dict) and set(entries) == {'set'}:
                entries = entries['set']
            if not isinstance(entries, list):
                raise ValueError('invalid routing set event')
            elements.setdefault(item['name'], []).extend(entries)
        else:
            objects[kind + ':' + item.get('name', '')] = value
    # nft echo can publish set elements separately and uses numeric protocols;
    # a live listing embeds the elements in the set and prints protocol names.
    for name in ('nodes4', 'nodes6'):
        value = objects.get('set:' + name)
        if value is None:
            if name in elements:
                raise ValueError('routing elements have no declared set')
            continue
        entries = value.get('elem', []) + elements.pop(name, [])
        for entry in entries:
            parts = entry.get('concat') if isinstance(entry, dict) else None
            if not isinstance(parts, list) or len(parts) != 3:
                raise ValueError('invalid routing node tuple')
            parts[1] = normalizer.canonical_proto(parts[1])
        value['elem'] = sorted(entries, key=lambda item: json.dumps(item, sort_keys=True))
    if elements:
        raise ValueError('undeclared routing elements')
    if 'table:lf_routing' not in objects:
        raise ValueError('routing firewall is absent')
    return dict(objects=objects, rules=rules)


def nft(settings, *args, **kwargs):
    return subprocess.run([settings['nft'], *args], capture_output=True, text=True,
                          timeout=5, check=True, **kwargs)


def check(settings, mode):
    expected = read(safe_file(Path(settings['state']) / 'routing-firewall.json', private=True))
    if expected.get('sha256') != sha(firewall(settings, mode).encode()):
        raise ValueError('routing firewall belongs to another policy or snapshot')
    actual = table_view(settings, json.loads(nft(settings, '-j', 'list', 'table', 'inet', 'lf_routing').stdout))
    if actual != expected['table']:
        raise ValueError('routing firewall differs from the declared transport boundary')


def enforce(settings, mode):
    try:
        check(settings, mode)
        return
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        pass
    script = firewall(settings, mode)
    applied_table = nft(settings, '-e', '-j', '-f', '-', input=script)
    atomic(Path(settings['state']) / 'routing-firewall.json',
           dict(sha256=sha(script.encode()), table=table_view(settings, json.loads(applied_table.stdout))), 0o600)
    check(settings, mode)


def view(settings):
    try:
        record = binding(settings)
    except (OSError, ValueError, KeyError, TypeError):
        return dict(routing_schema=1, routing_requested='unknown', routing_applied='unknown',
                    routing_epoch=None, routing_policy_digest=None, routing_phase='unknown')
    return {key: record[key] for key in FIELDS} | {'routing_schema': 1}


def main():
    settings = read(sys.argv[1])
    command = sys.argv[2]
    if not settings.get('enabled'):
        raise ValueError('runtime routing is disabled')
    if command in ('view', 'requested', 'probe-ready'):
        if command == 'view':
            print(json.dumps(view(settings)))
            return
        record = binding(settings)
        if command == 'requested':
            print(record['routing_requested'])
        elif command == 'probe-ready':
            if record['routing_phase'] not in ('verifying', 'ready'):
                raise ValueError('routing transaction is incomplete')
        return
    if os.geteuid() != 0:
        raise ValueError('routing changes require root')
    if command == 'initialize' and (len(sys.argv) == 3 or len(sys.argv) == 4 and sys.argv[3] in ('direct', 'blocked')):
        initialize(settings, sys.argv[3] if len(sys.argv) == 4 else None)
    elif command == 'begin' and len(sys.argv) in (4, 5):
        begin(settings, sys.argv[3], sys.argv[4] if len(sys.argv) == 5 else None,
              os.environ.get('LF_SOURCE_GENERATION'))
    elif command == 'egresses' and len(sys.argv) == 3:
        with transition_locks(settings) as source_settings:
            broker = module(settings['source_tool'], 'routing_source')
            worker, base, *_ = broker.local_source(source_settings, broker.selected(Path(source_settings['root'])), 'proxy-only')
            print(json.dumps([dict(id=worker.node_identity(node), name=node['name'], protocol=node['type'],
                udp=node.get('udp', node['type'] in ('hysteria2', 'tuic')) is True)
                for node in worker.egress_nodes(base, settings['ip_tunnel']['policy'] if settings.get('ip_tunnel') else None)],
                ensure_ascii=True))
    elif command == 'stop-ip-tunnel' and len(sys.argv) == 3:
        stop_ip_tunnel(settings)
    elif command == 'failed':
        phase(settings, 'failed')
    elif command == 'applied':
        applied(settings)
    elif command in ('enforce', 'check') and len(sys.argv) == 4 and sys.argv[3] in ('direct', 'proxy', 'blocked'):
        (enforce if command == 'enforce' else check)(settings, sys.argv[3])
    elif command == 'same-ready' and len(sys.argv) in (4, 5):
        try:
            record = binding(settings)
        except StalePolicy:
            raise SystemExit(1) from None
        state = module(settings['state_tool'], 'routing_readiness')
        ready = state.readiness(root=Path(settings['state']).parent, target='proxy')
        if (record['routing_requested'] != sys.argv[3] or ready.get('ready') is not True
                or len(sys.argv) == 5 and record.get('egress_requested') != sys.argv[4]):
            raise SystemExit(1)
        stage = module(settings['stage_tool'], 'routing_stage')
        stage_settings = read(settings['stage_settings'])
        with stage.source_lock(stage_settings):
            effective = Path(settings['runtime']) / 'effective'
            authority = Path(settings['state']) / 'authorized-config'
            stage.verify(effective, authority.with_name('config-digest'), authority)
            if not stage.restart_eligible(stage_settings, effective.resolve()):
                raise SystemExit(1)
        check(settings, 'proxy')
    else:
        raise ValueError('invalid fixed routing operation')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        codes = {
            'proxy-only requires an accepted pinned local source': 'accepted-source-required',
            'accepted source was modified': 'accepted-source-modified',
            'source content baseline differs': 'source-content-mismatch',
            'source requires pinned public endpoints': 'pinned-endpoints-required',
            'original Rule data is unavailable': 'original-rules-unavailable',
            'routing authority is missing or inconsistent': 'routing-authority-invalid',
            'routing policy changed; run clash-rules to recover': 'routing-policy-changed (run clash-rules)',
            'routing firewall differs from the declared transport boundary': 'routing-firewall-drift',
            'routing requires a complete current health proof': 'complete-health-proof-required',
        }
        code = codes.get(str(error), 'transaction-or-enforcement-unavailable')
        if os.geteuid() == 0 and len(sys.argv) > 2 and sys.argv[2] in ('begin', 'enforce', 'check', 'applied'):
            try:
                settings = read(sys.argv[1])
                record = binding(settings)
                atomic(Path(settings['state']) / 'routing-failure.json',
                       {key: record[key] for key in ('boot_id', 'routing_epoch')} | {'code': code})
            except (OSError, ValueError, KeyError, TypeError):
                pass
        print('proxy routing: ' + code, file=sys.stderr)
        raise SystemExit(1)
