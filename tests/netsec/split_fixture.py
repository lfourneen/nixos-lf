"""Exercise the real DNS lease broker and bound dnsmasq upstream in the VM."""
import json
from pathlib import Path
import subprocess
import time

if not Path('/etc/netsec-mode-b.nft').exists():
    raise SystemExit('VM fixture only')
policy = ['python3', '/etc/netsec-policy.py', '/etc/netsec-split.json']
active = Path('/run/split-active')
request_path = Path('/run/dns-upstream/policy.json')
servers = Path('/run/dns-upstream/servers.conf')
original = servers.read_text()


def run(argv):
    return subprocess.run(argv, capture_output=True, text=True, check=True).stdout


def publish(age=0):
    split = json.loads(run(policy + ['--split']))
    split['observed_at'] -= age
    state = json.loads(Path('/run/proxy-mode/state.json').read_text())
    request_path.write_text(json.dumps({'policy_version': state['version'],
        'monotonic_seconds': time.clock_gettime(time.CLOCK_BOOTTIME), 'split': split}))
    run(policy)


def query(ok=None):
    result = subprocess.run(['python3', '/etc/netsec-dns.py', 'query', '10.1.2.53', '--uid', '985'], capture_output=True)
    if ok is not None:
        assert (result.returncode == 0) == ok, result.stderr
    return result.returncode == 0


try:
    active.write_text('1')
    publish()
    query(True)
    # A live concatenated lease must be accepted by the actual drift checker.
    snapshot = Path('/tmp/split-rules.json')
    snapshot.write_text(run(['nft', '-j', 'list', 'ruleset']))
    run(['/etc/netsec-rules-check', 'compare', '/etc/netsec-expected/direct.json', str(snapshot)])
    # The actual dnsmasq process must be able to bind the upstream to wlo1.
    servers.write_text('server=127.0.0.1#1055\nserver=/corp.test/10.1.2.53@wlo1\n')
    run(['systemctl', 'reload', 'dnsmasq.service'])
    answer = run(['dig', '+time=1', '+tries=1', '+short', '@127.0.0.1', '-p', '1054', 'probe.corp.test', 'A'])
    assert answer.strip() == '192.0.2.99', answer

    run(['ip', 'route', 'add', '10.1.2.53/32', 'via', '100.67.0.1', 'dev', 'ens1'])
    query(False)  # Same destination, wrong interface must not match the tuple.
    run(['ip', 'route', 'del', '10.1.2.53/32'])
    query(True)

    active.write_text('2')
    run(policy)  # A fresh outer request cannot regrant the old active instance.
    query(False)
    assert json.loads(Path('/run/dns-policy/state.json').read_text())['status'] == 'stale-network'
    publish()
    query(True)
    run(['systemctl', 'start', 'dns-split-revoke.service'])
    query(False)
    for _ in range(6):
        run(['systemctl', 'start', 'dns-split-revoke.service'])
    # The revoke path flushes the lease sets with bare flush statements rather
    # than element deletions, so assert the observable result of that route:
    # both sets empty, the epoch rotated, and the recorded status revoked.
    epoch = Path('/run/dns-policy/split-epoch').read_text()
    run(['systemctl', 'start', 'dns-split-revoke.service'])
    assert Path('/run/dns-policy/split-epoch').read_text() != epoch, 'revoke did not rotate the epoch'
    for name in ('dns_split4', 'dns_split6'):
        assert 'elements' not in run(['nft', 'list', 'set', 'inet', 'lf_filter', name]), name + ' kept a lease'
    assert json.loads(Path('/run/dns-policy/state.json').read_text())['status'] == 'revoked'
    run(policy)  # The same NM instance is insufficient after epoch revocation.
    query(False)
    # Leave enough time for broker execution, then require expiry well before
    # a fresh 120-second lease: the original observation must remain binding.
    publish(105)
    query(True)
    deadline = time.monotonic() + 30
    while query():
        assert time.monotonic() < deadline, 'split lease exceeded its original deadline'
        time.sleep(0.2)
    query(False)
    print('Bound split DNS, uplink migration, active-instance/epoch revocation and original deadline passed')
finally:
    subprocess.run(['ip', 'route', 'del', '10.1.2.53/32'], capture_output=True)
    servers.write_text(original)
    subprocess.run(['systemctl', 'reload', 'dnsmasq.service'], capture_output=True)
    state = json.loads(Path('/run/proxy-mode/state.json').read_text())
    request_path.write_text(json.dumps({'policy_version': state['version'],
        'monotonic_seconds': time.clock_gettime(time.CLOCK_BOOTTIME), 'split': {'entries': []}}))
    subprocess.run(['systemctl', 'start', 'dns-policy.service'], check=True)
