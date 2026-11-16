"""Source precheck and recovery regressions with real immutable snapshots."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
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
    def test_automatic_download_defers_in_c_then_consumes_one_pending_request_in_b(self):
        (self.state / 'state.json').write_text('{"mode":"blocked"}')
        with patch.object(refresh.subprocess, 'run', side_effect=AssertionError('C must not try a download')):
            self.assertFalse(refresh.automatic_download_ready(self.settings, stage))
        pending = self.state / 'source-refresh-pending'
        self.assertTrue(pending.exists())
        record = json.loads((self.state / 'source-fetch.json').read_text())
        self.assertEqual((record['result'], record['reason']), ('deferred', 'network protection active'))
        self.assertEqual(record['boot_id'], self.boot)
        (self.state / 'state.json').write_text(json.dumps(dict(mode='proxy', policy_version=1,
            monotonic_seconds=time.clock_gettime(time.CLOCK_BOOTTIME))))
        self.assertFalse(refresh.automatic_download_ready(self.settings, stage))
        stable_path = self.state / 'source-refresh-stable.json'
        stable = json.loads(stable_path.read_text())
        stable['since'] -= 91
        stable_path.write_text(json.dumps(stable))
        self.assertTrue(refresh.automatic_download_ready(self.settings, stage))
        self.assertFalse(pending.exists(), 'activation must not enqueue another download on its own B transition')
        self.assertTrue(refresh.automatic_download_ready(self.settings, stage))
        self.assertFalse(pending.exists())

    def test_startup_download_stability_is_bound_to_health_boot_policy_and_snapshot(self):
        self.assertFalse(refresh.automatic_download_ready(self.settings, stage))
        stable_path = self.state / 'source-refresh-stable.json'
        original = json.loads(stable_path.read_text())
        for changed in (dict(boot_id='old'), dict(policy_version=0), dict(digest='f' * 64)):
            stable_path.write_text(json.dumps(dict(original, since=original['since'] - 100, **changed)))
            self.assertFalse(refresh.automatic_download_ready(self.settings, stage))
        (self.state / 'state.json').write_text('{"mode":"blocked"}')
        self.assertFalse(refresh.automatic_download_ready(self.settings, stage))
        self.assertFalse(stable_path.exists())

    def test_automatic_download_requires_a_mode_matching_operator_intent(self):
        for mode, intent, expected in (('direct', 'direct', True), ('direct', 'auto', True),
                ('direct', 'proxy', False), ('proxy', 'direct', False),
                ('unenforced', 'proxy', False), ('blocked', 'proxy', False)):
            (self.state / 'state.json').write_text(json.dumps(dict(mode=mode)))
            (self.state / 'intent').write_text(intent)
            with self.subTest(mode=mode, intent=intent):
                self.assertEqual(refresh.automatic_download_ready(self.settings, stage), expected)

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
        self.original = ('external-controller-unix: /run/clash-verge-rev/core/mihomo.sock\nmode: rule\n'
            'proxies: [{name: Fixture, type: vless, server: 1.1.1.1, lf-source-server: old-prefix.entry.example, port: 443, uuid: old-secret}]\n'
            "proxy-groups: [{name: Choice, type: select, proxies: [Fixture]}]\nrules: ['MATCH,Choice']\n")
        (seed / 'clash-verge.yaml').write_text(self.original)
        manifest = dict(source_id='one', revision=hashlib.sha256((self.source / 'registry.json').read_bytes()).hexdigest(),
                        policy=hashlib.sha256(self.policy.read_bytes()).hexdigest())
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

    def test_core_restart_uses_stop_then_start_without_restarting_helper(self):
        calls = []
        pid = 1234
        def ipc(_settings, method, endpoint, payload=None, **_kwargs):
            nonlocal pid
            calls.append((method, endpoint))
            if method == 'DELETE':
                pid = None
            elif method == 'POST':
                self.assertEqual(payload['core_config']['core_path'], '/fixed/launcher')
                pid = 5678
            return dict(core_pid=pid)
        settings = dict(self.settings, gui_data='/fixed/gui')
        with patch.object(stage, 'service_request', side_effect=ipc), \
                patch.object(stage.subprocess, 'run') as systemctl, \
                patch.object(Path, 'read_text', return_value='5678'), \
                patch.object(stage.socket, 'socket'):
            stage.restart_core(settings)
        self.assertEqual(calls, [('GET', '/status'), ('DELETE', '/clash/stop'),
            ('GET', '/status'), ('POST', '/clash/start'), ('GET', '/status')])
        systemctl.assert_not_called()

    def test_helper_rejection_does_not_trigger_a_unit_restart(self):
        with patch.object(stage, 'service_request', side_effect=ValueError('refused')), \
                patch.object(stage.subprocess, 'run') as systemctl:
            with self.assertRaises(ValueError):
                stage.restart_core(self.settings)
        systemctl.assert_not_called()

    def test_missing_helper_transport_recovers_unit_then_starts_fixed_core(self):
        responses = [ConnectionRefusedError(), dict(core_pid=None), {}, dict(core_pid=5678)]
        with patch.object(stage, 'service_request', side_effect=responses), \
                patch.object(stage.subprocess, 'run') as systemctl, \
                patch.object(Path, 'read_text', return_value='5678'), \
                patch.object(stage.socket, 'socket'):
            stage.restart_core(dict(self.settings, gui_data='/fixed/gui'), restart=False)
        systemctl.assert_called_once_with(['/fixed/systemctl', 'restart', 'clash-verge.service'],
            check=True, timeout=30)

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

    def enable_automatic(self):
        audit = module('automatic_audit', os.environ['SOURCE_AUDIT'])
        audit_settings = self.root / 'audit-settings.json'
        audit_settings.write_text('{"strict_semantics":true}')
        self.settings.update(audit_auto_approve=sorted(stage.AUTO_APPROVAL_CATEGORIES),
                             audit_settings=str(audit_settings))
        # Keep real file and permission checks; map only root ownership to the
        # unprivileged builder. The VM exercises actual root-owned approvals.
        read, lstat = stage.read_input, Path.lstat
        def owned_input(directory, relative, uid):
            return read(directory, relative, os.getuid() if uid == 0 else uid)
        def owned_stat(path):
            info = lstat(path)
            if path.name == 'approval.json':
                fields = list(info)
                fields[4] = 0
                return os.stat_result(fields)
            return info
        def findings(settings, candidate, baseline):
            return audit.audit(audit.load(candidate / 'clash-verge.yaml'),
                audit.load(baseline / 'clash-verge.yaml'), {'strict_semantics': True,
                    'protected_routes': {'account.example': 'proxy'}})
        for target, name, replacement in ((stage, 'read_input', owned_input),
                (Path, 'lstat', owned_stat), (stage, 'run_audit', findings)):
            context = patch.object(target, name, replacement)
            context.start()
            self.addCleanup(context.stop)
        self.changed = self.original.replace('old-prefix.entry', 'new-prefix.entry')
        (self.candidate / 'clash-verge.yaml').write_text(self.changed)

    def test_rotation_is_automatically_bound_then_consumed_by_normal_import(self):
        self.enable_automatic()
        self.run_refresh()
        self.activate.assert_called_once()
        approval = self.source / 'approval.json'
        record = json.loads(approval.read_text())
        self.assertEqual(record['origin'], 'automatic')
        self.assertEqual(record['categories'], {'node_identity': 1})
        self.assertEqual(record['nodes'], 1)
        self.assertEqual(record['baseline'], self.old)
        self.assertEqual(stat.S_IMODE(approval.stat().st_mode), 0o600)
        self.assertLessEqual(record['expires'] - time.time(), 300)
        data = self.root / 'data'
        data.mkdir()
        (data / 'cache.db').touch()
        self.settings['data'] = str(data)
        (self.source / 'approved/settled.json').write_text(json.dumps(dict(digest=self.old, snapshot_at=time.time())))
        stage.stage(self.settings, str(self.candidate / 'clash-verge.yaml'), self.validate)
        self.assertFalse(approval.exists(), 'normal import must consume the approval')
        imported = json.loads((self.state / 'profile-refresh.json').read_text())
        self.assertEqual(imported['result'], 'verifying')
        self.assertEqual(imported['last_auto_approval']['nodes'], 1)
        self.assertEqual(imported['audit']['deny'], {})
        self.assertEqual(imported['audit']['warn'], {'node_identity': 1})
        self.assertNotEqual(imported['digest'], self.old)

    def test_auto_approval_counts_each_changed_node_once(self):
        self.enable_automatic()
        changed = self.changed.replace('old-secret', 'new-secret').replace('port: 443', 'port: 8443, tls: true')
        (self.candidate / 'clash-verge.yaml').write_text(changed)
        self.run_refresh()
        value = json.loads((self.source / 'approval.json').read_text())
        self.assertEqual(value['nodes'], 1)
        self.assertEqual(value['categories'], dict(node_identity=1, node_authentication=1, node_transport=1))

    def test_first_clash_on_after_reboot_restores_accepted_then_imports_in_background(self):
        self.enable_automatic()
        data = self.root / 'data'
        data.mkdir()
        (data / 'cache.db').touch()
        self.settings['data'] = str(data)
        (self.source / 'approved/settled.json').write_text(json.dumps(dict(digest=self.old, snapshot_at=time.time())))
        for name in ('authorized-config', 'config-digest', 'health.json'):
            (self.state / name).unlink()
        (self.state / 'authorize-window').touch()
        self.assertFalse((self.source / 'approval.json').exists())
        stage.stage(self.settings, str(self.candidate / 'clash-verge.yaml'), self.validate)
        record = json.loads((self.state / 'profile-refresh.json').read_text())
        self.assertEqual(record['result'], 'restored')
        self.assertEqual(record['digest'], self.old)
        self.assertGreater(record['last_accepted_at'], 0)
        self.assertFalse((self.state / 'profile-refresh-verifying.json').exists())
        self.assertFalse((self.source / 'approval.json').exists())
        # The background activation has no operator grant. It still audits
        # and observes the new candidate before accepting it.
        stage.stage(self.settings, str(self.candidate / 'clash-verge.yaml'), self.validate)
        record = json.loads((self.state / 'profile-refresh.json').read_text())
        self.assertEqual(record['result'], 'verifying')
        self.assertEqual(record['last_auto_approval']['categories'], {'node_identity': 1})
        self.assertNotEqual(record['digest'], self.old)
        self.assertFalse((self.source / 'approval.json').exists())

    def test_startup_does_not_restore_corrupt_or_obsolete_policy_snapshots(self):
        self.enable_automatic()
        self.assertTrue(stage.startup_input(self.settings)[0]['_accepted_startup'])
        (self.baseline / 'clash-verge.yaml').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'snapshot mismatch'):
            stage.startup_input(self.settings)
        (self.baseline / 'clash-verge.yaml').write_text(self.original)
        self.policy.write_text('{"changed":true}')
        selected, path = stage.startup_input(self.settings)
        self.assertNotIn('_accepted_startup', selected)
        self.assertEqual(Path(path).parent, self.candidate)

    def test_accepted_policy_allows_only_the_validated_blocklist_location_change(self):
        previous = '/nix/store/' + 'a' * 32 + '-dns-blocklist.txt'
        policy = {'lf-private-targets': {'blocked-domains-file': previous, 'proxy-only': True}}
        encode = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
        manifest = dict(policy=hashlib.sha256(encode(policy)).hexdigest())
        policy['lf-private-targets']['blocked-domains-file'] = '/etc/netsec/dns-blocklist.txt'
        self.policy.write_bytes(encode(policy))
        settings = dict(self.settings, yq='/fixed/yq')
        with patch.object(stage.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0,
                json.dumps(previous), '')):
            self.assertTrue(stage.accepted_policy(settings, self.baseline, manifest))
            policy['lf-private-targets']['proxy-only'] = False
            self.policy.write_bytes(encode(policy))
            self.assertFalse(stage.accepted_policy(settings, self.baseline, manifest))
        with patch.object(stage.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0,
                json.dumps('/tmp/untrusted-list'), '')):
            self.assertFalse(stage.accepted_policy(settings, self.baseline, manifest))

    def test_health_rejection_pin_survives_an_explicit_restart_and_cannot_be_auto_approved(self):
        self.enable_automatic()
        digest = stage.snapshot_digest(self.candidate)
        stage.pin_candidate(self.state, digest, health=True)
        pin = (self.state / 'profile-refresh-pin.json').read_bytes()
        stage.clear_refresh(self.state, self.runtime, keep_health_pin=True)
        self.run_refresh()
        self.assert_unchanged()
        self.assertEqual(pin, (self.state / 'profile-refresh-pin.json').read_bytes())
        self.assertFalse((self.source / 'approval.json').exists())
        record = json.loads((self.state / 'profile-refresh.json').read_text())
        self.assertEqual(record['reason'], 'recent health failure')

    def test_auto_approval_refuses_structural_and_unknown_findings(self):
        self.enable_automatic()
        for category in sorted(stage.AUDIT_CATEGORIES - stage.AUTO_APPROVAL_CATEGORIES):
            for field in ('deny', 'unknown'):
                findings = dict(verdict='deny', deny={'node_identity': 1}, warn={}, unknown={}, changed_nodes=1)
                findings[field][category] = 1
                with self.subTest(category=category, field=field), patch.object(stage, 'run_audit', return_value=findings):
                    self.run_refresh()
                self.assert_unchanged()
                self.assertFalse((self.source / 'approval.json').exists())

    def test_injected_content_risks_still_fall_back_without_approval(self):
        self.enable_automatic()
        for text in (self.changed.replace('1.1.1.1', '169.254.169.254'),
                     self.changed.replace('port: 443', 'port: 443, skip-cert-verify: true'),
                     self.changed + 'hosts: {account.example: 1.1.1.1}\n',
                     self.changed.replace("rules: ['MATCH,Choice']", "rules: ['DOMAIN,account.example,DIRECT', 'MATCH,Choice']"),
                     self.changed + 'rules: [MATCH,DIRECT]\n'):
            (self.candidate / 'clash-verge.yaml').write_text(text)
            self.run_refresh()
            self.assert_unchanged()
            self.assertFalse((self.source / 'approval.json').exists())
        self.assertEqual(json.loads((self.state / 'profile-refresh.json').read_text())['result'], 'fallback')

    def test_binding_expiry_permissions_and_policy_changes_reject_auto_records(self):
        self.enable_automatic()
        self.run_refresh()
        approval = self.source / 'approval.json'
        original = approval.read_bytes()
        findings = dict(DENY, changed_nodes=1)
        candidate = stage.snapshot_digest(self.candidate)
        for field, bad in (('candidate', '0' * 64), ('baseline', '0' * 64), ('revision', '0' * 64),
                ('policy', '0' * 64), ('validator_policy', '0' * 64), ('audit_policy', '0' * 64),
                ('expires', time.time() - 1), ('expires', time.time() + 7200),
                ('categories', {'certificate': 1}), ('nodes', 99)):
            value = json.loads(original)
            value[field] = bad
            approval.write_text(json.dumps(value))
            with self.subTest(field=field, bad=bad):
                self.assertFalse(stage.apply_semantic_approval(self.settings, findings, candidate, self.old)[1])
        approval.write_bytes(original)
        approval.chmod(0o644)
        self.assertFalse(stage.apply_semantic_approval(self.settings, findings, candidate, self.old)[1])
        approval.chmod(0o600)
        for override in ({'audit_auto_approve': []}, {'refresh_policy': 'strict'}, {'audit_policy': 'off'}):
            self.assertFalse(stage.apply_semantic_approval(dict(self.settings, **override), findings, candidate, self.old)[1])
        self.assertTrue(stage.apply_semantic_approval(self.settings, findings, candidate, self.old)[1])
        # Older manual records retain their original list-based format.
        legacy = json.loads(original)
        for key in ('origin', 'at', 'nodes', 'audit_policy', 'validator_policy'):
            legacy.pop(key)
        legacy['categories'] = ['node_identity']
        approval.write_text(json.dumps(legacy))
        self.assertTrue(stage.apply_semantic_approval(dict(self.settings, audit_auto_approve=[]), findings, candidate, self.old)[1])

    def test_empty_allowlist_preserves_denial_and_rejects_changed_source_binding(self):
        self.enable_automatic()
        self.settings['audit_auto_approve'] = []
        self.run_refresh()
        self.assert_unchanged()
        self.assertFalse((self.source / 'approval.json').exists())
        self.settings['audit_auto_approve'] = ['node_identity']
        self.validate.side_effect = lambda _: self.policy.write_text('{"changed":true}')
        self.run_refresh()
        self.assert_unchanged()
        self.assertFalse((self.source / 'approval.json').exists())

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
