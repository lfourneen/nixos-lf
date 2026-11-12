"""VM-only refresh, refusal and recovery tests using the real pinned helper."""
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import yaml

GUI = Path('/home/lfour/.local/share/io.github.clash-verge-rev.clash-verge-rev')
CONFIG = GUI / 'clash-verge.yaml'
STATE = Path('/run/proxy-mode')
SOCKET = '/run/clash-verge-rev/service.sock'
MAGIC = 'A thing of beauty is a joy for ever. Its loveliness increases; it will never pass into nothingness.'


def request(method, endpoint, payload=None, timeout=18):
    conn = http.client.HTTPConnection('localhost', timeout=timeout)
    conn.sock = socket.socket(socket.AF_UNIX)
    conn.sock.settimeout(timeout)
    conn.sock.connect(SOCKET)
    try:
        conn.request(method, endpoint, json.dumps(payload) if payload is not None else None,
                     headers={'Content-Type': 'application/json', 'X-IPC-Magic': MAGIC})
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def record(name='profile-refresh.json'):
    try:
        return json.loads((STATE / name).read_text())
    except (OSError, ValueError):
        return {}


def digest():
    return (STATE / 'config-digest').read_text().strip()


def wait(predicate, timeout=95):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            pass
        time.sleep(.25)
    raise AssertionError('refresh condition did not settle')


def ready():
    if record('state.json').get('mode') != 'proxy' or not record('health.json').get('ok'):
        return False
    status, value = request('GET', '/status')
    pid = value.get('data', {}).get('core_pid')
    members = Path('/sys/fs/cgroup/system.slice/clash-verge.service/cgroup.procs').read_text().split()
    return status == 200 and type(pid) is int and str(pid) in members


def restart_gui(expected=200, short=False):
    child = subprocess.run(['setpriv', '--reuid=lfour', '--regid=users', '--init-groups',
                            'python3', __file__, 'gui-retry' if short else 'gui'],
                           capture_output=True, text=True, timeout=50)
    if child.returncode or json.loads(child.stdout)['status'] != expected:
        print('refresh state at failed request: ' + json.dumps(record()), flush=True)
        for log in sorted(Path('/var/lib/clash-verge-service/logs').glob('*.log')):
            print(log.read_text()[-6000:], flush=True)
    assert child.returncode == 0, 'desktop IPC fixture failed: ' + child.stderr[-2000:]
    response = json.loads(child.stdout)
    assert response['status'] == expected, response
    return response


def gui(short=False):
    launcher = Path('/etc/netsec-launcher').read_text().strip()
    payload = dict(core_config=dict(core_path=launcher, config_dir=str(GUI), config_path=str(CONFIG),
                                   core_ipc_path='/tmp/caller-supplied-socket'),
                   log_config=dict(directory='/tmp/caller-logs', max_log_size=1048576, max_log_files=2))
    for attempt in range(20 if short else 1):
        try:
            status, value = request('POST', '/clash/start', payload, timeout=.15 if short else 18)
            print(json.dumps(dict(status=status, code=value.get('code'), message=value.get('message'),
                                  attempts=attempt + 1)))
            return
        except (TimeoutError, OSError, http.client.HTTPException):
            if not short:
                raise
            time.sleep(min(.05 * 2 ** attempt, 10))
    raise AssertionError('short-timeout IPC retries exhausted')


def strict():
    original = CONFIG.read_bytes()
    before = request('GET', '/status')[1]['data']['restart_count']
    try:
        CONFIG.write_bytes(original + b'\n# strict candidate\n')
        result = restart_gui(expected=503, short=True)
        assert '78' in result['message'] and 'configuration refused' in result['message']
        time.sleep(2)
        assert request('GET', '/status')[1]['data']['restart_count'] == before
        assert record()['result'] == 'refused' and record()['policy'] == 'strict'
        CONFIG.write_bytes(original)
        restart_gui(short=True)
        wait(ready)
        print('strict: changed input refused once; original snapshot restarted', flush=True)
    finally:
        CONFIG.write_bytes(original)


