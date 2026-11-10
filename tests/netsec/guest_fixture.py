"""Real routed guests and user-mode network backends in the isolated core VM."""

import ipaddress
import json
import os
from pathlib import Path
import pwd
import socket
import struct
import subprocess
import sys
import threading
import time

ROOT = Path('/run/netsec-guests')
POLICY = Path('/etc/netsec-guests.json')
SCRIPT = '/etc/netsec-guests.py'


def run(*args, **kwargs):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f'{args}: exit {result.returncode}\n{result.stdout}{result.stderr}')
    return result.stdout


def wait(check, seconds=35):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.1)
    raise TimeoutError('guest fixture condition did not complete')


def signal(label, suffix):
    (ROOT / f'{label}.{suffix}').touch()


def data(label):
    return ROOT / f'{label}.json'


def probe(target, port, transport='tcp', allow=True, http=False, source=None):
    family = socket.AF_INET6 if ':' in target else socket.AF_INET
    reply = b''
    try:
        with socket.socket(family, socket.SOCK_STREAM if transport == 'tcp' else socket.SOCK_DGRAM) as stream:
            stream.settimeout(3 if allow else 0.8)
            if source:
                stream.bind((source, 0))
            stream.connect((target, port))
            stream.sendall(b'GET / HTTP/1.1\r\nHost: fixture\r\nConnection: close\r\n\r\n' if http else b'echo')
            reply = stream.recv(4096)
    except OSError as error:
        if allow:
            print(f'probe {target}:{port} {transport}: {error!r}', file=sys.stderr)
    ok = b' 200 ' in reply if http else reply == b'echo'
    assert ok == allow, (target, port, transport, allow, reply)


def dns(target, name, tcp=False, expected=None, nx=False, stream=None):
    question = b''.join(bytes([len(x)]) + x.encode() for x in name.split('.')) + b'\0'
    query = struct.pack('!HHHHHH', 9137, 0x0100, 1, 0, 0, 0) + question + struct.pack('!HH', 1, 1)
    owned = stream is None
    if owned:
        family = socket.AF_INET6 if ':' in target else socket.AF_INET
        stream = socket.socket(family, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM)
        stream.settimeout(2)
        stream.connect((target, 53))
    try:
        if tcp:
            stream.sendall(struct.pack('!H', len(query)) + query)
            def receive(length):
                result = b''
                while len(result) < length:
                    part = stream.recv(length-len(result))
                    assert part, 'DNS TCP stream closed'
                    result += part
                return result
            result = receive(struct.unpack('!H', receive(2))[0])
        else:
            stream.send(query)
            result = stream.recv(65535)
        header = struct.unpack('!HHHHHH', result[:12])
        assert header[0] == 9137 and header[1] & 15 == (3 if nx else 0), header
        if expected is not None:
            assert socket.inet_aton(expected) in result and not b'\xc6\x12' in result[-4:], result
        return result
    finally:
        if owned:
            stream.close()


def hold(label, target, port, transport='tcp'):
    family = socket.AF_INET6 if ':' in target else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM if transport == 'tcp' else socket.SOCK_DGRAM) as stream:
        stream.settimeout(3)
        stream.connect((target, port))
        stream.sendall(b'hold:' + label.encode())
        assert stream.recv(4096) == b'ready', label
        signal(label, 'ready')
        wait(lambda: (ROOT / f'{label}.after').exists(), 90)
        stream.settimeout(1.5)
        try:
            reply = stream.recv(4096)  # Server pushes first, without a new client packet.
        except OSError:
            reply = b''
        assert not reply, (label, 'revoked reply remained usable', reply)
        try:
            stream.sendall(b'after')
            reply = stream.recv(4096)
        except OSError:
            reply = b''
        assert not reply, (label, 'revoked original direction remained usable')
        signal(label, 'blocked')


