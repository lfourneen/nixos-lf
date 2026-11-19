"""Check a downloaded candidate before disrupting a trusted, healthy core."""

import fcntl
import grp
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def automatic_download_ready(settings, stage):
    """C protects the downloader too; defer without reporting a fetch error."""
    state = Path(settings['state'])
    mode = stage.read_record(state / 'state.json').get('mode')
    try:
        intent = (state / 'intent').read_text().strip()
    except FileNotFoundError:
        intent = 'auto'
    allowed = mode == 'direct' and intent != 'proxy'
    stable_path = state / 'source-refresh-stable.json'
    if mode == 'proxy' and intent == 'proxy':
        digest = healthy_runtime(settings, stage)
        now = time.clock_gettime(time.CLOCK_BOOTTIME)
        policy = stage.read_record(state / 'state.json').get('policy_version')
        binding = dict(boot_id=stage.boot_id(state), digest=digest, policy_version=policy)
        binding.update(stage.routing_binding(settings))
        stable = stage.read_record(stable_path)
        if digest and all(stable.get(key) == value for key, value in binding.items()):
            allowed = 90 <= now - stable.get('since', now)
        elif digest:
            stage.atomic_record(stable_path, dict(binding, since=now), mode=0o600)
        else:
            stable_path.unlink(missing_ok=True)
    else:
        stable_path.unlink(missing_ok=True)
    pending = state / 'source-refresh-pending'
    if allowed:
        pending.unlink(missing_ok=True)
        return True
    # Keep startup online before a background update can interrupt it.
    stage.atomic_text(pending, '1\n')
    stage.atomic_record(state / 'source-fetch.json', dict(at=time.time(),
        boot_id=stage.boot_id(state), result='deferred', reason='network protection active'))
    return False


def healthy_runtime(settings, stage):
    """An intact snapshot alone does not prove that keeping its core is safe."""
    try:
        state, runtime = Path(settings['state']), Path(settings['runtime'])
        readiness = module('source_readiness', settings['readiness_tool'])
        boot = stage.boot_id(state)
        ready = readiness.readiness(root=state.parent, target='proxy', boot_id=boot,
            maximum=settings.get('state_max_age', 120),
            verification_maximum=settings.get('verification_max_age', 420))
        if ready.get('ready') is not True:
            return None
        digest = stage.verify(runtime / 'effective', state / 'config-digest',
                              state / 'authorized-config', fast=True)
        health = stage.read_record(state / 'health.json')
        paths = {'mixed-tcp'} | ({'fake-ip-tcp'} if settings.get('require_tun') else set())
        routing = stage.routing_binding(settings)
        if routing.get('routing_requested') == 'proxy-only':
            paths.add('real-address-dns')
        if (health.get('ok') is not True or health.get('boot_id') != boot
                or health.get('digest') != digest or health.get('policy_version') != ready['policy_version']
                or any(health.get(key) != value for key, value in routing.items())
                or not paths <= set(health.get('scope', {}).get('requests', []))
                or not readiness.fresh(health.get('monotonic_seconds'),
                    time.clock_gettime(time.CLOCK_BOOTTIME), settings.get('state_max_age', 120))):
            return None
        status = stage.service_request(settings, 'GET', '/status', timeout=1)
        pid = status.get('core_pid')
        if type(pid) is not int or pid <= 0:
            return None
        return digest
    except (OSError, ValueError, KeyError, TypeError, AttributeError,
            http.client.HTTPException, subprocess.SubprocessError):
        return None


