"""VM only: actual TLS fetch/broker/stage and actual core/namespace byte paths.

The small readiness fixture deliberately isolates relay revocation latency.
Full supervisor A/B/C semantics remain covered by the existing control VMs.
"""
import grp
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
import shutil
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading
import time

s = json.loads(Path(sys.argv[1]).read_text())

def command(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=15, **kwargs)

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m

command('ip', 'addr', 'add', '1.1.1.1/32', 'dev', 'lo')
command('ip', 'addr', 'add', '1.0.0.1/32', 'dev', 'lo')
policy = {'lf-private-targets': {'prefixes': ['10.0.0.0/8', '192.168.0.0/16', '127.0.0.0/8'],
          'subscription-trust': 'fixed', 'strict-inlets': ['lf-strict']},
          'tun.enable': False, 'mixed-port': 7897, 'rule-providers': {},
          'listeners': [{'name': 'lf-strict', 'type': 'mixed', 'listen': '127.0.0.1', 'port': 7898, 'udp': False}]}
Path('/run/fixture-policy.json').write_text(json.dumps(policy))
source = {'proxies': [{'name':'remote', 'type':'http', 'server':'1.0.0.1', 'port':18081}],
          'proxy-groups':[{'name':'P', 'type':'select', 'proxies':['remote', 'DIRECT']}], 'rules':['MATCH,P']}
alternatives = {}
for identity, kind, server in [('second', 'url-test', '8.8.8.8'), ('third', 'fallback', '9.9.9.9')]:
    alternatives['/' + identity] = {
        'proxies': [dict(source['proxies'][0], server=server)],
        'proxy-groups': [{'name': 'P', 'type': kind, 'proxies': ['remote'],
                          'url': 'https://privacy-fixture.invalid/204', 'interval': 300, 'tolerance': 50}],
        'rules': ['MATCH,P']}
class Subscription(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (json.dumps(alternatives.get(self.path, source)).encode()
                if self.headers.get('User-Agent') == 'clash.meta' else b'dHJvamFuOi8vZXhhbXBsZQ==')
        self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *args): pass
origin = ThreadingHTTPServer(('1.1.1.1', 443), Subscription)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(Path(s['tls'])/'trusted.pem', Path(s['tls'])/'trusted.key')
origin.socket = context.wrap_socket(origin.socket, server_side=True)
threading.Thread(target=origin.serve_forever, daemon=True).start()
bs = dict(root='/var/lib/clash-source', policy='/run/fixture-policy.json', forced_domains=[], assets={},
          python=s['python'], worker=s['worker'], setpriv=s['setpriv'], ca=s['tls']+'/ca.pem', stageSettings='/run/stage-settings.json',
          gui_index='/run/gui-profiles.yaml', desktop_uid=0)
Path('/run/broker-settings.json').write_text(json.dumps(bs))
registry = Path('/run/source-definition.json')
registry.write_text(json.dumps({'id':'fixture', 'subscription':{'url':'https://privacy-fixture.invalid/sub'}, 'providers':{}}))
registry.chmod(0o600)
def broker(*args, okay=True):
    result = subprocess.run([s['python'], s['broker'], '/run/broker-settings.json', *args], capture_output=True, text=True, timeout=60)
    if okay and result.returncode and args == ('refresh',):
        # Fixture-only diagnostics bypass the production secret-free error
        # wrapper. All URLs/credentials here are synthetic and stay in the VM.
        registry_data = json.loads(Path(bs['root'], 'selected/registry.json').read_text())
        request = json.dumps({'registry':registry_data, 'policy':policy, 'forced_domains':[]}).encode()
        code = 'import runpy; runpy.run_path(' + repr(s['worker']) + ')["main"]()'
        probe = subprocess.run([s['setpriv'], '--reuid=clash-fetch', '--regid=clash-fetch', '--clear-groups',
            '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all', '--no-new-privs',
            s['python'], '-I', '-c', code], input=request, capture_output=True,
            env={'PATH':'','SSL_CERT_FILE':bs['ca']}, timeout=55)
        print('fixture worker diagnostic:', probe.returncode, probe.stderr.decode(), flush=True)
        if probe.returncode == 0:
            module('broker_debug', s['broker']).refresh(bs, Path(bs['root'], 'selected').resolve())
    assert (result.returncode == 0) == okay, (result.returncode, result.stdout, result.stderr)
    return result.stdout