def serve():
    config = json.loads(POLICY.read_text())
    outside = sys.argv[2] == 'outside'
    addresses = ([config[k] for k in ('private4', 'other4', 'private6', 'public4', 'public6')] if outside
                 else ['127.0.0.1', '::1'] + [g for n in config['networks'] for g in n['gateway4']+n['gateway6']])

    def push(label, send):
        wait(lambda: (ROOT / f'{label}.push').exists(), 90)
        try:
            send(b'pushed')
        except OSError:
            pass

    def tcp_client(connection):
        with connection:
            connection.settimeout(90)
            while request := connection.recv(4096):
                if request.startswith(b'hold:'):
                    label = request[5:].decode()
                    connection.sendall(b'ready')
                    push(label, connection.sendall)
                else:
                    connection.sendall(request)

    def tcp_server(family, address):
        listener = socket.socket(family, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        listener.bind((address, 18081)); listener.listen()
        while True:
            connection, _ = listener.accept()
            threading.Thread(target=tcp_client, args=(connection,), daemon=True).start()

    def udp_server(address):
        family = socket.AF_INET6 if ':' in address else socket.AF_INET
        with socket.socket(family, socket.SOCK_DGRAM) as stream:
            stream.bind((address, 18082))
            while True:
                request, peer = stream.recvfrom(4096)
                if request.startswith(b'hold:'):
                    label = request[5:].decode()
                    stream.sendto(b'ready', peer)
                    threading.Thread(target=push, args=(label, lambda value, peer=peer: stream.sendto(value, peer)), daemon=True).start()
                else:
                    stream.sendto(request, peer)

    for family, address in ((socket.AF_INET, '0.0.0.0'), (socket.AF_INET6, '::')):
        threading.Thread(target=tcp_server, args=(family, address), daemon=True).start()
    for address in addresses:
        threading.Thread(target=udp_server, args=(address,), daemon=True).start()
    signal('outside' if outside else 'host', 'server')
    threading.Event().wait()


def ns(iface, *args):
    return run('ip', 'netns', 'exec', 'guest-'+iface, *args)


def prepare():
    config = json.loads(POLICY.read_text())
    ROOT.mkdir(mode=0o777, exist_ok=True); ROOT.chmod(0o777)
    for user in ('lfour', 'guestnet'):
        entry = pwd.getpwnam(user)
        runtime = Path(f'/run/user/{entry.pw_uid}')
        runtime.mkdir(parents=True, exist_ok=True)
        os.chown(runtime, entry.pw_uid, entry.pw_gid); runtime.chmod(0o700)
    run('ip', '-n', 'outside', 'addr', 'add', config['other4']+'/32', 'dev', 'peer0')
    run('ip', '-n', 'outside', '-6', 'addr', 'add', config['private6']+'/128', 'dev', 'peer0', 'nodad')
    run('ip', '-6', 'route', 'add', 'fd42:feed::/64', 'via', config['public6'], 'dev', 'ens1')
    for index, role in enumerate(config['networks']):
        iface = role['interface']; namespace = 'guest-'+iface; device = 'gclient'+str(index)
        run('ip', 'link', 'add', iface, 'type', 'bridge')
        for address in role['gateway4']+role['gateway6']:
            prefix = next(ipaddress.ip_network(cidr).prefixlen for cidr in role['source4']+role['source6'] if ipaddress.ip_address(address) in ipaddress.ip_network(cidr))
            run('ip', 'addr', 'add', f'{address}/{prefix}', 'dev', iface, *(['nodad'] if ':' in address else []))
        run('ip', 'link', 'set', iface, 'up')
        run('ip', 'netns', 'add', namespace)
        run('ip', 'link', 'add', 'gv'+str(index), 'type', 'veth', 'peer', 'name', device)
        run('ip', 'link', 'set', 'gv'+str(index), 'master', iface)
        run('ip', 'link', 'set', 'gv'+str(index), 'up')
        run('ip', 'link', 'set', device, 'netns', namespace)
        run('ip', '-n', namespace, 'link', 'set', device, 'up')
        run('ip', '-n', namespace, 'link', 'set', 'lo', 'up')
        for cidr, gateway in [(role['source4'][0], role['gateway4'][0]), (role['source6'][0], role['gateway6'][0])]:
            subnet = ipaddress.ip_network(cidr)
            address = subnet.network_address + 2
            run('ip', '-n', namespace, 'addr', 'add', f'{address}/{subnet.prefixlen}', 'dev', device, *(['nodad'] if subnet.version == 6 else []))
            run('ip', '-n', namespace, '-6' if subnet.version == 6 else '-4', 'route', 'add', 'default', 'via', gateway)
            run('ip', '-n', 'outside', '-6' if subnet.version == 6 else '-4', 'route', 'add', cidr, 'via', '2001:db8:dead::2' if subnet.version == 6 else '100.67.0.2', 'dev', 'peer0')
        subnet = ipaddress.ip_network(role['source4'][0])
        run('systemd-run', '--unit=guest-dns-'+str(index), config['dnsmasq'], '--keep-in-foreground', '--conf-file=/dev/null',
            '--pid-file=', '--bind-interfaces', '--interface='+iface, '--except-interface=lo', '--listen-address='+role['gateway4'][0], '--no-resolv', '--no-hosts',
            '--server=127.0.0.1#1054', f'--dhcp-range={subnet.network_address+10},{subnet.network_address+20},1h')
    run('systemd-run', '--unit=guest-host-echo', config['python'], SCRIPT, 'serve', 'host')
    run('systemd-run', '--unit=guest-outside-echo', 'ip', 'netns', 'exec', 'outside', config['python'], SCRIPT, 'serve', 'outside')
    wait(lambda: (ROOT/'host.server').exists() and (ROOT/'outside.server').exists())
    print('Three disjoint routed guest networks and dual-stack endpoints prepared')


def policy(name):
    run('flock', '/run/proxy-mode/lock', 'nft', '-f', '/etc/netsec-guests-'+name+'.nft')


def actor(*args):
    return run('runuser', '-u', 'guestnet', '--', *args)


def start_backend(kind, user, label, probes=None, holds=None, check_dns=False):
    config = json.loads(POLICY.read_text())
    data(label).write_text(json.dumps({'probes': probes or [], 'holds': holds or [], 'dns': check_dns, 'kind': kind}))
    entry = pwd.getpwnam(user)
    command = [config['python'], SCRIPT, 'backend-client', label]
    if kind == 'pasta':
        backend = [config['pasta'], '--config-net', '-I', 'ens1', '--no-splice', '--no-map-gw',
                   '--map-host-loopback', '169.254.1.2', '--dns-forward', '169.254.1.53', '--dns-host', '127.0.0.53',
                   '-t', 'none', '-u', 'none', '-T', 'none', '-U', 'none', '--', *command]
    else:
        backend = [config['rootlesskit'], '--state-dir='+str(ROOT/('state-'+label)),
                   '--net='+('host' if kind == 'host-subuid' else 'slirp4netns'), '--copy-up=/etc']
        if kind != 'host-subuid': backend.append('--disable-host-loopback')
        backend += command
    run('systemd-run', '--unit=backend-'+label, '--uid='+user, '--gid='+str(entry.pw_gid),
        '--setenv=XDG_RUNTIME_DIR=/run/user/'+str(entry.pw_uid), '--setenv=PATH='+config['backendPath'], *backend)
    return label


def backend_client(label):
    instructions = json.loads(data(label).read_text())
    if instructions['kind'] == 'host-subuid': os.setuid(1)
    for entry in instructions['probes']:
        probe(**entry)
    if instructions['dns']:
        target = {'pasta': '169.254.1.53', 'rootlesskit': '10.0.2.3', 'host-subuid': '127.0.0.53'}[instructions['kind']]
        for tcp in (False, True):
            dns(target, 'public.guest.fixture.example', tcp, expected='203.0.113.10')
            dns(target, 'blocked.guest.fixture.example', tcp, nx=True)
    failures = []
    def session(entry):
        try:
            hold(**entry)
        except Exception as error:
            failures.append(repr(error))
    threads = [threading.Thread(target=session, args=(entry,)) for entry in instructions['holds']]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    if failures: raise AssertionError(failures)
    signal(label, 'done')


def complete(label):
    def ready():
        result = subprocess.run(['systemctl', 'is-failed', '--quiet', 'backend-'+label], capture_output=True)
        if result.returncode == 0:
            raise AssertionError(run('journalctl', '-u', 'backend-'+label, '--no-pager', '-n', '25'))
        return (ROOT/(label+'.done')).exists()
    wait(ready, 45)


def counter(chain):
    document = json.loads(run('nft', '-j', 'list', 'chain', 'inet', 'lf_filter', chain))
    return sum(expression.get('counter', {}).get('packets', 0)
               for item in document['nftables'] for expression in item.get('rule', {}).get('expr', []))


def stale_dns_client():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
        stream.settimeout(2)
        stream.connect(('127.0.0.53', 53))
        initial = dns('', 'stale.guest.fixture.example', stream=stream)
        assert b'\xc6\x12' in initial[-4:], initial
        signal('stale-dns', 'ready')
        wait(lambda: (ROOT/'stale-dns.after').exists())
        try:
            dns('', 'stale.guest.fixture.example', stream=stream)
        except OSError:
            signal('stale-dns', 'blocked')
        else:
            raise AssertionError('stale DNS conntrack bypassed the real-address resolver')


def stale_dns():
    # Model a previously allowed B fake-IP DNS association, then install the
    # current complete policy while preserving that exact UDP socket/tuple.
    run('nft', 'add', 'table', 'ip', 'guest_stale_dns')
    run('nft', 'add', 'chain', 'ip', 'guest_stale_dns', 'output',
        '{ type nat hook output priority -191; policy accept; }')
    run('nft', 'add', 'rule', 'ip', 'guest_stale_dns', 'output',
        'meta', 'skuid', '1100', 'udp', 'dport', '53', 'redirect', 'to', ':1054')
    run('nft', 'flush', 'chain', 'inet', 'lf_filter', 'lf_socket_guest_dns_guard')
    run('nft', 'flush', 'chain', 'inet', 'lf_filter', 'lf_socket_guest_input')
    config = json.loads(POLICY.read_text())
    run('systemd-run', '--unit=guest-stale-dns', '--uid=guestnet', '--gid=guestnet',
        config['python'], SCRIPT, 'stale-dns-client')
    wait(lambda: (ROOT/'stale-dns.ready').exists())
    run('nft', 'delete', 'table', 'ip', 'guest_stale_dns')
    policy('base-b')
    before = counter('lf_socket_guest_dns_after_nat')
    signal('stale-dns', 'after')
    wait(lambda: (ROOT/'stale-dns.blocked').exists(), 8)
    assert counter('lf_socket_guest_dns_after_nat') > before


def direct():
    config = json.loads(POLICY.read_text())
    run(config['listenerCheck'], 'socket-guest-dns')
    actor(config['python'], SCRIPT, 'dns-test')
    for index, role in enumerate(config['networks']):
        iface = role['interface']; device = 'gclient'+str(index)
        ns(iface, config['python'], SCRIPT, 'probe', config['public4'], '8080', 'tcp', 'yes', 'http')
        allowed = iface != 'ap0'
        for target in (config['private4'], config['private6']):
            for transport, port in (('tcp', 18081), ('udp', 18082)):
                ns(iface, config['python'], SCRIPT, 'probe', target, port, transport, 'yes' if allowed else 'no')
        ns(iface, config['python'], SCRIPT, 'probe', config['public6'], '18081', 'tcp', 'no')
        # All three roles keep DHCP/DNS, without granting arbitrary host ports.
        for gateway in role['gateway4'] + role['gateway6']:
            ns(iface, config['python'], SCRIPT, 'gateway-dns', gateway)
        ns(iface, 'ip', 'addr', 'del', str(ipaddress.ip_network(role['source4'][0]).network_address+2)+'/24', 'dev', device)
        ns(iface, config['dhcpcd'], '-B', '-1', '-4', '-t', '20', '--config', '/etc/netsec-guests-dhcpcd.conf', device)
        ns(iface, config['python'], SCRIPT, 'probe', config['public4'], '8080', 'tcp', 'yes', 'http')
    for transport, port in (('tcp', 18081), ('udp', 18082)):
        actor(config['python'], SCRIPT, 'probe', config['private6'], port, transport, 'yes')
    before = counter('lf_socket_guest_private')
    for target in ('169.254.169.254', '100.100.100.200', 'fd00:ec2::254', '64:ff9b::a9fe:a9fe', '198.18.255.254'):
        actor(config['python'], SCRIPT, 'probe', target, '8080', 'tcp', 'no')
    assert counter('lf_socket_guest_private') > before, 'unsafe targets never reached the UID guard'
    probes = [{'target': config['public4'], 'port': 18081}, {'target': config['private4'], 'port': 18081},
              {'target': config['other4'], 'port': 18081, 'allow': False}]
    for kind in ('pasta', 'rootlesskit'):
        complete(start_backend(kind, 'guestnet', 'a-'+kind, probes, check_dns=True))
    print('A public access, private tiers, IPv6 refusal, DHCP and real backend DNS passed')


def dns_test():
    for target in ('127.0.0.53', '203.0.113.10', '2001:db8:dead::1'):
        for tcp in (False, True):
            print(f'Forced DNS: {target}, tcp={tcp}', flush=True)
            dns(target, 'public.guest.fixture.example', tcp, expected='203.0.113.10')
            dns(target, 'blocked.guest.fixture.example', tcp, nx=True)


def proxy():
    config = json.loads(POLICY.read_text())
    # Stop only the VM's coordinator while loading declared policy variants;
    # live health reconciliation must not immediately repair a deliberate test.
    run('systemctl', 'mask', '--runtime', '--now', 'proxy-mode.service')
    # Stopping the coordinator can race the core restart. A probe sent before
    # the TPROXY listener exists is refused and never retried, so wait for the
    # declared listener rather than sleeping a fixed amount.
    for _ in range(50):
        if ':7896' in run('ss', '-lntH'):
            break
        time.sleep(0.2)
    else:
        raise RuntimeError('mihomo TPROXY listener did not become ready')
    def tproxy_diagnostics():
        print('--- TPROXY diagnostics ---', flush=True)
        for command in (
            ('ss', '-lntup'),
            ('nft', '-a', 'list', 'chain', 'ip', 'lf_mangle', 'prerouting'),
            ('nft', '-a', 'list', 'chain', 'ip', 'lf_mangle', 'proxymode_tproxy'),
            ('nft', '-a', 'list', 'chain', 'inet', 'lf_filter', 'lf_guest_guard'),
            ('nft', '-a', 'list', 'table', 'ip', 'lf_nat'),
            ('nft', '-a', 'list', 'chain', 'inet', 'lf_filter', 'proxymode_forward'),
            ('nft', '-a', 'list', 'chain', 'inet', 'lf_filter', 'lf_guest_from_0'),
            ('nft', '-a', 'list', 'chain', 'inet', 'lf_filter', 'lf_guest_host_0'),
            ('ip', 'rule'),
            ('ip', 'route', 'show', 'table', '100'),
            ('journalctl', '-u', 'clash-verge', '--no-pager', '-n', '80'),
            ('ss', '-tnp'),
        ):
            try:
                print(run(*command), flush=True)
            except Exception as error:  # noqa: BLE001 - diagnostic path only
                print(f'diagnostic {command} failed: {error}', flush=True)
        for iface, device in (('virbr0', 'gclient0'), ('waydroid0', 'gclient1'), ('ap0', 'gclient2')):
            for command in (('ip', '-4', 'addr', 'show', 'dev', device), ('ip', 'route', 'show')):
                try:
                    print(iface, run('ip', 'netns', 'exec', 'guest-'+iface, *command), flush=True)
                except Exception as error:  # noqa: BLE001
                    print(f'diagnostic {iface} {command} failed: {error}', flush=True)

    for role in config['networks']:
        iface = role['interface']
        try:
            ns(iface, config['python'], SCRIPT, 'probe', config['public4'], '8080', 'tcp', 'yes' if iface == 'virbr0' else 'no', 'http')
        except Exception:
            tproxy_diagnostics()
            raise
        ns(iface, config['python'], SCRIPT, 'probe', config['public6'], '18081', 'tcp', 'no')
        ns(iface, config['python'], SCRIPT, 'probe', config['public4'], '18082', 'udp', 'yes' if iface == 'virbr0' else 'no')
        ns(iface, config['python'], SCRIPT, 'probe', config['other4'], '18081', 'tcp', 'yes' if iface == 'virbr0' else 'no')
        ns(iface, config['python'], SCRIPT, 'probe', role['gateway4'][0], '18081', 'tcp', 'yes' if iface == 'virbr0' else 'no')
    # A spoofed source in the trusted network cannot borrow that network's role.
    ns('waydroid0', 'ip', 'addr', 'add', '192.168.122.77/32', 'dev', 'gclient1')
    before = counter('lf_guest_from_1')
    ns('waydroid0', config['python'], SCRIPT, 'probe-source', config['private4'], '192.168.122.77')
    assert counter('lf_guest_from_1') > before, 'spoofed source bypassed its interface role'
    # Guest fake-IP needs the explicit B TPROXY inlet and a live mapping.
    # The DNS coordinator can reload the gateway independently of the stopped
    # mode coordinator. Wait for an actual synthetic answer before testing its
    # data path; no failed/empty response counts as successful discovery.
    fake = None
    def mapped():
        nonlocal fake
        try:
            result = ns('virbr0', 'dig', '+short', '+time=2', '+tries=1', '@192.168.122.1', 'fake.guest.fixture.example', 'A')
        except RuntimeError:
            return False
        fake = next((x for x in result.splitlines() if x.startswith('198.18.')), None)
        return fake is not None
    try:
        wait(mapped, 15)
    except Exception:
        tproxy_diagnostics()
        print(run('journalctl', '-u', 'guest-dns-0', '-u', 'dnsmasq', '-u', 'dns-upstream', '--no-pager', '-n', '35'), flush=True)
        raise
    ns('virbr0', config['python'], SCRIPT, 'probe', fake, '8080', 'tcp', 'yes', 'http')
    ns('waydroid0', config['python'], SCRIPT, 'probe', fake, '8080', 'tcp', 'no', 'http')
    probes = [{'target': config['public4'], 'port': 18081}, {'target': config['public4'], 'port': 18082, 'transport': 'udp'},
              {'target': config['private4'], 'port': 18081}, {'target': config['private4'], 'port': 18082, 'transport': 'udp'},
              {'target': config['other4'], 'port': 18081, 'allow': False},
              {'target': config['private4'], 'port': 8080, 'allow': False, 'http': True},
              {'target': fake, 'port': 8080, 'allow': False, 'http': True}]
    for kind in ('pasta', 'rootlesskit', 'host-subuid'):
        complete(start_backend(kind, 'guestnet', 'b-'+kind, probes, check_dns=True))
        complete(start_backend(kind, 'lfour', 'trusted-'+kind, [{'target': config['other4'], 'port': 18081}]))
    # Even a host-side explicit proxy would lose the UID at the relay, so its
    # loopback inlet is refused to the restricted backend identity.
    assert subprocess.run(['runuser', '-u', 'guestnet', '--', 'curl', '--noproxy', '', '-x', 'http://127.0.0.1:33332', '-fsS', '--max-time', '2', 'http://203.0.113.10:8080/'], capture_output=True).returncode != 0
    policy('trust-ap')
    ns('ap0', config['python'], SCRIPT, 'probe', config['private4'], '18081', 'tcp', 'yes')
    sessions = []
    for role in config['networks']:
        iface = role['interface']
        for transport, port in (('tcp', 18081), ('udp', 18082)):
            for six in (False, True):
                label = f'{iface}-{transport}-{six}'
                target = config['private6'] if six else config['private4']
                run('systemd-run', '--unit=hold-'+label, 'ip', 'netns', 'exec', 'guest-'+iface, config['python'], SCRIPT, 'hold', label, target, port, transport)
                sessions.append(label)
    host_label = 'host-service'
    run('systemd-run', '--unit=hold-host-service', 'ip', 'netns', 'exec', 'guest-virbr0', config['python'], SCRIPT, 'hold', host_label, '192.168.122.1', 18081, 'tcp')
    sessions.append(host_label)
    backends = []
    for kind in ('pasta', 'rootlesskit', 'host-subuid'):
        entries = []
        for transport, port in (('tcp', 18081), ('udp', 18082)):
            label = kind+'-private-'+transport
            sessions.append(label)
            entries.append({'label': label, 'target': config['private4'], 'port': port, 'transport': transport})
        backends.append(start_backend(kind, 'guestnet', 'hold-'+kind, holds=entries))
    wait(lambda: all((ROOT/(label+'.ready')).exists() for label in sessions), 45)
    policy('revoke')
    for label in sessions: signal(label, 'after'); signal(label, 'push')
    wait(lambda: all((ROOT/(label+'.blocked')).exists() for label in sessions), 12)
    for label in backends: complete(label)
    # Public access survives private revocation, and a new denied connection
    # cannot borrow a stale established flow from another interface or port.
    ns('virbr0', config['python'], SCRIPT, 'probe', config['public4'], '8080', 'tcp', 'yes', 'http')
    ns('waydroid0', config['python'], SCRIPT, 'probe', config['private4'], '18081', 'tcp', 'no')
    actor(config['python'], SCRIPT, 'probe', config['private4'], '18081', 'tcp', 'no')
    policy('base-b')
    # C must also cut already proxied guest replies, while the real core stays
    # alive for root recovery. No stop/crash substitutes for this data check.
    label = 'c-tproxy'
    ns('virbr0', config['python'], SCRIPT, 'probe', config['public4'], '18081', 'tcp', 'yes')
    run('systemd-run', '--unit=hold-c-tproxy', 'ip', 'netns', 'exec', 'guest-virbr0', config['python'], SCRIPT, 'hold', label, config['public4'], 18081, 'tcp')
    entries = [{'label': 'c-socket', 'target': config['public4'], 'port': 18081}]
    c_backend = start_backend('rootlesskit', 'guestnet', 'c-rootless', holds=entries)
    wait(lambda: (ROOT/(label+'.ready')).exists() and (ROOT/'c-socket.ready').exists(), 30)
    policy('blocked')
    for name in (label, 'c-socket'): signal(name, 'after'); signal(name, 'push')
    wait(lambda: (ROOT/(label+'.blocked')).exists() and (ROOT/'c-socket.blocked').exists(), 10)
    complete(c_backend)
    ns('virbr0', config['python'], SCRIPT, 'probe', config['public4'], '8080', 'tcp', 'no', 'http')
    actor(config['python'], SCRIPT, 'probe', config['public4'], '8080', 'tcp', 'no', 'http')
    for role in config['networks']:
        for gateway in role['gateway4'] + role['gateway6']:
            ns(role['interface'], config['python'], SCRIPT, 'gateway-dns', gateway)
    run('curl', '--noproxy', '', '--socks5-hostname', '127.0.0.1:7897', '-fsS', '--max-time', '3', 'http://203.0.113.10:8080/')
    policy('base-b')
    stale_dns()
    actor(config['python'], SCRIPT, 'dns-test')
    print('B/C routed, real pasta/RootlessKit, fake-IP, UID scopes and bidirectional established revocation passed')


if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'probe':
        probe(sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5] == 'yes', len(sys.argv) > 6)
    elif action == 'probe-source':
        probe(sys.argv[2], 18081, source=sys.argv[3], allow=False)
    elif action == 'hold':
        hold(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5])
    elif action == 'backend-client':
        backend_client(sys.argv[2])
    elif action == 'stale-dns-client':
        stale_dns_client()
    elif action == 'dns-test':
        dns_test()
    elif action == 'gateway-dns':
        for tcp in (False, True):
            dns(sys.argv[2], 'public.guest.fixture.example', tcp, expected='203.0.113.10')
    else:
        {'prepare': prepare, 'serve': serve, 'direct': direct, 'proxy': proxy}[action]()