def source_binding(settings, stage):
    inputs, config = stage.source_input(settings)
    source = stage.source_directory(settings)
    try:
        approved = stage.approved_root(settings, source)
        record = json.loads(stage.read_input(approved, 'settled.json', os.geteuid()))
        if not isinstance(record, dict) or not isinstance(record.get('digest'), str):
            raise stage.RefreshError('snapshot mismatch')
        baseline_id = record['digest']
    except FileNotFoundError:
        baseline_id = None
    baseline = None
    if baseline_id is not None:
        if not isinstance(baseline_id, str) or not re.fullmatch('[0-9a-f]{64}', baseline_id):
            raise stage.RefreshError('snapshot mismatch')
        baseline = approved / baseline_id
        stage.snapshot_metadata(baseline)
        if baseline.is_symlink() or stage.snapshot_digest(baseline) != baseline_id:
            raise stage.RefreshError('snapshot mismatch')
        manifest = json.loads(stage.read_input(baseline, 'source-manifest.json', os.geteuid()))
        if not isinstance(manifest, dict) or manifest.get('source_id') != source.name:
            raise stage.RefreshError('snapshot mismatch')
    identity = dict(source_id=source.name, generation=str(Path(config).parent),
        candidate=stage.snapshot_digest(Path(config).parent), baseline=baseline_id,
        revision=hashlib.sha256(stage.read_input(source, 'registry.json', os.geteuid())).hexdigest(),
        policy=hashlib.sha256(Path(settings['managed_policy']).read_bytes()).hexdigest(),
        validator_policy=hashlib.sha256(Path(os.environ.get(
            'LF_MANAGED_PROXY_POLICY', settings['managed_policy'])).read_bytes()).hexdigest())
    identity.update(stage.routing_binding(settings))
    return inputs, config, baseline, identity


def check_candidate(settings, stage, validate):
    """Return a decision without switching authority or sharing the live cache."""
    state = Path(settings['state'])
    findings, identity, digest = None, None, ''
    phase = 'input unreadable'
    try:
        inputs, config, baseline, identity = source_binding(settings, stage)
        relative = Path(config).relative_to(inputs['gui_data'])
        raw = stage.read_input(inputs['gui_data'], relative, inputs['desktop_uid'])
        digest = hashlib.sha256(raw).hexdigest()
        phase = 'configuration invalid'
        normalized = stage.config_input(inputs, config, raw)
        with tempfile.TemporaryDirectory(prefix='.source-check-',
                                         dir=settings.get('check_directory', state)) as temporary:
            root = Path(temporary)
            stage.private_dir(root, settings['core_gid'])
            candidate = root / 'candidate'
            stage.private_dir(candidate, settings['core_gid'])
            phase = 'input unreadable'
            stage.copy_candidate_inputs(inputs, normalized, candidate, settings['core_gid'])
            digest = stage.snapshot_digest(candidate)
            copied_baseline = None
            if baseline is not None:
                copied_baseline = root / 'baseline'
                stage.private_dir(copied_baseline, settings['core_gid'])
                stage.write_private(copied_baseline / 'clash-verge.yaml',
                    stage.read_input(baseline, 'clash-verge.yaml', os.geteuid()), settings['core_gid'])
            phase = 'configuration invalid'
            validate(candidate)
            phase = 'snapshot mismatch'
            if stage.snapshot_digest(candidate) != digest:
                raise stage.RefreshError('snapshot mismatch')
            previous = stage.read_record(state / 'profile-refresh.json')
            pin = stage.read_record(state / 'profile-refresh-pin.json')
            if (pin.get('health') is True or previous.get('result') in ('rolled_back', 'fallback')
                    and previous.get('digest') == digest and previous.get('reason') in
                    ('node', 'tun', 'dns', 'obs', 'recent health failure')) and stage.pinned(state, digest):
                raise stage.RefreshError('recent health failure')
            findings = stage.run_audit(settings, candidate, copied_baseline)
            if source_binding(settings, stage)[3] != identity:
                raise stage.RefreshError('snapshot mismatch')
            stage.auto_approve_semantics(settings, findings, digest, identity['baseline'])
            findings, _ = stage.apply_semantic_approval(settings, findings, digest, identity['baseline'])
            stage.enforce_audit(settings, findings)
            if stage.snapshot_digest(candidate) != digest:
                raise stage.RefreshError('snapshot mismatch')
        if source_binding(settings, stage)[3] != identity:
            raise stage.RefreshError('snapshot mismatch')
        return dict(accepted=True, digest=digest, binding=identity, findings=findings)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError) as error:
        reason = ('validation timeout' if isinstance(error, subprocess.TimeoutExpired) else
                  str(error) if isinstance(error, stage.RefreshError) and str(error) in stage.REASONS else
                  'input unreadable' if isinstance(error, OSError) else phase)
        return dict(accepted=False, digest=digest, binding=identity, findings=findings, reason=reason)