review = json.loads(broker('review', str(registry)))
broker('register', str(registry), '--digest', '0'*64, '--previous', 'none', okay=False)
broker('register', str(registry), '--digest', review['digest'], '--previous', 'none')
broker('refresh')
current = Path('/var/lib/clash-source/selected/current').resolve()
assert json.loads((current/'source-manifest.json').read_text())['revision'] == review['digest']
# Wrong SNI/CA never reaches the source handler; old candidate remains intact.
registry.write_text(json.dumps({'id':'fixture', 'subscription':{'url':'https://1.1.1.1/sub'}, 'providers':{}}))
review2 = json.loads(broker('review', str(registry)))
broker('register', str(registry), '--digest', review2['digest'], '--previous', review['digest'])
broker('refresh', okay=False)
assert not Path('/var/lib/clash-source/selected/current').exists()
# Explicit restoration needs the current revision; no silent TOFU after reboot.
registry.write_text(json.dumps({'id':'fixture', 'subscription':{'url':'https://privacy-fixture.invalid/sub'}, 'providers':{}}))
broker('register', str(registry), '--digest', review['digest'], '--previous', review2['digest'])
broker('refresh')
stage = module('stage', s['stage'])
state = Path('/run/source-test-state'); state.mkdir()
(state/'intent').write_text('proxy')
(state/'boot-id').write_text(Path('/proc/sys/kernel/random/boot_id').read_text())
(state/'authorize-window').touch()
runtime = Path('/run/source-test-runtime'); runtime.mkdir()
data = Path('/run/source-test-data'); data.mkdir(); (data/'proxies').mkdir(); (data/'cache.db').touch()
gui = Path('/tmp/forged-gui'); gui.mkdir(); (gui/'clash-verge.yaml').write_text('mode: direct\n')
st = dict(runtime=str(runtime), state=str(state), core_gid=982, refresh_policy='auto',
          source_root='/var/lib/clash-source', gui_data=str(gui), desktop_uid=1000, data=str(data),
          audit_policy='enforce', audit_python=s['python'], audit_tool=s['audit'],
          audit_settings='/run/audit-settings.json', setpriv=s['setpriv'], managed_policy='/run/fixture-policy.json')
Path('/run/audit-settings.json').write_text(json.dumps({'strict_semantics':True, 'subscription_trust':'fixed'}))
Path('/run/stage-settings.json').write_text(json.dumps(st))
def validate(directory):
    command(s['setpriv'], '--reuid=clash-core', '--regid=clash-core', '--clear-groups',
            s['core'], '-t', '-d', str(directory), '-f', str(directory/'clash-verge.yaml'),
            env={**os.environ, 'LF_MANAGED_PROXY_POLICY':'/run/fixture-policy.json'})
