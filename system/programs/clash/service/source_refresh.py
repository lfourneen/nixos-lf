"""Check a downloaded candidate before disrupting a trusted, healthy core."""

from contextlib import contextmanager
import fcntl
import grp
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def network_mode(settings, stage):
    state = Path(settings['state'])
    record = stage.read_record(state / 'state.json')
    try:
        intent = (state / 'intent').read_text().strip()
    except FileNotFoundError:
        intent = 'auto'
    return record.get('mode'), intent, record.get('policy_version')


def authorization_epoch(state):
    try:
        return (state / 'authorization-epoch').read_text().strip()
    except FileNotFoundError:
        return ''


@contextmanager
def queue_lock(state):
    fd = os.open(state / 'source-refresh.lock', os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


@contextmanager
def download_job(state):
    fd = os.open(state / 'source-refresh-job.lock', os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def download_gate(settings, stage):
    state = Path(settings['state'])
    mode, intent, policy = network_mode(settings, stage)
    stable_path = state / 'source-refresh-stable.json'
    if mode == 'proxy' and intent == 'proxy':
        digest = healthy_runtime(settings, stage)
        if not digest:
            stable_path.unlink(missing_ok=True)
            return 'waiting for healthy proxy'
        now = time.clock_gettime(time.CLOCK_BOOTTIME)
        binding = dict(boot_id=stage.boot_id(state), digest=digest, policy_version=policy,
                       authorization_epoch=authorization_epoch(state))
        binding.update(stage.routing_binding(settings))
        stable = stage.read_record(stable_path)
        since = stable.get('since')
        if (type(since) not in (int, float) or not 0 <= since <= now
                or any(stable.get(key) != value for key, value in binding.items())):
            stage.atomic_record(stable_path, dict(binding, since=now), mode=0o600)
            since = now
        return 'waiting for stable proxy' if now - since < 90 else None
    stable_path.unlink(missing_ok=True)
    if mode == 'direct' and intent != 'proxy':
        return None
    return 'network protection active'


def schedule_download(settings, stage, *, consume=False, request=False, verified=True):
    state = Path(settings['state'])
    pending = state / 'source-refresh-pending'
    ready = state / 'source-refresh-ready'
    stable = state / 'source-refresh-stable.json'
    with queue_lock(state):
        if not verified:
            stable.unlink(missing_ok=True)
            ready.unlink(missing_ok=True)
        if request:
            pending.touch(mode=0o600)
        if consume:
            ready.unlink(missing_ok=True)
        if not pending.exists() and not (state / 'source-gui-request.json').exists():
            if stable.exists() and network_mode(settings, stage)[0] != 'proxy':
                stable.unlink()
            return False
        if (state / 'source-apply.json').exists():
            return False
        try:
            if verified:
                reason = download_gate(settings, stage)
            else:
                reason = ('network protection active' if network_mode(settings, stage)[0] == 'blocked'
                          else 'waiting for healthy proxy')
        except (OSError, ValueError):
            reason = 'waiting for healthy proxy'
            stable.unlink(missing_ok=True)
        if reason is not None:
            ready.unlink(missing_ok=True)
            boot = stage.boot_id(state)
            previous = stage.read_record(state / 'source-fetch.json')
            status = dict(boot_id=boot, result='deferred', reason=reason)
            if any(previous.get(key) != value for key, value in status.items()):
                stage.atomic_record(state / 'source-fetch.json', dict(status, at=time.time()))
            return False
        if consume:
            pending.unlink(missing_ok=True)
        elif not ready.exists():
            ready.touch(mode=0o600)
        return True


def cancel_automatic(settings):
    state = Path(settings['state'])
    with queue_lock(state):
        for name in ('source-refresh-pending', 'source-refresh-ready', 'source-refresh-stable.json',
                     'source-gui-request.json', 'source-apply.json', 'source-apply-status.json'):
            (state / name).unlink(missing_ok=True)


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
    baseline, baseline_id, _ = stage.persistent_source(settings)
    identity = dict(source_id=source.name, generation=str(Path(config).parent),
        candidate=stage.snapshot_digest(Path(config).parent), baseline=baseline_id or None,
        audit_context=stage.audit_context(settings),
        revision=hashlib.sha256(stage.read_input(source, 'registry.json', os.geteuid())).hexdigest(),
        policy=hashlib.sha256(Path(settings['managed_policy']).read_bytes()).hexdigest(),
        validator_policy=hashlib.sha256(Path(os.environ.get(
            'LF_MANAGED_PROXY_POLICY', settings['managed_policy'])).read_bytes()).hexdigest())
    identity.update(stage.routing_binding(settings))
    return inputs, config, baseline, identity


def selection_stamp(path):
    info = path.lstat()
    return [info.st_ino, info.st_ctime_ns]


def application_binding(settings, stage, expected):
    state = Path(settings['state'])
    root = Path(settings['source_root'])
    source = stage.source_directory(settings)
    _, config = stage.prepared_source(settings, expected)
    return dict(boot_id=stage.boot_id(state), source_id=source.name, candidate=expected,
                revision=hashlib.sha256(stage.read_input(source, 'registry.json', os.geteuid())).hexdigest(),
                selected=selection_stamp(root / 'selected'), current=selection_stamp(source / 'current'),
                generation=Path(config).parent.name, routing=stage.routing_binding(settings))


def queue_application(settings, stage, expected, at):
    state = Path(settings['state'])
    binding = application_binding(settings, stage, expected)
    with queue_lock(state):
        stage.atomic_record(state / 'source-apply-status.json',
                            dict(boot_id=binding['boot_id'], at=at, result='pending'))
        stage.atomic_record(state / 'source-apply.json', dict(binding=binding, at=at), mode=0o600)


def apply_queued(settings, stage, *, validate=None, activate=None, wait=time.sleep):
    state = Path(settings['state'])
    path = state / 'source-apply.json'

    def try_operator(lock):
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    while path.exists():
        request = stage.read_record(path)
        if not request:
            with queue_lock(state):
                if not stage.read_record(path):
                    path.unlink(missing_ok=True)
            continue
        try:
            binding = request['binding']
            at = request['at']
            boot = binding['boot_id']
            result = restart_source(settings, stage, validate, activate, expected=binding['candidate'],
                                    lock_operator=try_operator, request=request)
        except BlockingIOError:
            result = 'busy'
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException, subprocess.SubprocessError):
            at, boot = request.get('at'), stage.boot_id(state)
            result = 'discarded'
        if result == 'busy':
            wait(2)
            continue
        with queue_lock(state):
            if stage.read_record(path) == request:
                path.unlink()
                stage.atomic_record(state / 'source-apply-status.json', dict(
                    at=at, boot_id=boot,
                    result='submitted' if result in ('verifying', 'decided', 'accepted') else 'discarded'))


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


def restart_source(settings, stage, validate=None, activate=None, expected=None, lock_operator=None,
                   request=None):
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
            if request is not None:
                gui = stage.read_record(state / 'source-gui-request.json')
                if gui and gui.get('source') != request['binding']['source_id']:
                    return 'source selection changed'
            if request is None and expected is not None:
                stage.prepared_source(settings, expected)
            with (state / 'authorization.lock').open('a') as authorization:
                fcntl.flock(authorization, fcntl.LOCK_EX)
                verifying = any((state / name).exists() for name in ('profile-refresh-verifying.json',
                                'profile-refresh-restart.json', 'profile-refresh-commit.json'))
                if verifying:
                    pending = stage.read_record(state / 'profile-refresh-verifying.json')
                    if expected is None or pending.get('digest') != expected:
                        return 'busy'
                current = healthy_runtime(settings, stage) if not verifying else None
            if request is not None:
                if (stage.read_record(state / 'source-apply.json') != request
                        or application_binding(settings, stage, expected) != request['binding']):
                    return 'snapshot mismatch'
            if verifying:
                return 'verifying'
            # Keep the authority lock available to launch/sleep/recovery
            # writers while checks run. Recheck after reacquiring EX.
            decision = None
            if current is not None:
                settings = dict(settings, _prepare_deadline=time.monotonic() + 11)
                decision = check_candidate(settings, stage, validate)
            with (state / 'authorization.lock').open('a') as authorization:
                fcntl.flock(authorization, fcntl.LOCK_EX)
                if (state / 'intent').read_text().strip() != 'proxy':
                    return 'proxy intent changed'
                if any((state / name).exists() for name in ('profile-refresh-verifying.json',
                        'profile-refresh-restart.json', 'profile-refresh-commit.json')):
                    return 'busy'
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
    action, path, *arguments = sys.argv[1:]
    settings = json.loads(Path(path).read_text())
    stage = module('source_stage', settings['snapshot_tool'])
    if action == 'schedule':
        state = Path(settings['state'])
        with download_job(state) as idle:
            if idle:
                schedule_download(settings, stage, verified=not arguments)
            elif arguments:
                with queue_lock(state):
                    (state / 'source-refresh-stable.json').unlink(missing_ok=True)
    elif action == 'request':
        state = Path(settings['state'])
        with queue_lock(state):
            (state / 'source-refresh-pending').touch(mode=0o600)
    elif action == 'cancel':
        cancel_automatic(settings)
    elif action == 'apply':
        launcher, guard_service, check_directory = arguments
        settings.update(launcher=launcher, guard_service=guard_service, check_directory=check_directory,
                        core_gid=grp.getgrnam('clash-core').gr_gid)
        apply_queued(settings, stage)
    else:
        raise ValueError('unknown source refresh action')


if __name__ == '__main__':
    main()
