"""VM-only WebKit automation of the unchanged production GUI and its IPC."""
import http.client
import json
import os
import runpy
import shlex
from pathlib import Path
import subprocess
import sys
import time

import yaml
DESKTOP = runpy.run_path('/etc/netsec_desktop.py')
GUI, HOME, RUNTIME, USER = (DESKTOP[key] for key in ('GUI', 'HOME', 'RUNTIME', 'USER'))

SETTINGS = json.loads(Path('/etc/netsec-gui-webdriver.json').read_text())
SESSION = Path('/run/gui-webdriver-session.json')
EXIT_STATUS = RUNTIME / 'gui-webdriver-exit'
ENV = {
    'HOME': str(HOME), 'XDG_DATA_HOME': str(GUI.parent), 'XDG_RUNTIME_DIR': str(RUNTIME),
    'WAYLAND_DISPLAY': 'wayland-0', 'GDK_BACKEND': 'wayland',
    'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + str(RUNTIME / 'bus'),
    'TAURI_WEBVIEW_AUTOMATION': 'true', 'XDG_CURRENT_DESKTOP': 'GNOME',
    'WEBKIT_DISABLE_DMABUF_RENDERER': '1', 'WEBKIT_DISABLE_COMPOSITING_MODE': '1',
    'LIBGL_ALWAYS_SOFTWARE': '1', 'GSETTINGS_SCHEMA_DIR': SETTINGS['schemas'],
}


def run(*args, **kwargs):
    try:
        return subprocess.check_output(args, text=True, **kwargs)
    except subprocess.CalledProcessError as error:
        print(error.output, flush=True)
        raise