stage.stage(st, str(gui/'clash-verge.yaml'), validate)
assert 'mode: rule' in (runtime/'effective/clash-verge.yaml').read_text()
assert (runtime/'effective/source-manifest.json').is_file()
baseline = (state/'authorized-config').read_text().strip()
# Exercise persistence explicitly; full health settling is in control/refresh VMs.
stage.remember_settled(runtime, state, baseline, time.time(), st)
with Path('/var/lib/clash-source/source.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    waiting = subprocess.Popen([s['python'], s['broker'], '/run/broker-settings.json', 'refresh'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(.2)
    assert waiting.poll() is None, 'refresh bypassed source.lock'
    fcntl.flock(lock, fcntl.LOCK_UN)
    output = waiting.communicate(timeout=60)
    assert waiting.returncode == 0, output
manifest_path = Path('/var/lib/clash-source/selected/current/source-manifest.json')
original_manifest = manifest_path.read_bytes()
for changed in ({'source_id': 'forged'}, {'revision': '0' * 64}):
    manifest_path.write_text(json.dumps(dict(json.loads(original_manifest), **changed)))
    try:
        stage.source_input(st)
    except ValueError:
        pass
    else:
        raise AssertionError('cross-source or stale-revision manifest accepted')
    finally:
        manifest_path.write_bytes(original_manifest)
# Three independent main sources traverse actual TLS, worker, parser and audit.
# Persistence is explicit here; production health settling is in refresh-vm.
catalog = [json.loads(registry.read_text())] + [
    {'id': identity, 'gui-profile': 'uid-' + identity,
     'subscription': {'url': 'https://privacy-fixture.invalid/' + identity}, 'providers': {}}
    for identity in ('second', 'third')]
registry.write_text(json.dumps({'sources': catalog}))
overview = json.loads(broker('review', str(registry)))
assert len(overview['sources']) == 3
assert json.loads(broker('review', str(registry), '--id', 'fixture'))['digest'] == review['digest']
broker('register', str(registry), '--digest', review['digest'], '--previous', review['digest'], okay=False)
broker('review', str(registry), '--id', 'missing', okay=False)
for identity in ('second', 'third'):
    reviewed = json.loads(broker('review', str(registry), '--id', identity))
    assert reviewed['previous'] == 'none'
    broker('register', str(registry), '--id', identity, '--digest', reviewed['digest'], '--previous', 'none')
    broker('use', identity, '--revision', '0'*64, okay=False)
    broker('use', identity, '--revision', reviewed['digest'])
    broker('refresh')
    stage.stage(st, str(gui/'clash-verge.yaml'), validate)
    actual = json.loads((runtime/'effective/source-manifest.json').read_text())
    assert actual['source_id'] == identity and actual['revision'] == reviewed['digest']
    finding = json.loads((state/'profile-refresh.json').read_text())
    assert finding['audit']['verdict'] == 'pass', finding
    stage.remember_settled(runtime, state, (state/'authorized-config').read_text().strip(), time.time(), st)
    print('TLS register/refresh/parser/audit pass and explicit baseline persisted:', identity, flush=True)
Path('/run/gui-profiles.yaml').write_text('current: forged\nitems: [{url: https://evil.invalid/}]')
broker('gui-select')
assert Path('/var/lib/clash-source/selected').resolve().name == 'third'
Path('/run/gui-profiles.yaml').write_text('current: uid-second\nitems: [{uid: uid-second, url: https://evil.invalid/}]')
broker('gui-select')
assert Path('/var/lib/clash-source/selected').resolve().name == 'second'
broker('use', 'fixture')
stage.stage(st, str(gui/'clash-verge.yaml'), validate)
assert (state/'authorized-config').read_text().strip() == baseline
stage.remember_settled(runtime, state, baseline, time.time(), st)
alternatives['/rejected'] = dict(source, hosts={'undeclared.example': '1.1.1.1'})
registry.write_text(json.dumps({'id':'rejected', 'subscription':{'url':'https://privacy-fixture.invalid/rejected'}}))
rejected_review = json.loads(broker('review', str(registry)))
broker('register', str(registry), '--digest', rejected_review['digest'], '--previous', 'none')
broker('use', 'rejected')
stage.stage(st, str(gui/'clash-verge.yaml'), validate)
assert (state/'authorized-config').read_text().strip() == baseline
assert json.loads((state/'profile-refresh.json').read_text())['result'] == 'fallback'
assert json.loads((runtime/'effective/source-manifest.json').read_text())['source_id'] == 'fixture'
broker('use', 'fixture')
broker('use', '../fixture', okay=False)
broker('use', 'missing', okay=False)
registry.write_text(json.dumps({'id':'bad', 'subscription':{'url':'https://1.1.1.1/sub'}}))
bad = json.loads(broker('review', str(registry)))
broker('register', str(registry), '--digest', bad['digest'], '--previous', 'none')
broker('use', 'bad', okay=False)
assert Path('/var/lib/clash-source/selected').resolve().name == 'fixture'
for identity in ('second', 'third', 'bad', 'rejected'):
    broker('revoke', identity)
assert Path('/var/lib/clash-source/sources/fixture/approved/settled.json').exists()
source['proxies'][0]['server'] = '8.8.8.8'
broker('refresh')
stage.stage(st, str(gui/'clash-verge.yaml'), validate)
assert (state/'authorized-config').read_text().strip() == baseline
rejection = json.loads((state/'profile-refresh.json').read_text())
assert rejection['audit']['deny']['node_identity']
broker('approve', '--digest', rejection['digest'], '--baseline', '0'*64, '--categories', 'node_identity', okay=False)
broker('approve', '--digest', rejection['digest'], '--baseline', baseline, '--categories', 'node_identity')
stage.stage(st, str(gui/'clash-verge.yaml'), validate)
assert (state/'authorized-config').read_text().strip() != baseline
assert not Path('/var/lib/clash-source/selected/approval.json').exists()
broker('approve', '--digest', rejection['digest'], '--baseline', baseline, '--categories', 'node_identity', okay=False)
# A fresh runtime must recover the last settled baseline, not trust the newest
# downloaded or merely staged generation after a reboot.
shutil.rmtree(runtime); runtime.mkdir()
shutil.rmtree(state); state.mkdir()
(state/'intent').write_text('proxy')
(state/'boot-id').write_text(Path('/proc/sys/kernel/random/boot_id').read_text())
stage.stage(st, str(gui/'clash-verge.yaml'), validate)
assert (state/'authorized-config').read_text().strip() == baseline
source['proxies'][0]['server'] = '1.0.0.1'
print('Actual TLS, root registration, migration digest, GUI forgery, exact approval and persistent baseline passed', flush=True)

# Core black-box: a strict listener refuses DIRECT even after runtime selection;
# ordinary Rule routing retains DIRECT, while the isolated client has no uplink.
raw = json.loads(json.dumps(source))
raw.update({'mixed-port':7897, 'listeners':policy['listeners'], 'tun':{'enable':False},
            'lf-private-targets':policy['lf-private-targets'], 'external-controller-unix':'/run/fixture-core.sock'})
raw['rules'] = ['IN-NAME,lf-strict,P', 'IP-CIDR,1.1.1.1/32,DIRECT,no-resolve', 'MATCH,P']
Path('/run/core.yaml').write_text(json.dumps(raw))
class Remote(BaseHTTPRequestHandler):
    label = b'remote'
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Length', str(len(self.label))); self.end_headers(); self.wfile.write(self.label)
    def do_CONNECT(self):
        if self.path != '1.1.1.1:18082':
            self.send_error(502)
            return
        self.send_response(200); self.end_headers()
        # The real HTTP outbound uses CONNECT even for an HTTP application
        # request. Return a synthetic remote response inside that tunnel.
        while self.rfile.readline(16384).strip():
            pass
        self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Length: 6\r\nConnection: close\r\n\r\nremote')
        self.wfile.flush()
        self.close_connection = True
    def log_message(self, *args): pass
class Direct(Remote): label = b'direct'
for address, cls in [(('1.0.0.1',18081),Remote),(('1.1.1.1',18082),Direct)]:
    server=ThreadingHTTPServer(address, cls); threading.Thread(target=server.serve_forever, daemon=True).start()
core = subprocess.Popen([s['core'], '-d','/run/core-state','-f','/run/core.yaml'],
                        env={**os.environ,'LF_MANAGED_PROXY_POLICY':'/run/fixture-policy.json'}, stdout=subprocess.DEVNULL)
try:
    end = time.monotonic()+10
    while not Path('/run/fixture-core.sock').exists():
        assert core.poll() is None and time.monotonic()<end
        time.sleep(.1)
    Path('/run/readiness.py').write_text("from pathlib import Path\ndef readiness(**kwargs):\n m=Path('/run/fixture-mode').read_text().strip()\n return {'mode':m,'ready':m=='proxy'}\n")
    Path('/run/fixture-mode').write_text('proxy')
    Path('/run/relay-settings.json').write_text(json.dumps(dict(stateModule='/run/readiness.py', port=7898, coreUid=0, maxAge=10, verificationMaxAge=10)))
    command('systemctl','start','fixture-relay.socket')
    command('ip','netns','add','browser'); command('ip','-n','browser','link','set','lo','up')
    target='http://1.1.1.1:18082/marker'
    client=['ip','netns','exec','browser',s['python'],s['network'],'browser','--socket','/run/fixture-relay.sock','--port','18080','--',
            'curl','--noproxy','','-fsS','--max-time','4','-x','http://127.0.0.1:18080',target]
    assert command(*client).stdout == 'remote'
    # No namespace route, including loopback destinations in the host.
    assert subprocess.run(['ip','netns','exec','browser','curl','--noproxy','*','--max-time','1',target], capture_output=True).returncode
    for mode in ['direct','blocked','unknown']:
        Path('/run/fixture-mode').write_text(mode); time.sleep(.25)
        assert subprocess.run(client, capture_output=True).returncode
    Path('/run/fixture-mode').write_text('proxy'); time.sleep(.25)
    command('curl','--unix-socket','/run/fixture-core.sock','-X','PUT','-H','Content-Type: application/json','-d','{"name":"DIRECT"}','http://localhost/proxies/P')
    assert subprocess.run(client, capture_output=True).returncode
    ordinary=command('curl','--noproxy','','-fsS','--max-time','3','-x','http://127.0.0.1:7897',target)
    assert ordinary.stdout=='direct'
    print('Actual private network namespace, B-only relay, runtime DIRECT rejection and ordinary Rule routing passed', flush=True)
finally:
    core.terminate(); core.wait(timeout=10)

# The separate portal transport reaches exactly the selected literal address;
# it does not inherit the daily browser's ordinary/strict proxy choices.
portal_origin=ThreadingHTTPServer(('1.0.0.1',80),Direct)
threading.Thread(target=portal_origin.serve_forever, daemon=True).start()
portal_socket='/run/fixture-portal.sock'
portal_proxy=subprocess.Popen([s['python'],s['network'],'portal','--socket',portal_socket,'--address','1.0.0.1'])
try:
    deadline=time.monotonic()+5
    while not Path(portal_socket).exists():
        assert portal_proxy.poll() is None and time.monotonic()<deadline
        time.sleep(.05)
    portal_client=['ip','netns','exec','browser',s['python'],s['network'],'browser',
        '--socket',portal_socket,'--port','18080','--','curl','--noproxy','','-fsS','--max-time','4',
        '-x','http://127.0.0.1:18080']
    assert command(*portal_client,'http://1.0.0.1/marker').stdout == 'direct'
    assert subprocess.run(portal_client+['http://1.1.1.1/forbidden'],capture_output=True).returncode
    assert subprocess.run(portal_client+['http://1.0.0.1:18082/forbidden'],capture_output=True).returncode
    print('Separate portal namespace transport accepted only its selected address and ports',flush=True)
finally:
    portal_proxy.terminate(); portal_proxy.wait(timeout=5)
