"""Desktop-only, local-file refresh reminders. Never fetch a subscription."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time


def notify(refresh, subscription, directory, send, now=None, diagnostics=None):
    now = time.time() if now is None else now
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
        if refresh and refresh['result'] in ('fallback', 'rolled_back', 'refused'):
            key = refresh['result'] + ':' + refresh['digest']
            if key not in seen:
                reason = {'node': 'node health failed', 'tun': 'tun health failed',
                          'dns': 'dns health failed'}.get(refresh['reason'], refresh['reason'])
                messages.append('Profile refresh: ' + refresh['result'] + ' · ' + (reason or 'unknown'))
                seen.append(key)
        findings = refresh.get('audit') if refresh else None
        if findings and findings.get('verdict') in ('warn', 'deny', 'unknown'):
            key = 'audit:' + refresh['digest'] + ':' + json.dumps(findings, sort_keys=True)
            if key not in seen:
                messages.append('Content audit: findings recorded.')
                seen.append(key)
        diagnostics = diagnostics or {}
        old_states = previous.get('states', {})
        old_states = old_states if isinstance(old_states, dict) else {}
        states = {name: value for name, value in diagnostics.items()
                  if name in ('update', 'content') and value in
                  ('ok', 'overdue', 'differs', 'in sync', 'gui-closed', 'timer-off', 'unknown')}
        for name, value in states.items():
            if value in ('overdue', 'differs') and old_states.get(name) != value:
                messages.append('Subscription update is overdue.' if value == 'overdue' else
                                'GUI profile differs from the authorized snapshot.')
        day = time.strftime('%Y-%m-%d', time.localtime(now))
        if subscription.get('stale') and previous.get('stale_day') != day:
            days = subscription.get('age_days')
            messages.append('Subscription health: stale · ' +
                            ('last successful activity ' + str(days) + 'd ago'
                             if days is not None else 'no successful activity observed'))
            previous['stale_day'] = day
        if messages:
            # Persist only after a successful notification. One event may be a
            # rejected refresh, a content difference and stale at the same time.
            send('\n'.join(messages) + '\nInspect proxy-status for details.')
        if messages or states != old_states:
            previous.update(seen=seen, states=states)
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
    data = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share')
    profiles = data / 'io.github.clash-verge-rev.clash-verge-rev/profiles.yaml'
    refresh = state.profile_refresh(full_digests=True)
    subscription = state.subscription_health(profiles, refresh)
    directory = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'clash-verge'
    def send(message):
        subprocess.run([args.notify_send, '--app-name=Clash Verge', 'Proxy profile', message],
                       check=True, timeout=10)
    diagnostics = dict(update=state.auto_update(profiles)['state'],
                       content=state.profile_vs_runtime(profiles)['state'])
    notify(refresh, subscription, directory, send, diagnostics=diagnostics)


if __name__ == '__main__':
    main()