def wait(check, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(.2)
    raise AssertionError('GUI condition timed out')


def request(method, path, value=None, timeout=90):
    connection = http.client.HTTPConnection('127.0.0.1', 4444, timeout=timeout)
    try:
        connection.request(method, path, json.dumps(value) if value is not None else None,
                           {'Content-Type': 'application/json'})
        response = connection.getresponse()
        data = json.loads(response.read())
        assert response.status < 300, (path, response.status, data)
        return data['value']
    finally:
        connection.close()


def script(source, args=(), asynchronous=False):
    session = json.loads(SESSION.read_text())
    return request('POST', '/session/' + session + '/execute/' + ('async' if asynchronous else 'sync'),
                   {'script': source, 'args': list(args)})


def invoke(command, payload=None):
    result = script('''
        const [command, payload, done] = arguments;
        window.__TAURI_INTERNALS__.invoke(command, payload == null ? {} : {payload})
          .then(value => done({ok: true, value}), error => done({ok: false, error: String(error)}));
        ''', (command, payload), True)
    assert result['ok'], (command, payload, result)
    return result.get('value')


def proxy_state():
    return {schema + '/' + key: run('setpriv', f'--reuid={USER.pw_uid}', f'--regid={USER.pw_gid}', '--init-groups',
                     'env', *[f'{key}={value}' for key, value in ENV.items()],
                     SETTINGS['gsettings'], 'get', schema, key).strip()
            for schema, keys in [
                ('org.gnome.system.proxy', ('mode', 'autoconfig-url', 'ignore-hosts')),
                ('org.gnome.system.proxy.http', ('host', 'port')),
                ('org.gnome.system.proxy.https', ('host', 'port')),
                ('org.gnome.system.proxy.socks', ('host', 'port')),
            ] for key in keys}


def core_state():
    return json.loads(run('curl', '-fsS', '--unix-socket', '/run/clash-verge-rev/core/mihomo.sock',
                          'http://localhost/configs'))


def core_pid():
    return run('python3', '-c', '''
import importlib.util,json
from pathlib import Path
s=json.loads(Path('/etc/netsec-managed.json').read_text())
spec=importlib.util.spec_from_file_location('stage',s['stage'])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
settings=json.loads(Path(s['stageSettings']).read_text())
print(m.service_request(settings,'GET','/status')['core_pid'])
''').strip()


def authority():
    value = json.loads(Path('/run/proxy-mode/state.json').read_text())
    assert value['mode'] == 'proxy', value
    return {key: value[key] for key in ('mode', 'policy_version', 'routing_epoch', 'routing_policy_digest')}


def ready():
    value = json.loads(run('proxy-control', 'status', 'proxy'))
    if not value['ready']:
        print('Waiting for coordinated observations:', value['reasons'], flush=True)
    return value['ready']


def gui_processes():
    expected = (SETTINGS['package'] + '/bin/clash-verge').encode()
    result = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            if path.stat().st_uid == USER.pw_uid and (path / 'cmdline').read_bytes().split(b'\0')[0] == expected:
                result.append(int(path.name))
        except (FileNotFoundError, ProcessLookupError):
            pass
    return result


def start():
    if SESSION.exists():
        return
    wait(lambda: (RUNTIME / 'wayland-0').exists())
    # A non-default OS setting detects startup/disable writes too. This runs
    # only in the disposable VM, outside the GUI launch namespace.
    for schema, key, value in [
        ('org.gnome.system.proxy.http', 'host', '127.0.0.2'),
        ('org.gnome.system.proxy.http', 'port', '9999'),
        ('org.gnome.system.proxy', 'mode', 'manual'),
    ]:
        run('setpriv', f'--reuid={USER.pw_uid}', f'--regid={USER.pw_gid}', '--init-groups', 'env',
            *[f'{key}={value}' for key, value in ENV.items()],
            SETTINGS['gsettings'], 'set', schema, key, value)
    initial_proxy = proxy_state()
    initial_pid, initial_authority = core_pid(), authority()
    profile = yaml.safe_load(Path('/run/clash-verge-rev/effective/clash-verge.yaml').read_bytes())
    (GUI / 'profiles').mkdir(parents=True, exist_ok=True)
    (GUI / 'profiles/RFixture.yaml').write_text(yaml.safe_dump(profile, sort_keys=False))
    (GUI / 'profiles.yaml').write_text(yaml.safe_dump({'current': 'uid-one', 'items': [{
        'uid': 'uid-one', 'name': 'Fixture', 'type': 'local', 'file': 'RFixture.yaml',
        'updated': int(time.time()),
    }]}))
    (GUI / 'config.yaml').write_text(yaml.safe_dump(profile, sort_keys=False))
    (GUI / 'verge.yaml').write_text(yaml.safe_dump({
        'language': 'en', 'enable_tun_mode': True, 'enable_system_proxy': False,
        'auto_check_update': False, 'enable_auto_backup_schedule': False,
        # Connection closing remains authorized; isolate it from control tests.
        'auto_close_connection': False,
        'default_latency_test': 'http://node.fixture.example:8080/',
        'enable_global_hotkey': False,
    }))
    run('chown', '-R', f'{USER.pw_uid}:{USER.pw_gid}', str(GUI))
    launcher = RUNTIME / 'gui-webdriver-launch'
    launcher.write_text(
        '#!/bin/sh\n' + shlex.quote(SETTINGS['package'] + '/bin/clash-verge') + ' "$@"\n'
        'result=$?\n'
        'printf "%s\\n" "$result" > ' + shlex.quote(str(EXIT_STATUS)) + '\n'
        'exit "$result"\n')
    launcher.chmod(0o700)
    os.chown(launcher, USER.pw_uid, USER.pw_gid)
    run('systemd-run', '--unit=gui-webdriver', f'--property=User={USER.pw_name}', f'--property=Group={USER.pw_gid}',
        '--property=SupplementaryGroups=clash-verge-control',
        *[f'--setenv={key}={value}' for key, value in ENV.items()],
        SETTINGS['webdriver'], '--host=127.0.0.1', '--port=4444')
    wait(lambda: request('GET', '/status').get('ready'))
    session = request('POST', '/session', {'capabilities': {'alwaysMatch': {
        'webkitgtk:browserOptions': {'binary': str(launcher), 'args': []},
    }}})
    SESSION.write_text(json.dumps(session['sessionId']))
    request('POST', '/session/' + session['sessionId'] + '/timeouts', {'script': 60000})
    wait(lambda: script('return Boolean(window.__TAURI_INTERNALS__)'))
    wait(lambda: script('return document.body.innerText.length > 100'))
    time.sleep(3)
    print('GUI loaded:', script('return document.body.innerText.slice(0,1200)'), flush=True)
    assert proxy_state() == initial_proxy, 'GUI startup changed OS proxy state'
    assert core_pid() == initial_pid and authority() == initial_authority, 'GUI startup changed managed state'


def check():
    start()
    script('''
        window.__guiAuditErrors = [];
        if (window.__guiAuditObserver) window.__guiAuditObserver.disconnect();
        const collect = () => document.querySelectorAll(
            '.MuiAlert-colorError,.MuiAlert-standardError,.MuiAlert-filledError,.MuiAlert-outlinedError'
        ).forEach(node => window.__guiAuditErrors.push(node.innerText));
        window.__guiAuditObserver = new MutationObserver(collect);
        window.__guiAuditObserver.observe(document.body, {childList:true,subtree:true});
        ''')
    before, proxy, pid, policy = core_state(), proxy_state(), core_pid(), authority()
    print('GUI test baseline:', {'pid': pid, 'core_mode': before['mode'], 'proxy': proxy}, flush=True)
    for mode in ('rule', 'global', 'direct'):
        invoke('patch_clash_mode', mode)
        assert core_state()['mode'] == before['mode'] == 'rule'
        assert authority() == policy
    for enabled in (False, True):
        invoke('patch_verge_config', {'enable_system_proxy': enabled})
        assert proxy_state() == proxy
        assert authority() == policy
    for enabled in (False, True):
        invoke('patch_verge_config', {'enable_tun_mode': enabled})
        current = core_state()
        assert current['tun'] == before['tun'] and current['mode'] == before['mode']
        assert core_pid() == pid, 'GUI TUN preference restarted the managed core'
        assert authority() == policy
    for stack in ('system', 'gvisor'):
        invoke('patch_clash_config', {'tun': {
            'enable': False, 'stack': stack, 'device': 'GuiIgnored', 'mtu': 9000,
            'auto-route': False, 'auto-detect-interface': False,
        }})
        assert core_state()['tun'] == before['tun'] and core_pid() == pid
        assert authority() == policy
    for enabled in (True, False):
        invoke('patch_verge_config', {'proxy_auto_config': enabled, 'enable_proxy_guard': enabled,
                                       'proxy_guard_duration': 1})
        if enabled:
            time.sleep(2.2)
        assert proxy_state() == proxy
        assert authority() == policy
    assert core_pid() == pid
    for page in ('/', '/proxies'):
        script('''
            history.pushState({}, '', arguments[0]);
            dispatchEvent(new PopStateEvent('popstate'));
            ''', (page,))
        time.sleep(1)
        for mode in ('rule', 'global', 'direct'):
            clicked = script('''
                const item = Array.from(document.querySelectorAll('button,.MuiPaper-root'))
                    .find(node => node.textContent.trim().toLowerCase() === arguments[0]);
                if (!item) return false;
                item.click(); return true;
                ''', (mode,))
            assert clicked, ('mode control not found', page, mode)
            time.sleep(1)
            assert core_state()['mode'] == 'rule' and core_pid() == pid
            assert authority() == policy
    errors = script('return window.__guiAuditErrors')
    assert not errors, ('GUI displayed errors', errors)
    wait(ready, timeout=30)
    assert authority() == policy
    print('Real GUI IPC: mode/TUN/System Proxy/PAC/guard succeeded without changing the core or OS', flush=True)
    status = json.loads(run('proxy-control', 'status', 'proxy'))
    if status['routing_applied'] == 'proxy-only':
        assert gui_processes(), 'no production GUI process observed'
        script("setTimeout(() => window.__TAURI_INTERNALS__.invoke('exit_app'), 100); return true")
        # WebKit can defer process exit while automation/network work settles.
        try:
            wait(lambda: not gui_processes(), timeout=60)
        except AssertionError:
            for pid in gui_processes():
                path = Path('/proc') / str(pid)
                print('Remaining GUI process:', pid, (path / 'exe').readlink(),
                      (path / 'stat').read_text(), flush=True)
            raise
        wait(EXIT_STATUS.exists, timeout=10)
        assert EXIT_STATUS.read_text().strip() == '0', 'GUI shutdown reported a cleanup error'
        assert core_pid() == pid and proxy_state() == proxy, 'GUI exit changed managed state'
        assert authority() == policy
        wait(ready, timeout=30)
        print('GUI exit preserved the root-managed core and OS proxy', flush=True)


if __name__ == '__main__':
    try:
        check()
    except BaseException:
        print(run('journalctl', '-u', 'gui-webdriver', '-u', 'clash-verge', '--no-pager', '-n', '100'))
        raise
