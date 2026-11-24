"""Read-only local listener and trust posture, with bounded process inspection."""

import grp
import hashlib
import importlib.util
import ipaddress
import json
import math
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import time


PROXY_HINTS = {1080, 1081, 3128, 7897, 8080, 8118, 8788, 9050, 9150, 33332, 33333}
ENV_LIMIT = 256 * 1024


def socket_address(address, family):
    if address == "*":
        return "0.0.0.0" if family == 4 else "::"
    return str(ipaddress.ip_address(address.split('%', 1)[0].strip('[]')))


def id_for(database, name):
    try:
        return database(name)[2] if name else None
    except KeyError:
        return None


def declaration(row, expected):
    candidates = [item for item in expected if item['protocol'] == row['protocol']
                  and item['port'] == row['port']]
    for item in candidates:
        if item['address'] != row['address']:
            continue
        allowed = item.get('uids', ())
        if 'user' in item:
            allowed = (*allowed, id_for(pwd.getpwnam, item['user']))
        if row['uid'] is None:
            continue
        if row['uid'] in allowed:
            cgroup = item.get('cgroup')
            if not cgroup or row['cgroup'] in (None, cgroup):
                service = item.get('service') or cgroup.rsplit('/', 1)[-1]
                return 'declared' if cgroup and row['cgroup'] else 'uid-match', service
    if any(item['address'] == row['address'] for item in candidates):
        return ('identity-unknown' if row['uid'] is None else 'identity-mismatch'), None
    return ('binding-mismatch' if candidates else 'unlisted'), None


def listed(port, entries):
    for entry in entries:
        match = re.fullmatch(r'\s*([0-9]+)(?:\s*-\s*([0-9]+))?\s*', str(entry))
        if match and int(match[1]) <= port <= int(match[2] or match[1]):
            return True
    return False


def ingress_for(row, policy, mode):
    if row['binding'] == 'loopback':
        return dict(lan='loopback-only', tailnet='loopback-only', guests='loopback-only')
    if mode == 'unknown':
        return dict(lan='unknown', tailnet='unknown', guests='unknown')
    protocol, port = row['protocol'], row['port']
    if port in policy['privateProxyPorts']:
        return dict(lan='denied', tailnet='denied', guests='denied')
    if protocol == 'udp' and port == policy['lan']['transportPort']:
        return dict(lan='denied' if mode == 'blocked' else 'marked-transport-only',
                    tailnet='denied', guests='denied')
    tailnet = 'listed' if mode != 'blocked' and listed(port, policy['tailnet'][protocol]) else 'denied'
    address = ipaddress.ip_address(row['address'])
    address = getattr(address, 'ipv4_mapped', None) or address
    families = {address.version}
    if address.is_unspecified and row.get('ipv6_only') is False:
        families.add(4)
    lan = 'listed-for-trusted-sources' if 4 in families and listed(port, policy['lan'][protocol]) else 'denied'
    if protocol == 'udp' and ((port == 68 and 4 in families) or (port == 546 and 6 in families)):
        lan = 'dhcp-replies-only'
    guests = []
    for guest in policy['guests']:
        ports = guest['hostTcpPorts'] if protocol == 'tcp' else guest['hostUdpPorts']
        if port == 53 or port in ports:
            for gateway in map(ipaddress.ip_address, guest['gateways']):
                if gateway.version in families and (address.is_unspecified or address == gateway):
                    guests.append(guest['interface'])
                    break
    return dict(lan=lan, tailnet=tailnet, guests=guests if guests else 'denied')


def unit_properties(command, systemctl, unit, *properties):
    result = command([systemctl, 'show', unit, *[f'--property={key}' for key in properties]])
    values = dict(line.split('=', 1) for line in result['output'].splitlines() if '=' in line)
    return values if result['available'] or values.get('LoadState') == 'not-found' else {}