def restart_source(settings, stage, validate=None, activate=None, expected=None, lock_operator=None):
    state = Path(settings['state'])
    if validate is None:
        validate = lambda directory: stage.validate_snapshot(settings, settings['managed_core'],
            directory, state / 'source-validator.log')
    if activate is None:
        def activate():
            subprocess.run([settings['systemctl'], 'start', settings['guard_service']],
                           check=True, timeout=25)
            stage.restart_core(settings)
    with (state / 'operator.lock').open('a') as operator:
        if lock_operator is None:
            fcntl.flock(operator, fcntl.LOCK_EX)
        else:
            lock_operator(operator)
        # A competing network fetch must not hold up clash-off behind this lock.
        with stage.source_lock(settings, blocking=False) as acquired:
            if not acquired:
                return 'busy'
            if (state / 'intent').read_text().strip() != 'proxy':
                return 'proxy intent changed'
            if expected is not None:
                stage.prepared_source(settings, expected)
            with (state / 'authorization.lock').open('a') as authorization:
                fcntl.flock(authorization, fcntl.LOCK_EX)
                if any((state / name).exists() for name in ('profile-refresh-verifying.json',
                        'profile-refresh-restart.json', 'profile-refresh-commit.json')):
                    pending = stage.read_record(state / 'profile-refresh-verifying.json')
                    if expected is not None and pending.get('digest') == expected:
                        return 'verifying'
                    return 'busy'  # The existing acceptance/rollback worker owns it.
                current = healthy_runtime(settings, stage)
            # Keep the authority lock available to launch/sleep/recovery
            # writers while checks run. Recheck after reacquiring EX.
            decision = None
            if current is not None:
                settings = dict(settings, _prepare_deadline=time.monotonic() + 11)
                decision = check_candidate(settings, stage, validate)
            with (state / 'authorization.lock').open('a') as authorization:
                fcntl.flock(authorization, fcntl.LOCK_EX)
                if ((state / 'intent').read_text().strip() != 'proxy'
                        or any((state / name).exists() for name in ('profile-refresh-verifying.json',
                            'profile-refresh-restart.json', 'profile-refresh-commit.json'))):
                    return 'proxy intent or verification changed'
                if current is not None:
                    if (decision['accepted'] and decision['digest'] != current
                            and settings.get('refresh_policy', 'strict') == 'strict'):
                        decision.update(accepted=False, reason='operator authorization required')
                    still_healthy = healthy_runtime(settings, stage) == current
                    if still_healthy and not decision['accepted']:
                        previous = stage.read_record(state / 'profile-refresh.json')
                        if decision['digest'] and not stage.pinned(state, decision['digest']):
                            stage.pin_candidate(state, decision['digest'])
                        stage.audit(state, 'fallback' if settings.get('refresh_policy') == 'auto' else 'refused',
                            settings.get('refresh_policy', 'strict'),
                            decision['digest'], current, decision['reason'],
                            previous.get('snapshot_at', 0), decision['findings'])
                        return 'decided'
                    if still_healthy and decision['digest'] == current and decision['accepted']:
                        previous = stage.read_record(state / 'profile-refresh.json')
                        if previous.get('digest') != current or previous.get('result') != 'accepted':
                            stage.audit(state, 'accepted', settings.get('refresh_policy', 'strict'),
                                current, current, snapshot_at=previous.get('snapshot_at', 0),
                                findings=decision['findings'])
                        return 'accepted'
                    # SH source + EX operator/auth locks freeze the normal
                    # publishers. Bind one more time before any destructive step.
                    if decision['accepted'] and source_binding(settings, stage)[3] != decision['binding']:
                        return 'source changed during validation'
            # No precheck grants import authority. The helper revalidates and
            # reaudits the same source under SH, then health accepts or rolls back.
            activate()
            return 'verifying'