def auto():
    wait(lambda: record().get('result') == 'accepted')
    CONFIG.write_bytes(CONFIG.read_bytes() + b'\n# automatic refresh\n')
    restart_gui(short=True)
    pid = request('GET', '/status')[1]['data']['core_pid']
    restart_gui(short=True)
    assert request('GET', '/status')[1]['data']['core_pid'] == pid
    wait(lambda: record().get('result') == 'accepted')
    wait(ready)
    original, settled = CONFIG.read_bytes(), digest()
    assert record()['policy'] == 'auto'
    print('auto: valid refresh accepted; 150ms IPC retries kept one running core', flush=True)
    for kind in ('invalid', 'audit'):
        candidate = yaml.safe_load(original)
        if kind == 'invalid':
            candidate['mixed-port'] = 7888
        else:
            candidate['hosts']['undeclared.fixture.example'] = '1.1.1.1'
        CONFIG.write_text(yaml.safe_dump(candidate, sort_keys=False))
        restart_gui()
        assert record()['result'] == 'fallback' and digest() == settled
        assert record()['reason'] == ('configuration invalid' if kind == 'invalid' else 'blocked by audit')
        validator = Path('/run/clash-verge-rev/validator.log').stat().st_mtime_ns
        summary = record()
        restart_gui()
        assert digest() == settled and record() == summary
        assert Path('/run/clash-verge-rev/validator.log').stat().st_mtime_ns == validator
        subprocess.run(['systemctl', 'restart', 'clash-verge.service'], check=True)
        wait(ready)
        assert digest() == settled and record() == summary
        assert Path('/run/clash-verge-rev/validator.log').stat().st_mtime_ns == validator
        print('auto: ' + kind + ' candidate rejected; repeated restart reused pin', flush=True)
    for failure, port in (('stale', 18081), ('missing', 18082)):
        candidate = yaml.safe_load(original)
        candidate['proxies'][0]['port'] = port
        CONFIG.write_text(yaml.safe_dump(candidate, sort_keys=False))
        restart_gui()
        transaction = record()
        assert transaction['result'] == 'verifying'
        rejected = digest()
        assert rejected != settled
        bad_pid = request('GET', '/status')[1]['data']['core_pid']
        desired = list(Path('/var/lib/clash-verge-service').rglob('desired-state.json'))
        assert len(desired) == 1
        if failure == 'missing':
            desired[0].unlink()
        else:
            value = json.loads(desired[0].read_text())
            value['last_clash_config']['core_config']['core_path'] = '/nix/store/nonexistent-old-package/bin/verge-mihomo'
            desired[0].write_text(json.dumps(value))
        wait(lambda: record().get('result') == 'rolled_back' and digest() == settled)
        wait(ready, timeout=20)
        elapsed = time.time() - transaction['at']
        assert 60 <= elapsed <= 95, elapsed
        assert request('GET', '/status')[1]['data']['core_pid'] != bad_pid
        assert not (STATE / 'profile-refresh-restart.json').exists()
        assert record('profile-refresh-pin.json')['digest'] == rejected
        restart_gui()
        wait(ready)
        subprocess.run(['systemctl', 'restart', 'clash-verge.service'], check=True)
        wait(ready)
        assert digest() == settled and record()['result'] == 'rolled_back'
        print('auto: dead nodes rolled back and recovered in %.1fs with %s desired-state' % (elapsed, failure), flush=True)
    CONFIG.write_bytes(original)
    subprocess.run(['proxy-control', 'off'], check=True)
    assert not (STATE / 'profile-refresh-pin.json').exists()
    assert not (STATE / 'profile-refresh-verifying.json').exists()
    assert not (STATE / 'effective-config.json').exists()
    subprocess.run(['proxy-control', 'on'], check=True)
    wait(lambda: request('GET', '/status')[0] == 200, timeout=20)
    restart_gui()
    wait(ready)
    policy = subprocess.check_output(['proxy-policy'], text=True)
    assert 'profile_refresh' in policy and 'http://' not in policy and 'Fixture' not in policy
    print('auto: on/off cancelled pending work; public policy remained sanitized', flush=True)


if __name__ == '__main__':
    {'gui': gui, 'gui-retry': lambda: gui(short=True), 'strict': strict, 'auto': auto}[sys.argv[1]]()