def ssh_status(command, systemctl, rows, complete, policy):
    states = {}
    for name in ('sshd.service', 'sshd.socket'):
        values = unit_properties(command, systemctl, name, 'LoadState', 'ActiveState')
        states[name] = {
            'load': values.get('LoadState') if values.get('LoadState') in (
                'loaded', 'not-found', 'masked', 'error', 'bad-setting') else None,
            'active': values.get('ActiveState') if values.get('ActiveState') in (
                'active', 'inactive', 'activating', 'deactivating', 'failed', 'reloading') else None,
        }
    present = any(row['protocol'] == 'tcp' and row['port'] == 22 for row in rows)
    return dict(units=states, tcp_22=True if present else False if complete else None,
                declared_tcp_22={
                    'tailnet': listed(22, policy['tailnet']['tcp']),
                    'guests': [guest['interface'] for guest in policy['guests'] if 22 in guest['hostTcpPorts']],
                    'lan': listed(22, policy['lan']['tcp']),
                })


def digest(path):
    try:
        with Path(path).open('rb') as stream:
            data = stream.read(8 * 1024 * 1024 + 1)
        return hashlib.sha256(data).hexdigest() if len(data) <= 8 * 1024 * 1024 else None
    except OSError:
        return None


def process_environment(pid, keys, approved=None, cgroup=None):
    result = {'pid': pid, 'available': False, 'variables': []}
    try:
        fd = os.open(f'/proc/{pid}', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if cgroup:
                with os.fdopen(os.open('cgroup', os.O_RDONLY, dir_fd=fd)) as stream:
                    observed = next((line[3:].strip() for line in stream if line.startswith('0::')), '')
                if observed != cgroup and not observed.startswith(cgroup + '/'):
                    return result
            with os.fdopen(os.open('environ', os.O_RDONLY, dir_fd=fd), 'rb') as stream:
                data = stream.read(ENV_LIMIT + 1)
        finally:
            os.close(fd)
        if len(data) > ENV_LIMIT:
            return result
    except OSError:
        return result
    variables = dict(item.split(b'=', 1) for item in data.split(b'\0') if b'=' in item)
    for key in sorted(keys):
        value = variables.get(key.encode())
        if value is not None or (approved and key in approved):
            status = 'present'
            if approved is not None:
                if key not in approved:
                    status = 'unexpected'
                elif value is None:
                    status = 'missing'
                else:
                    status = 'match' if approved[key] == hashlib.sha256(value).hexdigest() else 'mismatch'
            result['variables'].append({'name': key, 'status': status})
    return dict(result, available=True)


def json_result(result):
    if result['available'] and len(result['output']) <= 1024 * 1024:
        try:
            return True, json.loads(result['output'])
        except (ValueError, RecursionError):
            pass
    return False, None


def tailscale_status(command, executable):
    result = dict(ssh=None, accept_dns=None, accept_routes=None, serve=None, funnel=None)
    if not executable:
        return result
    _, prefs = json_result(command([executable, 'debug', 'prefs']))
    if isinstance(prefs, dict):
        for key, field in [('ssh', 'RunSSH'), ('accept_dns', 'CorpDNS'), ('accept_routes', 'RouteAll')]:
            result[key] = prefs[field] if type(prefs.get(field)) is bool else None
    available, config = json_result(command([executable, 'serve', 'status', '--json']))
    if available and config is None:
        result.update(serve=False, funnel=False)
    elif isinstance(config, dict):
        foreground = config.get('Foreground') or {}
        if isinstance(foreground, dict) and all(isinstance(item, dict) for item in foreground.values()):
            configurations = (config, *foreground.values())
            result.update(
                serve=any(item.get(key) for item in configurations for key in ('TCP', 'Web', 'Services')),
                funnel=any(value is True for item in configurations
                           for value in (item.get('AllowFunnel') or {}).values())
                if all(isinstance(item.get('AllowFunnel') or {}, dict) for item in configurations) else None,
            )
    return result


def socket_metadata(path):
    parts = Path(path).parts
    flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(parts[0], flags)
    try:
        for part in parts[1:-1]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
    finally:
        os.close(fd)


def unix_status(specs):
    result = {}
    for service, spec in specs.items():
        try:
            info = socket_metadata(spec['path'])
        except FileNotFoundError:
            result[service] = {'status': 'absent'}
            continue
        except OSError:
            result[service] = {'status': 'unknown'}
            continue
        if not stat.S_ISSOCK(info.st_mode):
            result[service] = {'status': 'not-a-socket'}
            continue
        uid = spec.get('uid', id_for(pwd.getpwnam, spec.get('user')))
        gid = id_for(grp.getgrnam, spec.get('group'))
        mode = f'{stat.S_IMODE(info.st_mode):04o}'
        matched = ((uid is None or uid == info.st_uid)
                   and (gid is None or gid == info.st_gid) and spec.get('mode', mode) == mode)
        known = uid is not None and (not spec.get('group') or gid is not None)
        result[service] = dict(status=('match' if matched else 'mismatch') if known else 'unknown',
                               uid=info.st_uid, gid=info.st_gid, mode=mode)
    return result


def collect(settings, command, read_json, *, pids=(), privileged=False):
    policy = settings['posture']
    spec = importlib.util.spec_from_file_location('listener_snapshot', policy['listenerModule'])
    listeners = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(listeners)
    manifest = read_json(policy['listenerSettings'])
    expected = [record for group in manifest['groups'].values() for record in group
                if not record.get('namespace')] + policy['declarations']
    try:
        current_namespace = os.readlink('/proc/self/ns/net')
    except OSError:
        current_namespace = None
    try:
        host_namespace = current_namespace == os.readlink('/proc/1/ns/net') if current_namespace else None
    except OSError:
        host_namespace = None
    mode_state = read_json('/run/proxy-mode/state.json') or {}
    mode = mode_state.get('mode')
    if mode not in ('direct', 'proxy', 'blocked') or host_namespace is False or Path('/run/netsec/failed').exists():
        mode = 'unknown'
    if mode == 'proxy' and mode_state.get('authorization_lease_seconds') is not None:
        authorization = read_json('/run/proxy-mode/authorization.json') or {}
        until = authorization.get('until')
        if (type(until) not in (int, float) or not math.isfinite(until) or until <= time.monotonic()
                or any(authorization.get(key) != mode_state.get(key) for key in ('policy_version', 'boot_id'))):
            mode = 'unknown'

    rows, unavailable = [], []
    for family in (4, 6):
        for protocol in ('tcp', 'udp'):
            try:
                sockets = listeners.snapshot(manifest['ss'], {protocol}, family=family)
                for socket in sockets:
                    socket['address'] = socket_address(socket['address'], family)
            except (OSError, ValueError, subprocess.SubprocessError):
                unavailable.append(f'{protocol}{family}')
                continue
            for socket in sockets:
                address = ipaddress.ip_address(socket['address'])
                mapped = getattr(address, 'ipv4_mapped', None)
                binding = ('wildcard' if address.is_unspecified else
                           'loopback' if (mapped or address).is_loopback else 'address')
                socket['uid'] = socket['uid'] if socket['extended'] else None
                classification, service = declaration(socket, expected)
                if host_namespace is False:
                    classification, service = 'unknown', None
                row = {key: socket[key] for key in ('protocol', 'address', 'port', 'uid')}
                row.update(family=family, binding=binding, declaration=classification, service=service,
                           ipv6_only=socket.get('ipv6_only'), proxy_port_hint=socket['port'] in PROXY_HINTS)
                row['ingress_policy'] = ingress_for(row, policy['ingress'], mode)
                rows.append(row)

    bundle, reference = digest('/etc/ssl/certs/ca-certificates.crt'), digest(policy['caBundle'])
    processes = [process_environment(pid, policy['environmentKeys']) for pid in dict.fromkeys(pids)]
    unit_environment = {}
    if privileged:
        for unit, approved in policy['environmentUnits'].items():
            values = unit_properties(command, settings['systemctl'], unit + '.service', 'MainPID', 'ControlGroup')
            main, group = values.get('MainPID'), values.get('ControlGroup')
            unit_environment[unit] = (process_environment(int(main), policy['environmentKeys'], approved, group)
                                      if group and main and main.isdecimal() and int(main) > 0 else None)
    desktop = None
    if os.geteuid() == policy['desktopUid'] and os.environ.get('XDG_RUNTIME_DIR'):
        result = command([policy['dconf'], 'read', '/system/proxy/mode'])
        value = result['output'].strip("'")
        if result['available'] and value in ('none', 'manual', 'auto'):
            desktop = value
    return {
        'scope': {'uid': os.geteuid(), 'network_namespace': current_namespace,
                  'host_namespace_confirmed': host_namespace, 'other_namespaces': 'not-inspected'},
        'mode': mode,
        'listeners': rows,
        'unavailable': unavailable,
        'unix_sockets': unix_status(policy['unix']),
        'openssh': ssh_status(command, settings['systemctl'], rows,
                              not any(s.startswith('tcp') for s in unavailable), policy['ingress']),
        'tailscale': tailscale_status(command, policy['tailscale']),
        'ca_bundle': {'sha256': bundle, 'generation_match': bundle == reference if bundle and reference else None},
        'environment': {'privileged_requested': privileged, 'units': unit_environment, 'processes': processes},
        'desktop_proxy_mode': desktop,
    }


def format_report(report):
    def value(item):
        return 'unknown' if item is None else str(item)

    scope = report['scope']
    yield (f"Network posture · {value(scope['network_namespace'])} · observer UID {scope['uid']}"
           f" · host namespace confirmed {value(scope['host_namespace_confirmed'])} · policy mode {report['mode']}")
    if report['unavailable']:
        yield 'Unavailable listeners · ' + ', '.join(report['unavailable'])
    for row in report['listeners']:
        ingress = ', '.join(f'{role}={state}' for role, state in row['ingress_policy'].items())
        hint = ' · proxy-port hint' if row['proxy_port_hint'] else ''
        service = f" ({row['service']})" if row['service'] else ''
        yield (f"{row['protocol']}{row['family']} [{row['address']}]:{row['port']} · {row['binding']}"
               f" · uid {value(row['uid'])} · {row['declaration']}{service} · {ingress}{hint}")
    for name, socket in report['unix_sockets'].items():
        attributes = f" · uid {socket['uid']} · gid {socket['gid']} · {socket['mode']}" if 'uid' in socket else ''
        yield f"Unix {name} · {socket['status']}{attributes}"
    yield 'OpenSSH · ' + ', '.join(f"{unit}={value(state['load'])}/{value(state['active'])}"
                                   for unit, state in report['openssh']['units'].items())
    yield f"TCP/22 listener · {value(report['openssh']['tcp_22'])}"
    yield 'TCP/22 declarations · ' + ', '.join(f'{role}={value(state)}'
                                              for role, state in report['openssh']['declared_tcp_22'].items())
    yield 'Tailscale · ' + ', '.join(f'{name}={value(state)}' for name, state in report['tailscale'].items())
    yield f"System CA bundle · {value(report['ca_bundle']['sha256'])} · generation match {value(report['ca_bundle']['generation_match'])}"
    yield f"Desktop proxy mode · {value(report['desktop_proxy_mode'])}"
    for name, process in (*report['environment']['units'].items(),
                          *((f"PID {p['pid']}", p) for p in report['environment']['processes'])):
        status = ', '.join(f"{item['name']}={item['status']}" for item in process['variables']) \
            if process and process['available'] else 'unknown'
        yield f"{name} startup environment · {status or 'no tracked overrides'}"
