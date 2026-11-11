"""Boot-scoped DNS allowance, journaled before granting a kernel lease.

The journal records the entire possibly installed lease, not whether it is
still open when the coordinator next runs. A failed/ambiguous apply is charged
conservatively until expiry; only a confirmed revocation shortens that interval.
"""
import argparse
import json
import math
from pathlib import Path
import re
import time

TOTAL = 1800
LEASE = 120


def initial(now):
    return dict(balance=TOTAL, tick=now, until=0, healthy=None)


def settle(state, now):
    if now < state['tick']:
        raise ValueError('budget clock moved backwards')
    spent = max(0, min(now, state['until']) - state['tick'])
    state['balance'] = max(0, state['balance'] - spent)
    state['tick'] = now


def change(state, action, now, *, override=False, secure=False, epoch='', awake=0,
           boot_until=None):
    settle(state, now)
    if action == 'grant':
        state['healthy'] = None
        duration = LEASE if override else min(LEASE, state['balance'])
        until = now + duration
        if not override and boot_until is not None:
            until = min(until, boot_until)
        # Never forget an earlier uncertain lease just because a new request
        # has less allowance. The broker will revoke it before observe runs.
        state['until'] = max(state['until'], max(now, until))
        return max(now, until)
    if action == 'observe':
        # Called only after the broker has confirmed a transaction with NO
        # plaintext permission. Settle first, then truncate that old lease.
        state['until'] = 0
        old = state.get('healthy')
        if secure and old and old['epoch'] == epoch:
            elapsed = now - old['at']
            # Two fresh successful rounds in one coordinator/policy epoch.
            # Neither restarts nor suspend nor a long unobserved gap earn time.
            if 0 <= elapsed <= 90 and abs(elapsed - (awake - old['awake'])) <= 2:
                if boot_until is not None:
                    elapsed = max(0, min(now, boot_until) - old['at'])
                state['balance'] = min(TOTAL, state['balance'] + elapsed)
        state['healthy'] = dict(at=now, awake=awake, epoch=epoch) if secure else None
    elif action == 'interrupt':
        # An unsuccessful reconciliation breaks the consecutive healthy rounds,
        # but says nothing about whether the last kernel lease is still alive.
        state['healthy'] = None
    elif action != 'view':
        raise ValueError('unknown budget action')
    return state['until']


def load(path, now):
    if not path.exists():
        return initial(now)
    text = path.read_text()
    if re.fullmatch(r'\d+ \d+\s*', text):
        # Old coordinators did not save a settlement timestamp. Do not mint a
        # new allowance on upgrade; charge one uncertain historical lease.
        balance, until = map(int, text.split())
        return dict(balance=max(0, min(TOTAL, balance) - LEASE), tick=now,
                    until=min(until, now + LEASE), healthy=None)
    state = json.loads(text)
    for key in ('balance', 'tick', 'until'):
        value = state[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('invalid budget journal')
    if state['balance'] > TOTAL or state['until'] > now + LEASE:
        raise ValueError('invalid budget bounds')
    return state


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('runtime', type=Path)
    parser.add_argument('session')
    parser.add_argument('action', choices=['view', 'grant', 'observe', 'interrupt'])
    parser.add_argument('--override', action='store_true')
    parser.add_argument('--secure', action='store_true')
    parser.add_argument('--epoch', default='')
    parser.add_argument('--boot-until', type=int)
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', args.session):
        raise ValueError('invalid session identity')
    now = int(time.clock_gettime(time.CLOCK_BOOTTIME))
    path = args.runtime / 'budgets' / args.session
    state = load(path, now)
    until = change(state, args.action, now, override=args.override, secure=args.secure,
                   epoch=args.epoch, awake=int(time.monotonic()), boot_until=args.boot_until)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, separators=(',', ':')) + '\n')
    temporary.replace(path)
    print(int(state['balance']), int(until))


if __name__ == '__main__':
    main()
