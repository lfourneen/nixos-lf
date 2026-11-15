"""Desktop-only, local-file refresh reminders. Never fetch a subscription."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess


def notify(refresh, directory, send, health=None, fetch=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / 'refresh-notified.json'
    with (directory / 'refresh-notified.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            previous = json.loads(path.read_text())
            if not isinstance(previous, dict):
                previous = {}
        except (OSError, ValueError):
            previous = {}
        seen = previous.get('seen', [])
        seen = list(seen) if isinstance(seen, list) else []
        messages = []
        if fetch and fetch['result'] == 'failed':
            key = 'download:' + str(fetch['at'])
            if key not in seen:
                messages.append('Subscription download failed · ' + (fetch.get('reason') or fetch.get('phase') or 'worker failure'))
                seen.append(key)
        if refresh and refresh['result'] in ('fallback', 'rolled_back', 'refused'):
            key = refresh['result'] + ':' + refresh['digest']
            if key not in seen:
                reason = {'node': 'node health failed', 'tun': 'tun health failed',
                          'dns': 'dns health failed'}.get(refresh['reason'], refresh['reason'])
                messages.append('Profile refresh: ' + refresh['result'] + ' · ' + (reason or 'unknown'))
                seen.append(key)
        old_states = previous.get('states', {})
        old_states = old_states if isinstance(old_states, dict) else {}
        states = {}
        reason = (health or {}).get('reason')
        if reason not in ('node', 'tun', 'dns', 'config', 'route', 'obs', 'direct-mode', 'no-tun', 'core-gone'):
            reason = 'obs'
        if health:
            states['health'] = 'ok' if health.get('ok') is True else reason
            if states['health'] != 'ok' and old_states.get('health') != states['health']:
                messages.append('Proxy health warning: ' + reason)
        if messages:
            # Combine failures in one message and advance deduplication only
            # after delivery succeeds. Successful updates stay in proxy-status.
            send('\n'.join(messages) + '\nInspect proxy-status for details.')
        if messages or states != old_states:
            previous.update(seen=seen[-100:], states=states)
            temporary = path.with_name(path.name + '.new')
            temporary.unlink(missing_ok=True)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(previous, stream)
                stream.write('\n')
            temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('state_tool')
    parser.add_argument('notify_send')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('network_state', args.state_tool)
    state = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(state)
    refresh = state.profile_refresh(full_digests=True)
    fetch = state.source_fetch()
    directory = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'clash-verge'
    def send(message):
        subprocess.run([args.notify_send, '--app-name=Clash Verge', 'Proxy profile', message],
                       check=True, timeout=10)
    health = state.read('/run/proxy-mode/health.json')
    if health.get('boot_id') != state.boot_identity():
        health = None
    notify(refresh, directory, send, health=health, fetch=fetch)


if __name__ == '__main__':
    main()
