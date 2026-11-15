"""Consume a fresh health proof once; lease failure never selects direct access.

The caller holds the coordinator's policy lock. Kernel timeouts enforce expiry
even when the caller stops running. Proof time is boot time, including suspend.
"""
import json
import math
from pathlib import Path
import subprocess
import sys
import time


def read(path):
    return json.loads(path.read_text())


def grant(settings, version, root=Path('/run'), now=None, run=subprocess.run):
    root = Path(root)
    state = root / 'proxy-mode'
    seconds = settings['seconds']
    if seconds is None:
        return
    if type(seconds) is not int or not 30 <= seconds <= 120:
        raise ValueError('invalid authorization lifetime')
    now = time.clock_gettime(time.CLOCK_BOOTTIME) if now is None else now
    proof = read(state / 'health.json')
    policy = read(state / 'state.json')
    boot = (state / 'boot-id').read_text().strip()
    digest = (state / 'config-digest').read_text().strip()
    observed = proof.get('monotonic_seconds')
    previous = read(state / 'authorization.json') if (state / 'authorization.json').exists() else {}
    epoch = (state / 'authorization-epoch').read_text().strip() if (state / 'authorization-epoch').exists() else '0'
    if (policy.get('mode') != 'proxy' or policy.get('policy_version') not in (version, version + 1)
            or (state / 'intent').read_text().strip() != 'proxy'
            or (root / 'dns-policy/sleeping').exists()
            or proof.get('ok') is not True or proof.get('boot_id') != boot
            or not digest or proof.get('digest') != digest
            or proof.get('policy_version') != version
            or proof.get('authorization_epoch') != epoch
            or type(observed) not in (int, float) or not math.isfinite(observed)
            or not 0 <= now - observed < seconds
            or not isinstance(proof.get('proof_id'), str) or not proof['proof_id']
            or proof['proof_id'] == previous.get('proof_id')):
        raise ValueError('stale, replayed or unauthorized health proof')
    remaining = int((observed + seconds - now) * 1000)
    if remaining <= 0:
        raise ValueError('expired health proof')
    # Record consumption before nft: interrupted/ambiguous commits cannot reuse
    # the same proof. Only a new complete health round can retry authorization.
    temporary = state / 'authorization.json.tmp'
    temporary.write_text(json.dumps(dict(boot_id=boot, proof_id=proof['proof_id'],
        policy_version=policy['policy_version'], until=0)) + '\n')
    temporary.replace(state / 'authorization.json')
    transaction = ('flush set inet lf_filter b_authorization\n'
        'add element inet lf_filter b_authorization { '
        f'ipv4 timeout {remaining}ms, ipv6 timeout {remaining}ms' + ' }\n')
    run([settings['nft'], '-f', '-'], input=transaction, text=True,
        capture_output=True, timeout=5, check=True)
    temporary.write_text(json.dumps(dict(boot_id=boot, proof_id=proof['proof_id'],
        policy_version=policy['policy_version'], until=observed + seconds)) + '\n')
    temporary.replace(state / 'authorization.json')


if __name__ == '__main__':
    try:
        grant(json.loads(Path(sys.argv[1]).read_text()), int(sys.argv[2]))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('proxy-authorization: ' + type(error).__name__, file=sys.stderr)
        sys.exit(1)
