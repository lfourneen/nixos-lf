"""Fixed core topology, host policy rendering and read-only netns observations.

Only the host setup and mode coordinator modify host network objects. The
helper, core and observer enter the namespace through systemd before exec.
"""
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import socket
import stat
import struct
import subprocess
import sys
import time


def run(settings, program, *args, **kwargs):
    return subprocess.run([settings[program], *map(str, args)], text=True,
                          capture_output=True, check=True, timeout=5, **kwargs)


def module(name, settings):
    spec = importlib.util.spec_from_file_location(name, settings[name + 'Module'])
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def request(path, operation, deadline=None):
    """Ask the root-owned, capless observer for one fixed read-only operation."""
    deadline = time.monotonic() + 2 if deadline is None else deadline
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('namespace observation deadline exhausted')
        return value

    info = os.stat(path, follow_symlinks=False)
    parent = os.stat(Path(path).parent, follow_symlinks=False)
    if info.st_uid != 0 or not stat.S_ISSOCK(info.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022:
        raise ValueError('unsafe namespace observer socket')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(remaining())
        stream.connect(path)
        _, uid, _ = struct.unpack('3i', stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != 0:
            raise ValueError('namespace observer is not root')
        stream.settimeout(remaining())
        stream.sendall(operation.encode() + b'\n')
        data = bytearray()
        while True:
            stream.settimeout(remaining())
            chunk = stream.recv(65536)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 1048576:
                raise ValueError('oversized namespace observation')
    result = json.loads(data)
    if result.get('ok') is not True:
        raise ValueError('namespace observation unavailable: ' + str(result.get('detail', 'unknown'))[:512])
    return result


def observe(settings):
    path = settings['observer']
    Path(path).unlink(missing_ok=True)
    listeners, tun = module('listeners', settings), module('tun', settings)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(path)
        # Read-only observations have the same audience as unprivileged ss.
        os.chmod(path, 0o666)
        server.listen(32)
        while True:
            stream, _ = server.accept()
            with stream:
                stream.settimeout(2)
                try:
                    operation = stream.recv(32)
                    if operation == b'listeners\n':
                        sockets = listeners.snapshot(settings['ss'], {'tcp', 'udp'})
                        for item in sockets:
                            item['namespace'] = True
                        result = {'ok': True, 'sockets': sockets}
                    elif operation == b'tun\n':
                        tun.check({'ip': settings['ip'], 'device': settings['interface'],
                                   'table': settings['table'], 'ipv6': settings['ipv6'],
                                   'uids': [0, settings['desktopUid']]})
                        result = {'ok': True}
                    elif operation == b'strict-tcp4\n':
                        lines = Path('/proc/net/tcp').read_text().splitlines()
                        endpoint = f'0100007F:{settings["ports"]["mihomoStrict"]:04X}'
                        selected = [line for line in lines[1:] if len(line.split()) > 9
                                    and line.split()[1] == endpoint and line.split()[3] == '01']
                        result = {'ok': True, 'table': '\n'.join(lines[:1] + selected)}
                    else:
                        result = {'ok': False}
                except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                    result = {'ok': False, 'detail': str(error)[:512]}
                try:
                    stream.sendall(json.dumps(result).encode())
                except OSError:
                    pass


def validate(settings):
    for name in ('interface', 'peer', 'namespace'):
        if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', settings[name]):
            raise ValueError('invalid fixed core object')
    for key in ('private4', 'private6', 'localTargets', 'routeExclude', 'nat64'):
        for value in settings[key]:
            ipaddress.ip_network(value, strict=True)
    for port in settings['ports'].values():
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('invalid fixed port')
    for family, prefix in ((4, 30), (6, 64)):
        network = ipaddress.ip_network(settings[f'subnet{family}'], strict=True)
        host, core = (ipaddress.ip_address(settings[f'{side}{family}']) for side in ('host', 'core'))
        private = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16') if family == 4 else ('fc00::/7',)
        if (network.version != family or network.prefixlen != prefix or host == core
                or host not in network or core not in network
                or not any(network.version == ipaddress.ip_network(cidr).version
                           and network.subnet_of(ipaddress.ip_network(cidr)) for cidr in private)
                or host in (network.network_address, network.broadcast_address)
                or core in (network.network_address, network.broadcast_address)):
            raise ValueError('coreLink requires distinct usable private veth endpoints in its /30 and /64')
    macs = [settings[key].lower() for key in ('hostMac', 'coreMac')]
    if (len(set(macs)) != 2 or any(not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', mac)
            or int(mac[:2], 16) & 3 != 2 for mac in macs)):
        raise ValueError('coreLink MAC addresses must be distinct locally administered unicast addresses')


def check_overlap(s):
    """Never take an existing LAN/VPN prefix for our internal point-to-point link."""
    networks = [ipaddress.ip_network(s['subnet4'])]
    if s['ipv6']:
        networks.append(ipaddress.ip_network(s['subnet6']))
    for item in json.loads(run(s, 'ip', '-j', 'address', 'show').stdout):
        if item['ifname'] == s['interface']:
            continue
        for address in item.get('addr_info', []):
            value = ipaddress.ip_network(f'{address["local"]}/{address["prefixlen"]}', strict=False)
            if any(value.version == net.version and value.overlaps(net) for net in networks):
                raise ValueError('coreLink overlaps an existing interface address; configure a different internal subnet')
    for net in networks:
        for item in json.loads(run(s, 'ip', '-j', f'-{net.version}', 'route', 'show', 'table', 'all').stdout):
            destination = item.get('dst', 'default')
            if (destination == 'default' or item.get('dev') == s['interface']
                    or str(item.get('table', 'main')) == str(s['table'])
                    or item.get('type') == 'throw'):
                continue
            value = ipaddress.ip_network(destination, strict=False)
            if value.version == net.version and value.overlaps(net):
                raise ValueError('coreLink overlaps an existing route; configure a different internal subnet')


def render(settings, destination):
    validate(settings)
    s, p = settings, settings['ports']
    device = json.dumps(s['interface'])
    c4, c6, h4, h6 = (s[key] for key in ('core4', 'core6', 'host4', 'host6'))
    reserved = ', '.join(map(str, [53, p['mihomoMixed'], p['mihomoStrict'], p['mihomoDns'],
        p['mihomoTproxy'], p['gostHttp'], p['gostRedirect'], p['dnsmasq'], p['unbound'],
        p['socketGuestDns'], p['coreDnsTransport'], p['coreDnsBootstrapTransport'], p['coreLoopback'],
        *(p[name] for name in ('coreRealDns', 'unboundInfrastructure', 'coreInfrastructureDns') if name in p)]))
    inlets = ', '.join(map(str, [p['mihomoMixed'], p['mihomoStrict'], p['mihomoDns']]))
    unsafe4 = ['0.0.0.0/8', '127.0.0.0/8', '169.254.169.254/32', '169.254.170.2/32',
               '100.100.100.200/32', '192.0.0.0/24', '198.18.0.0/15', '224.0.0.0/4', '240.0.0.0/4', s['fake']]
    unsafe6 = ['::/96', '::ffff:0:0/96', '::1/128', 'fd00:ec2::254/128', 'fec0::/10', 'ff00::/8', '64:ff9b:1::/48',
               '2001::/32', '2002::/16', *s['nat64']]
    def values(items):
        return '{ ' + ', '.join(dict.fromkeys(items)) + ' }'
    def local(family):
        return [value for value in s['localTargets'] if ipaddress.ip_network(value).version == family]
    def permitted_loop(family):
        address = ipaddress.ip_address('127.0.0.1' if family == 4 else '::1')
        return any(address in ipaddress.ip_network(value) for value in local(family))
    def grant(reply=False):
        field = (lambda f: f'ct original {f} daddr') if reply else (lambda f: f'{f} daddr')
        transport = 'ct protocol { tcp, udp }' if reply else 'meta l4proto { tcp, udp }'
        result = []
        for family, unsafe, private in [(4, unsafe4, s['private4']), (6, unsafe6, s['private6'])]:
            f = 'ip' if family == 4 else 'ip6'
            result.append(f'{field(f)} {values(unsafe)} counter drop')
            if local(family):
                result.append(f'{field(f)} {values(local(family))} {transport} return')
            result.append(f'{field(f)} != {values(private + unsafe)} {transport} return')
        result.append('counter drop')
        return '\n'.join(result)
    magic = ''
    if s['magicDns']:
        m = s['magicDns']
        magic = f'oifname {json.dumps(m["interface"])} ip daddr {m["address"]} meta l4proto {{ tcp, udp }} th dport 53 return'
    # Opportunistic DNS is sent back to the filtered host gateway; it never
    # becomes a new core-wide plaintext uplink exception.
    strict_dns = f'{magic}\nmeta l4proto {{ tcp, udp }} th dport 53 counter drop'
    result = {
        'filter.nft': f'''
chain lf_core_scrub {{
 type filter hook prerouting priority -310; policy accept;
 iifname {device} meta mark set 0
}}
chain lf_core_path {{
 type filter hook postrouting priority -5; policy accept;
 oifname {device} ct direction original meta l4proto {{ tcp, udp }} ct mark set ct mark | 0x80000000
}}
chain lf_core_egress {{
 {strict_dns}
 {grant()}
}}
chain lf_core_return {{
 {grant(True)}
}}
chain lf_core_forward_guard {{
 type filter hook forward priority -5; policy accept;
 iifname {device} ct direction reply ct state established,related ct mark & 0x80000000 != 0 accept
 iifname {device} ct direction reply counter drop
 iifname {device} ip saddr {c4} jump lf_core_egress
 iifname {device} ip saddr {c4} accept
 iifname {device} ip6 saddr {c6} jump lf_core_egress
 iifname {device} ip6 saddr {c6} accept
 iifname {device} counter drop
 oifname {device} ct direction reply ct original ip saddr {c4} jump lf_core_return
 oifname {device} ct direction reply ct original ip6 saddr {c6} jump lf_core_return
}}
chain lf_core_input {{
 # Host input is reserved for replies on flows that the host sent through this veth.
 ct direction reply ct state established,related ct mark & 0x80000000 != 0 accept
 counter drop
}}
chain lf_core_alias_guard {{
 type filter hook output priority -199; policy accept;
 ct direction reply ct state established,related accept
 meta skuid != 0 ip daddr {{ {h4}, {c4} }} counter drop
 meta skuid != 0 ip6 daddr {{ {h6}, {c6} }} counter drop
}}
chain lf_core_route_guard {{
 type filter hook postrouting priority -6; policy accept;
 ip daddr {c4} oifname != {device} counter drop
 ip6 daddr {c6} oifname != {device} counter drop
}}
''',
        'nat4.nft': f'''
chain lf_core_inlet {{
 type nat hook output priority -110; policy accept;
 ip daddr 127.0.0.1 meta l4proto {{ tcp, udp }} th dport {{ {inlets} }} dnat to {c4}
}}
chain lf_core_snat {{
 type nat hook postrouting priority 99; policy accept;
 oifname {device} ip daddr {c4} ip saddr 127.0.0.0/8 snat to {h4}
 iifname {device} ip saddr {c4} oifname != {device} masquerade
}}
''',
        'nat6.nft': f'''
chain lf_core_inlet {{
 type nat hook output priority -110; policy accept;
 ip6 daddr ::1 meta l4proto {{ tcp, udp }} th dport {{ {inlets} }} dnat to {c6}
}}
chain lf_core_snat {{
 type nat hook postrouting priority 99; policy accept;
 oifname {device} ip6 daddr {c6} ip6 saddr ::1 snat to {h6}
 iifname {device} ip6 saddr {c6} oifname != {device} masquerade
}}
''',
    }
    guest_tproxy = '\n'.join(
        f'iifname {json.dumps(s["peer"])} ip saddr {values([c for c in guest["sources"] if ":" not in c])} '
        f'ip daddr != {values(s["private4"] + ["127.0.0.0/8"])} meta l4proto {{ tcp, udp }} '
        f'tproxy to :{p["mihomoTproxy"]} meta mark set 0x233 accept'
        for guest in s['guests'] if any(':' not in value for value in guest['sources']))
    dns4_selector = f'ip daddr != {s["magicDns"]["address"]} ' if s['magicDns'] else ''
    dns_out4 = (f'{dns4_selector}tcp dport 53 dnat to 127.0.0.1:{p["coreLoopback"]}\n'
                f'{dns4_selector}udp dport 53 dnat to 127.0.0.1')
    dns_out6 = (f'tcp dport 53 dnat to [::1]:{p["coreLoopback"]}\n'
                f'udp dport 53 dnat to ::1')
    validator_rules, validator_local = [], {4: '', 6: ''}
    if s.get('localDnssec'):
        uid = s['validatorUid']
        rule = (f'meta skuid {uid} ip daddr 127.0.0.1 tcp dport '
                f'{{ {p["coreDnsTransport"]}, {p["coreDnsBootstrapTransport"]} }}')
        validator_rules.append(rule + ' accept')
        validator_local[4] = rule + ' return'
        validator_rules += [f'meta skuid {uid} oifname lo ct direction reply '
                            f'meta l4proto {{ tcp, udp }} th sport {{ {p["unbound"]}, {p["socketGuestDns"]} }} accept',
                            f'meta skuid {uid} counter drop']
    validator_policy = ('table inet lf_core_dns { chain output { '
        'type filter hook output priority -199; policy accept;\n' + '\n'.join(validator_rules) + '\n}\n}\n') if validator_rules else ''
    result['namespace.nft'] = f'''
destroy table inet lf_core_dns
{validator_policy}
destroy table ip lf_core_inlets
table ip lf_core_inlets {{
 chain ingress {{ type nat hook prerouting priority -120; policy accept;
  ip daddr {c4} meta l4proto {{ tcp, udp }} th dport {{ {inlets} }} dnat to 127.0.0.1
 }}
 chain loopback {{ type nat hook output priority -120; policy accept;
  {validator_local[4]}
  {dns_out4 if not s['strictDns'] else ''}
  {'ip daddr 127.0.0.1 tcp dport != { ' + reserved + ' } redirect to :' + str(p['coreLoopback']) if permitted_loop(4) else ''}
 }}
 chain source {{ type nat hook postrouting priority 100; policy accept;
  oifname {json.dumps(s['peer'])} ip daddr {h4} ip saddr 127.0.0.0/8 snat to {c4}
 }}
 chain guest {{ type filter hook prerouting priority -160; policy accept;
  {guest_tproxy}
 }}
}}
destroy table ip6 lf_core_inlets
table ip6 lf_core_inlets {{
 chain ingress {{ type nat hook prerouting priority -120; policy accept;
  ip6 daddr {c6} meta l4proto {{ tcp, udp }} th dport {{ {inlets} }} dnat to ::1
 }}
 chain loopback {{ type nat hook output priority -120; policy accept;
  {validator_local[6]}
  {dns_out6 if not s['strictDns'] else ''}
  {'ip6 daddr ::1 tcp dport != { ' + reserved + ' } redirect to :' + str(p['coreLoopback']) if permitted_loop(6) else ''}
 }}
 chain source {{ type nat hook postrouting priority 100; policy accept;
  oifname {json.dumps(s['peer'])} ip6 daddr {h6} ip6 saddr ::1 snat to {c6}
 }}
}}
destroy table inet lf_core_local_udp
table inet lf_core_local_udp {{
 chain receive {{ type filter hook prerouting priority -151; policy accept;
  iifname lo meta mark {s['loopbackReplyMark']} accept
  {'iifname lo ip daddr 127.0.0.1 udp dport 53 tproxy ip to 127.0.0.1:' + str(p['coreLoopback']) + ' accept' if not s['strictDns'] else ''}
  {'iifname lo ip daddr 127.0.0.1 udp dport != { ' + reserved + ' } tproxy ip to 127.0.0.1:' + str(p['coreLoopback']) + ' accept' if permitted_loop(4) else ''}
  {'iifname lo ip6 daddr ::1 udp dport 53 tproxy ip6 to [::1]:' + str(p['coreLoopback']) + ' accept' if not s['strictDns'] and s['ipv6'] else ''}
  {'iifname lo ip6 daddr ::1 udp dport != { ' + reserved + ' } tproxy ip6 to [::1]:' + str(p['coreLoopback']) + ' accept' if permitted_loop(6) and s['ipv6'] else ''}
 }}
}}
'''
    output = Path(destination)
    output.mkdir()
    for name, text in result.items():
        (output / name).write_text(text)


def teardown(s):
    """Retire only the topology owned by this service, including on rollback."""
    validate(s)
    namespace, device = s['namespace'], s['interface']
    ns_path = Path(s['namespacePath'])
    owner = Path('/run/clash-network-owner')
    present = Path('/sys/class/net', device).exists()
    if not owner.exists():
        if ns_path.exists() or present:
            raise ValueError('core network objects exist without the managed ownership marker; stop the previous helper first')
        return
    if present:
        link = json.loads(run(s, 'ip', '-j', '-d', 'link', 'show', 'dev', device).stdout)
        if (len(link) != 1 or link[0].get('linkinfo', {}).get('info_kind') != 'veth'
                or link[0].get('address', '').lower() != s['hostMac'].lower()):
            raise ValueError('refusing to remove a foreign or legacy core interface')
    routes(s, False)
    if ns_path.exists():
        run(s, 'ip', 'netns', 'delete', namespace)
    if Path('/sys/class/net', device).exists():
        try:
            run(s, 'ip', 'link', 'delete', device)
        except subprocess.CalledProcessError:
            # Releasing the last namespace reference removes the peer too.
            if Path('/sys/class/net', device).exists():
                raise
    owner.unlink()


def setup(s, policy):
    validate(s)
    check_overlap(s)
    teardown(s)
    namespace, device = s['namespace'], s['interface']
    owner = Path('/run/clash-network-owner')
    owner.write_text('fixed core topology\n')
    run(s, 'ip', 'netns', 'add', namespace)
    run(s, 'ip', 'link', 'add', device, 'address', s['hostMac'], 'type', 'veth',
        'peer', 'name', s['peer'], 'address', s['coreMac'])
    run(s, 'ip', 'link', 'set', s['peer'], 'netns', namespace)
    for n, dev, four, six in [(None, device, s['host4'], s['host6']),
                             (namespace, s['peer'], s['core4'], s['core6'])]:
        prefix = ['-n', n] if n else []
        run(s, 'ip', *prefix, 'addr', 'add', four + '/30', 'dev', dev)
        if s['ipv6']:
            run(s, 'ip', *prefix, '-6', 'addr', 'add', six + '/64', 'dev', dev, 'nodad')
        run(s, 'ip', *prefix, 'link', 'set', dev, 'mtu', s['mtu'], 'up')
    host_mac = json.loads(run(s, 'ip', '-j', 'link', 'show', device).stdout)[0]['address']
    core_mac = json.loads(run(s, 'ip', '-n', namespace, '-j', 'link', 'show', s['peer']).stdout)[0]['address']
    for n, dev, four, six, mac in [(None, device, s['core4'], s['core6'], core_mac),
                                  (namespace, s['peer'], s['host4'], s['host6'], host_mac)]:
        prefix = ['-n', n] if n else []
        # Toggling NOARP flushes the neighbour table. Pin entries afterwards.
        run(s, 'ip', *prefix, 'link', 'set', dev, 'arp', 'off')
        for family, address in [(4, four)] + ([(6, six)] if s['ipv6'] else []):
            run(s, 'ip', *prefix, f'-{family}', 'neigh', 'replace', address, 'lladdr', mac,
                'nud', 'permanent', 'dev', dev)
    # Only the fixed veth accepts loopback DNAT; no physical interface changes.
    run(s, 'sysctl', '-w', f'net.ipv4.conf.{device}.route_localnet=1',
        f'net.ipv4.conf.{device}.rp_filter=0', f'net.ipv6.conf.{device}.accept_ra=0')
    run(s, 'ip', 'netns', 'exec', namespace, s['sysctl'], '-w',
        f'net.ipv4.conf.{s["peer"]}.route_localnet=1', 'net.ipv4.ip_forward=1',
        'net.ipv4.ip_unprivileged_port_start=0',
        'net.ipv4.conf.all.rp_filter=0', 'net.ipv4.conf.default.rp_filter=0',
        'net.ipv6.conf.all.forwarding=1')
    run(s, 'ip', '-n', namespace, 'link', 'set', 'lo', 'up')
    for family, gateway in [(4, s['host4'])] + ([(6, s['host6'])] if s['ipv6'] else []):
        run(s, 'ip', '-n', namespace, f'-{family}', 'route', 'add', 'default', 'via', gateway)
    run(s, 'ip', 'netns', 'exec', namespace, s['nft'], '-f', policy)
    if s['guests']:
        run(s, 'ip', '-n', namespace, 'rule', 'add', 'pref', '100', 'fwmark', '0x233', 'lookup', '100')
        run(s, 'ip', '-n', namespace, 'route', 'add', 'local', 'default', 'dev', 'lo', 'table', '100')
    routes(s, False)
    if s['tun']:
        run(s, 'ip', 'route', 'replace', s['fake'], 'via', s['core4'], 'dev', device)


def routes(s, enabled):
    """The host coordinator owns only fixed rule priorities and its table."""
    if enabled:
        check_overlap(s)
        if not s['tun']:
            return
    for family, gateway in [(4, s['core4'])] + ([(6, s['core6'])] if s['ipv6'] else []):
        rules = json.loads(run(s, 'ip', '-j', f'-{family}', 'rule', 'show').stdout)
        priorities = {rule['priority'] for rule in rules}
        base = s['rule']
        if not enabled:
            for index in range(10):
                if base + index in priorities:
                    run(s, 'ip', f'-{family}', 'rule', 'del', 'pref', base + index)
            continue
        if base in priorities:
            continue
        if not Path('/sys/class/net', s['interface']).exists():
            raise ValueError('managed core veth is absent')
        # Creating a default first makes an initially absent FIB table a valid
        # flush target. A missing table is not an installation permission error.
        run(s, 'ip', f'-{family}', 'route', 'replace', 'table', s['table'], 'default',
            'via', gateway, 'dev', s['interface'], 'onlink')
        run(s, 'ip', f'-{family}', 'route', 'flush', 'table', s['table'])
        run(s, 'ip', f'-{family}', 'route', 'add', 'table', s['table'], 'default',
            'via', gateway, 'dev', s['interface'], 'onlink')
        for cidr in s['routeExclude']:
            if ipaddress.ip_network(cidr).version == family:
                run(s, 'ip', f'-{family}', 'route', 'replace', 'throw', cidr, 'table', s['table'])
        # Namespace transport may use only the main routing result, after the
        # independent ingress/forwarding grant check; its marks are scrubbed.
        # A missing physical route must terminate lookup. Falling through the
        # main-table rule would send the core's own transport back into itself
        # via our later catch-all TUN table during uplink renewal/outages.
        specifications = [(['iif', s['interface']], 'main'),
                          (['iif', s['interface']], None),
                          (['fwmark', str(s['coreMark'])], 'main'),
                          (['fwmark', str(s['tailscaleMark'])], 'main')]
        for uid in s['bypassUids']:
            specifications.append((['uidrange', f'{uid}-{uid}'], 'main'))
        specifications.append(([], str(s['table'])))
        for index, (selector, table) in reversed(list(enumerate(specifications))):
            action = ['lookup', table] if table is not None else ['unreachable']
            run(s, 'ip', f'-{family}', 'rule', 'add', 'pref', base + index, *selector, *action)


def renew(s):
    """Renew only the existing proxy-intent service through a fixed operation."""
    try:
        if Path('/run/proxy-mode/intent').read_text().strip() == 'proxy':
            run(s, 'systemctl', 'is-active', '--quiet', 'clash-verge.service')
            run(s, 'systemctl', '--no-block', 'try-restart', 'clash-verge.service')
    except (OSError, subprocess.CalledProcessError):
        pass


def watch(s):
    """Physical routes are invisible inside the core; renew stale transports.

    Debounce rtnetlink events and compare effective addresses/routes, ignoring
    lease timers. A fixed asynchronous restart never blocks an NM callback.
    """
    def configuration():
        addresses, routes = [], []
        for interface in s['uplinks']:
            try:
                for item in json.loads(run(s, 'ip', '-j', 'address', 'show', 'dev', interface).stdout):
                    addresses.append({key: item.get(key) for key in ('ifname', 'flags', 'operstate', 'mtu', 'address')})
                    addresses.extend({key: value.get(key) for key in ('family', 'local', 'prefixlen', 'scope', 'flags')}
                                     for value in item.get('addr_info', []) if value.get('scope') == 'global')
            except subprocess.CalledProcessError:
                pass  # A physical link may not exist yet at boot.
        for family in (4, 6) if s['ipv6'] else (4,):
            routes.extend({key: item.get(key) for key in ('dst', 'gateway', 'dev', 'metric', 'prefsrc', 'protocol', 'pref')}
                          for item in json.loads(run(s, 'ip', '-j', f'-{family}', 'route', 'show', 'table', 'main').stdout)
                          if item.get('dev') in s['uplinks'])
        return json.dumps([addresses, routes], sort_keys=True)

    pattern = re.compile(r'(?<![a-zA-Z0-9_.-])(?:' + '|'.join(map(re.escape, s['uplinks'])) + r')(?=[ :@]|$)')
    previous = configuration()
    with subprocess.Popen([s['ip'], '-o', 'monitor', 'link', 'address', 'route'], stdout=subprocess.PIPE) as process:
        pending, data = None, b''
        while True:
            timeout = None if pending is None else max(0, pending - time.monotonic())
            ready, _, _ = select.select([process.stdout], [], [], timeout)
            if ready:
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise ValueError('physical network observer exited')
                data += chunk
                while b'\n' in data:
                    line, data = data.split(b'\n', 1)
                    if pattern.search(line.decode(errors='replace')):
                        pending = time.monotonic() + .7
                if len(data) > 65536:
                    raise ValueError('oversized physical network observation')
            if pending is not None and time.monotonic() >= pending:
                pending = None
                current = configuration()
                if current != previous:
                    previous = current
                    renew(s)


def main():
    s = json.loads(Path(sys.argv[1]).read_text())
    action = sys.argv[2]
    if action == 'render':
        render(s, sys.argv[3])
    elif action == 'setup':
        setup(s, sys.argv[3])
    elif action == 'teardown':
        teardown(s)
    elif action in ('routes-on', 'routes-off'):
        routes(s, action == 'routes-on')
    elif action == 'observe':
        observe(s)
    elif action == 'watch':
        watch(s)
    elif action == 'resume':
        renew(s)
    else:
        raise ValueError('unknown fixed core operation')


if __name__ == '__main__':
    main()
