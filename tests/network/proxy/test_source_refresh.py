"""Source precheck and recovery regressions with real immutable snapshots."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


stage = module('refresh_stage', sys.argv.pop(1))
refresh = module('source_refresh', sys.argv.pop(1))
readiness = sys.argv.pop(1)
SERVICE_REQUEST = stage.service_request
PASS = dict(verdict='pass', deny={}, warn={}, unknown={})
DENY = dict(verdict='deny', deny={'node_identity': 1}, warn={}, unknown={})


class SourceRefreshTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / 'proxy-mode'
        self.runtime = self.root / 'core'
        self.store = self.root / 'store'
        self.source = self.store / 'sources' / 'one'
        self.state.mkdir()
        self.runtime.mkdir()
        self.source.mkdir(parents=True, mode=0o700)
        (self.source / 'registry.json').write_text('{"id":"one"}')
        self.policy = self.root / 'policy.json'
        self.policy.write_text('{}')
        self.boot = '00000000-1111-2222-3333-444444444444'
        (self.state / 'boot-id').write_text(self.boot)
        (self.state / 'intent').write_text('proxy')
        now = time.clock_gettime(time.CLOCK_BOOTTIME)
        common = dict(mode='proxy', policy_version=1, monotonic_seconds=now)
        for relative, value in {
            'proxy-mode/state.json': common,
            'proxy-mode/heartbeat.json': common,
            'gost-relay/state.json': common,
            'dns-upstream/state.json': common,
            'dns-policy/state.json': dict(common, status='applied'),
            'netsec/state.json': dict(result='ok', version=1, monotonic_seconds=now, boot_id=self.boot),
        }.items():
            path = self.root / relative
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps(value))
        seed = self.root / 'seed'
        seed.mkdir()
        self.original = 'external-controller-unix: /run/clash-verge-rev/core/mihomo.sock\nmode: rule\n'
        (seed / 'clash-verge.yaml').write_text(self.original)
        manifest = dict(source_id='one', revision=hashlib.sha256((self.source / 'registry.json').read_bytes()).hexdigest())
        (seed / 'source-manifest.json').write_text(json.dumps(manifest))
        self.old = stage.snapshot_digest(seed)
        self.baseline = self.source / 'approved' / self.old
        shutil.copytree(seed, self.baseline)
        (self.source / 'approved/settled.json').write_text(json.dumps(dict(digest=self.old)))
        active = self.runtime / 'snapshots' / self.old
        shutil.copytree(seed, active)
        (self.runtime / 'effective').symlink_to(active)
        for name in ('authorized-config', 'config-digest'):
            (self.state / name).write_text(self.old)
        health = dict(common, boot_id=self.boot, ok=True, digest=self.old,
                      scope={'requests': ['mixed-tcp', 'fake-ip-tcp']})
        (self.state / 'health.json').write_text(json.dumps(health))
        self.candidate = self.source / 'generations' / ('a' * 64)
        shutil.copytree(seed, self.candidate)
        (self.candidate / 'clash-verge.yaml').write_text(self.original + '# changed\n')
        (self.source / 'current').symlink_to(self.candidate)
        self.settings = dict(state=str(self.state), runtime=str(self.runtime), source_root=str(self.store),
            core_gid=os.getgid(), managed_policy=str(self.policy), readiness_tool=readiness,
            refresh_policy='auto', audit_policy='enforce', require_tun=True,
            systemctl='/fixed/systemctl', guard_service='clash-source-guard.service', launcher='/fixed/launcher')
        def inputs(settings):
            directory = (self.source / 'current').resolve()
            return dict(settings, gui_data=str(directory), desktop_uid=os.getuid()), str(directory / 'clash-verge.yaml')
        for name, replacement in (
            ('source_directory', lambda *_: self.source), ('source_input', inputs),
            ('run_audit', lambda *_: copy.deepcopy(PASS)),
            ('service_request', lambda *_args, **_kwargs: {'core_pid': 1234}),
        ):
            context = patch.object(stage, name, replacement)
            context.start()
            self.addCleanup(context.stop)
        self.validate = Mock()
        self.activate = Mock()

    def run_refresh(self):
        refresh.restart_source(self.settings, stage, self.validate, self.activate)

    def assert_unchanged(self):
        self.activate.assert_not_called()
        self.assertEqual(stage.verify(self.runtime / 'effective', self.state / 'config-digest',
                                      self.state / 'authorized-config'), self.old)
        self.assertEqual(json.loads((self.state / 'state.json').read_text())['policy_version'], 1)
        self.assertFalse(list(self.state.glob('.source-check-*')))

    def test_audit_rejection_keeps_current_authority_and_does_not_renew_pin(self):
        with patch.object(stage, 'run_audit', return_value=DENY):
            self.run_refresh()
            first_pin = (self.state / 'profile-refresh-pin.json').read_bytes()
            self.run_refresh()
        self.assert_unchanged()
        self.assertEqual(first_pin, (self.state / 'profile-refresh-pin.json').read_bytes())
        record = json.loads((self.state / 'profile-refresh.json').read_text())
        self.assertEqual((record['result'], record['reason']), ('fallback', 'blocked by audit'))

    def test_real_helper_protocol_returns_an_unwrapped_core_pid(self):
        import http.server
        import socketserver
        import threading
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                expected = 'A thing of beauty is a joy for ever. Its loveliness increases; it will never pass into nothingness.'
                self.server.seen_magic = self.headers.get('X-IPC-Magic') == expected
                body = json.dumps({'code': 0, 'data': {'core_pid': 1234}}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_):
                pass
        with socketserver.UnixStreamServer(str(self.runtime / 'service.sock'), Handler) as server:
            worker = threading.Thread(target=server.handle_request, daemon=True)
            worker.start()
            with patch.object(stage, 'service_request', SERVICE_REQUEST):
                self.assertEqual(refresh.healthy_runtime(self.settings, stage), self.old)
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertTrue(server.seen_magic)

    def test_invalid_parser_timeout_and_unknown_audit_keep_healthy_core(self):
        for error, reason in ((stage.RefreshError('configuration invalid'), 'configuration invalid'),
                              (subprocess.TimeoutExpired('validator', 11), 'validation timeout')):
            self.validate.side_effect = error
            self.run_refresh()
            self.assert_unchanged()
            self.assertEqual(json.loads((self.state / 'profile-refresh.json').read_text())['reason'], reason)
        self.validate.side_effect = None
        with patch.object(stage, 'run_audit', return_value=dict(PASS, unknown={'route_unknown': 1})):
            self.run_refresh()
        self.assertEqual(json.loads((self.state / 'profile-refresh.json').read_text())['reason'], 'audit indeterminate')
        self.assert_unchanged()

    def test_accepted_candidate_is_checked_without_publishing_import_authority(self):
        self.run_refresh()
        self.validate.assert_called_once()
        self.activate.assert_called_once()
        self.assertEqual((self.state / 'config-digest').read_text(), self.old)
        self.assertFalse((self.state / 'profile-refresh-verifying.json').exists())

    def test_exact_approval_is_retained_for_actual_import(self):
        approval = self.source / 'approval.json'
        approval.write_text('{"fixture":"approval for import"}')
        def approve(settings, findings, candidate, baseline):
            self.assertEqual(candidate, stage.snapshot_digest(self.candidate))
            self.assertEqual(baseline, self.old)
            return dict(verdict='warn', deny={}, warn={'node_identity': 1}, unknown={}), True
        with patch.object(stage, 'run_audit', return_value=DENY), \
                patch.object(stage, 'apply_semantic_approval', side_effect=approve):
            self.run_refresh()
        self.activate.assert_called_once()
        self.assertTrue(approval.exists(), 'precheck consumed the import approval')

    def test_corrupt_baseline_is_not_treated_as_a_first_import(self):
        for value in ('{}', '{"digest":null}', '[]', 'not-json'):
            (self.source / 'approved/settled.json').write_text(value)
            self.run_refresh()
            self.assert_unchanged()
            self.validate.assert_not_called()

    def test_registration_policy_source_and_baseline_changes_cannot_restart(self):
        mutations = [
            (self.source / 'registry.json', '{"id":"one","changed":true}'),
            (self.policy, '{"changed":true}'),
            (self.candidate / 'clash-verge.yaml', self.original + '# replaced during validation\n'),
            (self.source / 'approved/settled.json', '{"digest":"' + 'b' * 64 + '"}'),
        ]
        for path, replacement in mutations:
            original = path.read_bytes()
            self.validate.side_effect = lambda _, path=path, replacement=replacement: path.write_text(replacement)
            try:
                self.run_refresh()
                self.assert_unchanged()
            finally:
                path.write_bytes(original)

    def test_strict_changed_input_requires_operator_but_identical_input_is_not_restarted(self):
        self.settings['refresh_policy'] = 'strict'
        self.run_refresh()
        self.assert_unchanged()
        record = json.loads((self.state / 'profile-refresh.json').read_text())
        self.assertEqual((record['result'], record['reason']), ('refused', 'operator authorization required'))
        (self.candidate / 'clash-verge.yaml').write_text(self.original)
        self.run_refresh()
        self.assert_unchanged()
        self.assertEqual(json.loads((self.state / 'profile-refresh.json').read_text())['result'], 'accepted')

    def test_unhealthy_or_tampered_current_core_uses_existing_recovery(self):
        for path, replacement in (
            (self.state / 'health.json', '{}'),
            (self.state / 'authorized-config', '0' * 64),
            (self.root / 'netsec/failed', 'nftables-verify'),
            (self.state / 'health.json', json.dumps(dict(ok=True, scope={'requests': []}))),
        ):
            original = path.read_bytes() if path.exists() else None
            self.activate.reset_mock()
            self.validate.reset_mock()
            path.write_text(replacement)
            try:
                self.run_refresh()
                self.activate.assert_called_once()
                self.validate.assert_not_called()
            finally:
                if original is None: path.unlink()
                else: path.write_bytes(original)

    def test_current_core_failure_during_rejected_check_still_recovers(self):
        self.validate.side_effect = lambda _: (self.state / 'health.json').write_text('{}')
        with patch.object(stage, 'run_audit', return_value=DENY):
            self.run_refresh()
        self.activate.assert_called_once()

    def test_validation_keeps_authority_lock_available_to_other_writers(self):
        import fcntl
        def validate(_):
            with (self.state / 'authorization.lock').open('a') as authorization:
                fcntl.flock(authorization, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.validate.side_effect = validate
        with patch.object(stage, 'run_audit', return_value=DENY):
            self.run_refresh()
        self.assert_unchanged()

    def test_expired_authorization_does_not_preserve_an_untrusted_b_state(self):
        policy = json.loads((self.state / 'state.json').read_text())
        policy['authorization_lease_seconds'] = 60
        (self.state / 'state.json').write_text(json.dumps(policy))
        # There is no live kernel-lease record for this state.
        self.run_refresh()
        self.activate.assert_called_once()
        self.validate.assert_not_called()

    def test_pending_acceptance_and_busy_fetch_defer_without_operator_lock_leak(self):
        import fcntl
        marker = self.state / 'profile-refresh-verifying.json'
        marker.write_text('{}')
        self.run_refresh()
        marker.unlink()
        with (self.store / 'source.lock').open('a') as locked:
            fcntl.flock(locked, fcntl.LOCK_EX)
            self.run_refresh()
        self.assert_unchanged()
        self.validate.assert_not_called()
        with (self.state / 'operator.lock').open('a') as operator:
            fcntl.flock(operator, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_production_activation_arms_c_before_restart(self):
        calls = []
        def request(arguments, **kwargs):
            calls.append(arguments)
        with patch.object(refresh.subprocess, 'run', side_effect=request), \
                patch.object(stage, 'restart_core', side_effect=lambda _: calls.append('restart')):
            refresh.restart_source(self.settings, stage, self.validate)
        self.assertEqual(calls, [[self.settings['systemctl'], 'start', self.settings['guard_service']], 'restart'])
        with patch.object(refresh.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'guard')), \
                patch.object(stage, 'restart_core') as restart:
            with self.assertRaises(subprocess.CalledProcessError):
                refresh.restart_source(self.settings, stage, self.validate)
            restart.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
