"""VM-only checks of the production helper, launcher and running process."""
import http.client
import json
import os
from pathlib import Path
import pwd
import socket
import subprocess
import sys

GUI = Path('/home/lfour/.local/share/io.github.clash-verge-rev.clash-verge-rev')
CONFIG = GUI / 'clash-verge.yaml'
MAGIC = 'A thing of beauty is a joy for ever. Its loveliness increases; it will never pass into nothingness.'
SENTINEL = Path('/run/proxy-mode/helper-sentinel')


def request(sock, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection('localhost', timeout=5)
    conn.sock = socket.socket(socket.AF_UNIX)
    conn.sock.settimeout(5)
    conn.sock.connect(sock)
    conn.request(method, path, json.dumps(body) if body is not None else None,
                 headers={'Content-Type': 'application/json', **(headers or {})})
    res = conn.getresponse()
    result = res.status, res.read().decode()
    conn.close()
    return result


def prepare():
    import yaml
    GUI.mkdir(parents=True, exist_ok=True)
    profile = json.loads(Path('/run/netsec-real-profile.json').read_text())
    profile['external-controller-unix'] = '/run/user/1000/clash-verge-rev/verge-mihomo.sock'
    CONFIG.write_text(yaml.safe_dump(profile, allow_unicode=True, sort_keys=False))
    for path in [GUI, *GUI.parents]:
        if path == Path('/home'):
            break
        os.chown(path, 1000, pwd.getpwnam('lfour').pw_gid)
        path.chmod(0o700)
    os.chown(CONFIG, 1000, pwd.getpwnam('lfour').pw_gid)
    CONFIG.chmod(0o600)
    Path('/run/user/1000').mkdir(parents=True, exist_ok=True)
    os.chown('/run/user/1000', 1000, pwd.getpwnam('lfour').pw_gid)
    os.chmod('/run/user/1000', 0o700)
    SENTINEL.write_text('root-owned sentinel\n')
    SENTINEL.chmod(0o600)


def start():
    payload = {
        'core_config': {
            'core_path': Path('/etc/netsec-launcher').read_text().strip(),
            'config_dir': str(GUI), 'config_path': str(CONFIG),
            'core_ipc_path': str(SENTINEL),
        },
        'log_config': {'directory': '/run/user/1000/untrusted-logs',
                       'max_log_size': 1048576, 'max_log_files': 2},
    }
    # Exercise the actual IPC as a desktop group member, including the helper's
    # root spawn choke point; no substitute root ExecStart is used here.
    if os.geteuid() == 0:
        user = pwd.getpwnam('lfour')
        os.initgroups(user.pw_name, user.pw_gid)
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
    wrong = json.loads(json.dumps(payload))
    wrong['core_config']['core_path'] = '/run/current-system/sw/bin/true'
    status, body = request('/run/clash-verge-rev/service.sock', 'POST', '/clash/start', wrong,
                           {'X-IPC-Magic': MAGIC})
    assert status == 503 and 'configured launcher' in body, (status, body)
    status, body = request('/run/clash-verge-rev/service.sock', 'POST', '/clash/start', payload,
                           {'X-IPC-Magic': MAGIC})
    assert status == 200, (status, body)


def check():
    core_uid = pwd.getpwnam('clash-core').pw_uid
    pids = Path('/sys/fs/cgroup/system.slice/clash-verge.service/cgroup.procs').read_text().split()
    cores = []
    for pid in pids:
        cmd = Path(f'/proc/{pid}/cmdline').read_bytes()
        if b'/.verge-mihomo-wrapped\0' in cmd:
            cores.append(pid)
    assert len(cores) == 1, cores
    status = dict(line.split(':', 1) for line in Path(f'/proc/{cores[0]}/status').read_text().splitlines() if ':' in line)
    assert set(status['Uid'].split()) == {str(core_uid)}, status['Uid']
    for cap in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'):
        assert status[cap].strip() == '0000000000003000', (cap, status[cap])
    for option in ('+tcp', '+notcp'):
        result = subprocess.check_output(['dig', '+short', '+time=2', '+tries=1', option,
                                          '@127.0.0.1', '-p', '1053', 'tun.fixture.example', 'A'], text=True)
        assert '198.18.' in result, result
    effective = Path('/run/clash-verge-rev/effective')
    assert effective.stat().st_uid == 0 and effective.stat().st_mode & 0o777 == 0o750
    assert (effective / 'clash-verge.yaml').stat().st_mode & 0o777 == 0o640
    # The core must not be able to replace the snapshot it was authorized with.
    assert subprocess.run(['setpriv', '--reuid=clash-core', '--regid=clash-core', '--clear-groups',
                           'test', '-w', str(effective / 'clash-verge.yaml')]).returncode != 0
    sock = '/run/user/1000/clash-verge-rev/verge-mihomo.sock'
    assert request(sock, 'PATCH', '/rules/disable', {'0': True})[0] == 403
    assert request(sock, 'PUT', '/configs', {})[0] == 403
    assert request(sock, 'PATCH', '/configs', {'mode': 'direct'})[0] == 403
    assert request(sock, 'PATCH', '/configs', {'mode': 'rule'})[0] == 204
    user = pwd.getpwnam('lfour')
    subprocess.check_call(['setpriv', '--reuid=1000', f'--regid={user.pw_gid}', '--init-groups',
                           sys.executable, __file__, 'writer'])
    assert not Path('/run/proxy-mode/authorize-window').exists(), 'writer minted an import window'
    assert SENTINEL.read_text() == 'root-owned sentinel\n'
    assert SENTINEL.stat().st_mode & 0o777 == 0o600, 'helper chmodded caller-supplied path'


def writer():
    directory = Path('/run/user/1000/untrusted-logs')
    directory.mkdir(exist_ok=True)
    link = directory / 'service_latest.log'
    if not link.is_symlink():
        link.symlink_to('/run/proxy-mode/authorize-window')
    result = request('/run/clash-verge-rev/service.sock', 'PUT', '/writer',
                     {'directory': str(directory), 'max_log_size': 1048576, 'max_log_files': 2},
                     {'X-IPC-Magic': MAGIC})
    assert result[0] == 200, result


def refuse():
    launcher = Path('/etc/netsec-launcher').read_text().strip()
    args = [launcher, '-d', str(GUI), '-f', str(CONFIG)]
    assert subprocess.run(args).returncode == 78
    assert subprocess.run(['setpriv', '--reuid=1000', '--clear-groups', *args]).returncode == 78
    assert SENTINEL.read_text() == 'root-owned sentinel\n', 'helper unlinked caller-supplied path'


def preflight_rejection():
    import yaml
    original = CONFIG.read_bytes()
    try:
        profile = yaml.safe_load(original)
        profile['proxies'][0]['skip-cert-verify'] = True
        CONFIG.write_text(yaml.safe_dump(profile, sort_keys=False))
        result = subprocess.run(['proxy-control', 'on'], text=True, capture_output=True)
        assert result.returncode != 0, result.stdout
        verdict = json.loads(result.stdout)
        assert verdict['phase'] == 'prepare-policy', verdict
        assert verdict['subscription']['certificate_exceptions_rejected'] == 1, verdict
        assert json.loads(Path('/run/proxy-mode/state.json').read_text())['mode'] == 'direct'
        shown = subprocess.check_output(['setpriv', '--reuid=1000', '--clear-groups',
                                         'proxy-status', '--plain'], text=True)
        assert 'Certificate or subscription policy rejected' in shown, shown
        assert 'rejected 1' in shown, shown
    finally:
        CONFIG.write_bytes(original)


def provider_setup():
    import yaml
    profile = yaml.safe_load(CONFIG.read_bytes())
    profile['proxy-providers'] = {'ProviderSource': {
        'type': 'http', 'url': 'http://100.67.0.1:18095/provider.json',
        'path': 'proxies/fixture.yaml', 'interval': 1,
    }}
    profile['proxy-groups'].append({'name': 'ProviderChoice', 'type': 'select', 'use': ['ProviderSource']})
    CONFIG.write_text(yaml.safe_dump(profile, sort_keys=False))
    sentinel = Path('/tmp/provider-root-sentinel')
    sentinel.mkdir(mode=0o700)
    # Model a compromised core replacing a child of its writable state.
    # The root launcher must never chown/chmod through this link.
    Path('/var/lib/clash-core/proxies').symlink_to(sentinel)
    provider_contents(False)


def provider_contents(insecure):
    directory = Path('/tmp/netsec-provider')
    directory.mkdir(exist_ok=True)
    (directory / 'provider.json').write_text(json.dumps({'proxies': [{
        'name': 'ProviderFixture', 'type': 'socks5', 'server': '100.67.0.1', 'port': 18080,
        'skip-cert-verify': insecure,
    }]}))


def provider_check():
    status, content = request('/run/clash-verge-rev/core/mihomo.sock', 'GET', '/providers/proxies/ProviderSource')
    assert status == 200, status
    assert [p['name'] for p in json.loads(content)['proxies']] == ['ProviderFixture']
    cached = Path('/var/lib/clash-core/proxies/fixture.yaml')
    assert cached.stat().st_uid == pwd.getpwnam('clash-core').pw_uid
    assert json.loads(cached.read_text())['proxies'][0]['skip-cert-verify'] is False


def provider_cache_boundary():
    sentinel = Path('/tmp/provider-root-sentinel')
    assert sentinel.stat().st_uid == 0
    assert sentinel.stat().st_mode & 0o777 == 0o700
    assert not list(sentinel.iterdir()), 'core wrote through a root-only directory'
    cache = Path('/var/lib/clash-core/proxies')
    cache.unlink()
    cache.mkdir(mode=0o700)
    account = pwd.getpwnam('clash-core')
    os.chown(cache, account.pw_uid, account.pw_gid)


def provider_reject_refresh():
    # Subscribe before changing the source, using the same bounded log stream
    # as the health probe. Core output is owned by the helper's private writer.
    connection = http.client.HTTPConnection('localhost', timeout=12)
    connection.sock = socket.socket(socket.AF_UNIX)
    connection.sock.settimeout(12)
    connection.sock.connect('/run/clash-verge-rev/core/mihomo.sock')
    try:
        connection.request('GET', '/logs?level=error')
        provider_contents(True)
        response = connection.getresponse()
        assert response.status == 200
        for _ in range(20):
            line = response.readline(65536)
            if b'ProviderSource' in line and b'explicit certificate-validation exception' in line:
                return
        raise AssertionError('no certificate-policy rejection for provider refresh')
    finally:
        connection.close()


{'prepare': prepare, 'start': start, 'check': check, 'refuse': refuse,
 'writer': writer, 'preflight-rejection': preflight_rejection,
 'provider-setup': provider_setup, 'provider-check': provider_check,
 'provider-cache-boundary': provider_cache_boundary,
 'provider-reject-refresh': provider_reject_refresh,
 'provider-good': lambda: provider_contents(False)}[sys.argv[1]]()