def verify_selection(settings, stage, candidate, initial_mode, lock_operator):
    """Wait outside the operator/source locks for this candidate's verdict."""
    state = Path(settings['state'])
    boot, started = stage.boot_id(state), time.time()

    def result(outcome, reason=None):
        source = stage.read_record(state / 'source.json')
        mode = stage.read_record(state / 'state.json').get('mode')
        active = source.get('source_id') if source.get('boot_id') == boot and mode != 'direct' else None
        return dict(result=outcome, reason=reason, active_source=active or None)

    activation_failed = False
    try:
        if initial_mode == 'direct':
            # Use the same protected startup and routing transaction as clash-on.
            subprocess.run(['/run/current-system/sw/bin/proxy-control', 'on'], check=True, timeout=120,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=dict(os.environ, LF_SOURCE_GENERATION=candidate))
        else:
            settings = dict(settings, core_gid=grp.getgrnam('clash-core').gr_gid)
            deadline = time.monotonic() + 90
            while True:
                decision = restart_source(settings, stage, expected=candidate, lock_operator=lock_operator)
                if decision != 'busy' or time.monotonic() >= deadline:
                    break
                time.sleep(1)
            if decision == 'accepted':
                return result('accepted')
            if decision not in ('verifying', 'decided'):
                return result('refused', decision)
    except stage.RefreshError as error:
        return result('refused', str(error) if str(error) in stage.REASONS else 'snapshot mismatch')
    except (OSError, ValueError, http.client.HTTPException, subprocess.SubprocessError):
        activation_failed = True

    if not activation_failed:
        try:
            subprocess.run([settings['systemctl'], 'start', '--no-block', 'profile-refresh-verify.service'],
                           check=False, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            pass  # The periodic verifier also owns this transaction.
    deadline = time.monotonic() + 100
    while True:
        audit = stage.read_record(state / 'profile-refresh.json')
        if audit.get('boot_id') == boot:
            for entry in reversed(audit.get('history', [])):
                if entry.get('digest') != candidate or entry.get('at', 0) < started:
                    continue
                verdict = entry.get('result')
                if verdict in ('accepted', 'refused', 'fallback', 'rolled_back'):
                    return result(verdict, entry.get('reason'))
                break
        if activation_failed or (state / 'intent').read_text().strip() != 'proxy':
            return result('refused', 'candidate activation failed' if activation_failed else 'proxy intent changed')
        if time.monotonic() >= deadline:
            return result('pending', 'verification timed out')
        time.sleep(1)


def main():
    if os.geteuid() != 0:
        raise ValueError('source refresh requires root')
    if sys.argv[1] == 'download-ready':
        settings = json.loads(Path(sys.argv[2]).read_text())
        stage = module('source_stage', settings['snapshot_tool'])
        raise SystemExit(0 if automatic_download_ready(settings, stage) else 1)
    settings = json.loads(Path(sys.argv[1]).read_text())
    settings.update(launcher=sys.argv[2], guard_service=sys.argv[3], core_gid=grp.getgrnam('clash-core').gr_gid)
    if len(sys.argv) > 4:
        settings['check_directory'] = sys.argv[4]
    stage = module('source_stage', settings['snapshot_tool'])
    restart_source(settings, stage)


if __name__ == '__main__':
    main()
