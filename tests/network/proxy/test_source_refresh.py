"""Source precheck and recovery regressions with real immutable snapshots."""
import copy
import fcntl
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
    def test_prepared_startup_rejects_a_replaced_generation(self):
        expected = stage.snapshot_digest(self.candidate)
        _, config = stage.prepared_source(self.settings, expected)
        self.assertEqual(Path(config).parent, self.candidate)
        (self.candidate / 'clash-verge.yaml').write_text(self.original)
        with self.assertRaisesRegex(stage.RefreshError, 'snapshot mismatch'):
            stage.prepared_source(self.settings, expected)

    def test_interrupted_active_revocation_refuses_a_restart_before_staging(self):
        stage.atomic_record(self.store / 'revoking.json', dict(source_id='one', protect=True), mode=0o600)
        validator = Mock()
        with self.assertRaisesRegex(ValueError, 'revocation is in progress'):
            stage.stage(self.settings, 'unused', validator)
        validator.assert_not_called()

    def test_manual_selection_uses_verifier_and_ignores_an_older_or_unrelated_verdict(self):
        candidate = 'a' * 64
        now = time.time()
        (self.state / 'state.json').write_text('{"mode":"proxy"}')
        (self.state / 'source.json').write_text(json.dumps(dict(
            boot_id=self.boot, source_id='source-one', snapshot=self.old)))
        audit = self.state / 'profile-refresh.json'
        history = [dict(digest=candidate, result='accepted', at=now - 30),
                   dict(digest='b' * 64, result='accepted', at=now + 1)]
        audit.write_text(json.dumps(dict(boot_id=self.boot, history=history)))

        def verify_round(*_):
            history.append(dict(digest=candidate, result='rolled_back', reason='obs', at=now + 2))
            audit.write_text(json.dumps(dict(boot_id=self.boot, history=history)))

        lock = Mock()
        with patch.object(refresh, 'restart_source', return_value='verifying') as restart, \
                patch.object(refresh.grp, 'getgrnam', return_value=Mock(gr_gid=os.getgid())), \
                patch.object(refresh.subprocess, 'run') as systemctl, \
                patch.object(refresh, 'schedule_download', side_effect=AssertionError('manual refresh must not defer')), \
                patch.object(refresh.time, 'sleep', side_effect=verify_round) as sleep:
            result = refresh.verify_selection(self.settings, stage, candidate, 'proxy', lock)
        self.assertEqual(restart.call_args.kwargs, dict(expected=candidate, lock_operator=lock))
        self.assertEqual(systemctl.call_args.args[0][-1], 'profile-refresh-verify.service')
        sleep.assert_called_once()
        self.assertEqual(result, dict(result='rolled_back', reason='obs', active_source='source-one'))

    def test_manual_selection_from_a_starts_the_prepared_generation_immediately(self):
        candidate = 'a' * 64

        def command(argv, **kwargs):
            if argv[-1] == 'on':
                self.assertEqual(kwargs['env']['LF_SOURCE_GENERATION'], candidate)
                stage.atomic_record(self.state / 'source.json', dict(
                    boot_id=self.boot, source_id='source-one', snapshot=self.old))
                stage.atomic_record(self.state / 'profile-refresh.json', dict(boot_id=self.boot,
                    history=[dict(digest=candidate, result='accepted', at=time.time())]))

        with patch.object(refresh.subprocess, 'run', side_effect=command) as systemctl, \
                patch.object(refresh, 'schedule_download', side_effect=AssertionError('manual refresh must not defer')):
            result = refresh.verify_selection(self.settings, stage, candidate, 'direct', Mock())
        self.assertEqual(systemctl.call_args_list[0].args[0], ['/run/current-system/sw/bin/proxy-control', 'on'])
        self.assertEqual(result, dict(result='accepted', reason=None, active_source='source-one'))

    def test_automatic_download_dispatches_once_after_ninety_healthy_seconds(self):
        (self.state / 'state.json').write_text('{"mode":"blocked"}')
        self.assertFalse(refresh.schedule_download(self.settings, stage, request=True))
        pending = self.state / 'source-refresh-pending'
        ready = self.state / 'source-refresh-ready'
        status = self.state / 'source-fetch.json'
        self.assertTrue(pending.exists())
        record = stage.read_record(status)
        self.assertEqual((record['result'], record['reason']), ('deferred', 'network protection active'))
        self.assertEqual(record['boot_id'], self.boot)
        self.assertFalse(refresh.schedule_download(self.settings, stage, request=True))
        self.assertEqual(record, stage.read_record(status))
        (self.state / 'state.json').write_text('{"mode":"proxy","policy_version":1}')
        with patch.object(refresh, 'healthy_runtime', return_value=self.old), \
                patch.object(refresh.time, 'clock_gettime', side_effect=[100, 189.9, 190, 191, 192]):
            self.assertFalse(refresh.schedule_download(self.settings, stage))
            self.assertFalse(refresh.schedule_download(self.settings, stage))
            self.assertFalse(ready.exists())
            self.assertTrue(refresh.schedule_download(self.settings, stage, request=True))
            stamp = ready.stat().st_mtime_ns
            self.assertTrue(refresh.schedule_download(self.settings, stage))
            self.assertEqual(ready.stat().st_mtime_ns, stamp)
            self.assertTrue(refresh.schedule_download(self.settings, stage, consume=True))
        self.assertFalse(ready.exists())
        self.assertFalse(pending.exists())
        self.assertFalse(refresh.schedule_download(self.settings, stage))

    def test_startup_download_stability_is_bound_to_health_boot_policy_and_snapshot(self):
        self.assertFalse(refresh.schedule_download(self.settings, stage, request=True))
        stable_path = self.state / 'source-refresh-stable.json'
        original = json.loads(stable_path.read_text())
        for changed in (dict(boot_id='old'), dict(policy_version=0), dict(digest='f' * 64),
                        dict(authorization_epoch='older'), dict(routing_epoch=8)):
            with patch.object(stage, 'routing_binding', return_value={'routing_epoch': 9}), \
                    patch.object(refresh, 'healthy_runtime', return_value=self.old), \
                    patch.object(refresh.time, 'clock_gettime', return_value=100):
                stable_path.write_text(json.dumps(dict(original, since=0, routing_epoch=9) | changed))
                self.assertFalse(refresh.schedule_download(self.settings, stage))
                self.assertEqual(stage.read_record(stable_path)['since'], 100)
        (self.state / 'state.json').write_text('{"mode":"blocked"}')
        self.assertFalse(refresh.schedule_download(self.settings, stage))
        self.assertFalse(stable_path.exists())
        refresh.cancel_automatic(self.settings)
        self.assertFalse((self.state / 'source-refresh-pending').exists())

    def test_automatic_download_requires_a_mode_matching_operator_intent(self):
        for mode, intent, expected in (('direct', 'direct', True), ('direct', 'auto', True),
                ('direct', 'proxy', False), ('proxy', 'direct', False),
                ('unenforced', 'proxy', False), ('blocked', 'proxy', False)):
            (self.state / 'state.json').write_text(json.dumps(dict(mode=mode)))
            (self.state / 'intent').write_text(intent)
            with self.subTest(mode=mode, intent=intent):
                self.assertEqual(refresh.schedule_download(self.settings, stage, request=True), expected)

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
        (self.store / 'selected').symlink_to(self.source)
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
        manifest = dict(source_id='one', schema=1, assets={}, yaml=hashlib.sha256(self.original.encode()).hexdigest(),
                        revision=hashlib.sha256((self.source / 'registry.json').read_bytes()).hexdigest(),
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
        responses = [ConnectionRefusedError(), dict(core_pid=None), {}, dict(core_pid=None), {}, dict(core_pid=5678)]
        with patch.object(stage, 'service_request', side_effect=responses), \
                patch.object(stage.subprocess, 'run') as systemctl, \
                patch.object(Path, 'read_text', return_value='5678'), \
                patch.object(stage.socket, 'socket'):
            stage.restart_core(dict(self.settings, gui_data='/fixed/gui'))
        systemctl.assert_called_once_with(['/fixed/systemctl', 'restart', 'clash-verge.service'],
            check=True, timeout=30)

    def test_start_source_waits_for_initial_helper_ipc_without_restarting_it(self):
        responses = [ConnectionRefusedError(), dict(core_pid=None), {}, dict(core_pid=5678)]
        with patch.object(stage, 'service_request', side_effect=responses), \
                patch.object(stage.subprocess, 'run') as systemctl, \
                patch.object(stage.time, 'sleep'), \
                patch.object(Path, 'read_text', return_value='5678'), \
                patch.object(stage.socket, 'socket'):
            stage.restart_core(dict(self.settings, gui_data='/fixed/gui'), restart=False)
        systemctl.assert_not_called()

    def test_start_source_ipc_timeout_fails_without_a_second_unit_start(self):
        with patch.object(stage, 'service_request', side_effect=ConnectionRefusedError()), \
                patch.object(stage.time, 'monotonic', side_effect=[0, 19]), \
                patch.object(stage.subprocess, 'run') as systemctl:
            with self.assertRaisesRegex(ValueError, 'timed out'):
                stage.restart_core(self.settings, restart=False)
        systemctl.assert_not_called()

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
                audit.load(baseline / 'clash-verge.yaml') if baseline else None, {'strict_semantics': True,
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

    def test_renamed_rotation_respects_all_auto_approval_categories(self):
        self.enable_automatic()
        renamed = self.changed.replace('Fixture', 'Renamed')
        (self.candidate / 'clash-verge.yaml').write_text(renamed)
        self.settings['audit_auto_approve'] = ['node_identity']
        self.run_refresh()
        self.assert_unchanged()
        self.assertFalse((self.source / 'approval.json').exists())
        self.settings['audit_auto_approve'] = sorted(stage.AUTO_APPROVAL_CATEGORIES)
        self.run_refresh()
        self.activate.assert_called_once()
        record = json.loads((self.source / 'approval.json').read_text())
        self.assertEqual(record['nodes'], 1)
        self.assertEqual(record['categories'], dict(node_identity=1, node_authentication=1, node_transport=1))

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
            (self.state / 'authorized-config', '0' * 64),
            (self.root / 'netsec/failed', 'nftables-verify'),
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

    def test_probe_loss_during_rejected_check_keeps_authorized_core(self):
        self.validate.side_effect = lambda _: (self.state / 'health.json').write_text('{}')
        with patch.object(stage, 'run_audit', return_value=DENY):
            self.run_refresh()
        self.assert_unchanged()

    def test_new_download_with_equivalent_forwarding_keeps_serving_during_probe_loss(self):
        (self.candidate / 'clash-verge.yaml').write_text(self.original)
        manifest_path = self.candidate / 'source-manifest.json'
        manifest = stage.read_record(manifest_path)
        manifest.update(responses={'subscription': 'a' * 64}, dropped=1, worker='b' * 64)
        manifest_path.write_text(json.dumps(manifest))
        (self.state / 'health.json').write_text('{}')
        before = (self.runtime / 'effective').readlink()
        result = refresh.restart_source(self.settings, stage, self.validate, self.activate)
        self.assertEqual(result, 'accepted')
        self.assert_unchanged()
        self.assertEqual((self.runtime / 'effective').readlink(), before)
        self.assertNotEqual(stage.snapshot_digest(self.candidate), self.old)
        self.validate.assert_called_once()
        for changed in (self.original.replace('old-secret', 'new-secret'),
                        self.original.replace('MATCH,Choice', 'MATCH,REJECT')):
            with self.subTest(changed=changed[-24:]):
                (self.candidate / 'clash-verge.yaml').write_text(changed)
                self.activate.reset_mock()
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

    def test_queued_generation_waits_for_source_lock_and_previous_acceptance(self):
        expected = stage.snapshot_digest(self.candidate)
        request = self.state / 'source-apply.json'
        refresh.queue_application(self.settings, stage, expected, 1)
        marker = self.state / 'profile-refresh-verifying.json'
        with (self.store / 'source.lock').open('a') as locked:
            fcntl.flock(locked, fcntl.LOCK_EX)
            def other_operation_finishes(_):
                self.assertTrue(request.exists())
                self.validate.assert_not_called()
                with (self.state / 'operator.lock').open('a') as operator:
                    fcntl.flock(operator, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if marker.exists():
                    marker.unlink()
                else:
                    marker.write_text('{}')
                    fcntl.flock(locked, fcntl.LOCK_UN)
            sleep = Mock(side_effect=other_operation_finishes)
            refresh.apply_queued(self.settings, stage, validate=self.validate, activate=self.activate, wait=sleep)
        self.assertEqual(sleep.call_count, 2)
        self.validate.assert_called_once()
        self.activate.assert_called_once()
        self.assertFalse(request.exists())
        self.assertEqual(stage.read_record(self.state / 'source-apply-status.json')['result'], 'submitted')

    def test_queued_generation_is_discarded_after_intent_selection_revision_or_current_changes(self):
        expected = stage.snapshot_digest(self.candidate)
        with patch.object(stage, 'run_audit', return_value=copy.deepcopy(PASS)):
            for name, path, value in (
                    ('intent', self.state / 'intent', 'direct'),
                    ('GUI selection', self.state / 'source-gui-request.json', '{"source":"two"}'),
                    ('registration', self.source / 'registry.json', '{"id":"one","revision":2}'),
                    ('current', self.source / 'current', self.candidate),
                    ('selection', self.store / 'selected', self.source)):
                with self.subTest(name=name):
                    refresh.queue_application(self.settings, stage, expected, 1)
                    if isinstance(value, Path):
                        path.unlink()
                        path.symlink_to(value)
                    else:
                        original = path.read_text() if path.exists() else None
                        path.write_text(value)
                    refresh.apply_queued(self.settings, stage, validate=self.validate, activate=self.activate)
                    self.assertEqual(stage.read_record(self.state / 'source-apply-status.json')['result'], 'discarded')
                    if isinstance(value, str):
                        if original is None:
                            path.unlink()
                        else:
                            path.write_text(original)
        self.assert_unchanged()
        self.validate.assert_not_called()

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


class SourceMigrationTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.state, self.runtime, self.data, self.store = (
            self.root / name for name in ('state', 'runtime', 'data', 'store'))
        self.source = self.store / 'sources/fixture'
        self.source.mkdir(mode=0o700, parents=True)
        for directory in (self.state, self.runtime, self.data):
            directory.mkdir()
        (self.data / 'cache.db').touch()
        (self.store / 'selected').symlink_to(self.source)
        self.boot = '00000000-1111-2222-3333-444444444444'
        (self.state / 'boot-id').write_text(self.boot)
        (self.state / 'intent').write_text('proxy')
        self.broker = module('migration_broker', os.environ['SOURCE_BROKER'])
        self.worker = module('migration_worker', os.environ['SOURCE_WORKER'])
        self.audit = module('migration_audit', os.environ['SOURCE_AUDIT'])
        self.policy = self.root / 'policy.json'
        self.audit_settings = self.root / 'audit.json'
        self.routing = self.state / 'routing.json'
        self.stage_settings = self.root / 'stage.json'
        self.settings = dict(snapshot_tool=stage.__file__, source_root=str(self.store), runtime=str(self.runtime),
            state=str(self.state), data=str(self.data), core_gid=os.getgid(), managed_policy=str(self.policy),
            routing_state=str(self.routing), refresh_policy='auto', audit_policy='enforce',
            audit_settings=str(self.audit_settings), audit_python=sys.executable, audit_tool=self.audit.__file__)
        self.source_settings = dict(root=str(self.store), policy=str(self.policy), worker=self.worker.__file__,
            stageSettings=str(self.stage_settings), routing_state=str(self.routing), python=sys.executable,
            setpriv='/fixture/setpriv', ca='/fixture/ca', assets={})
        root_input = stage.read_input
        write_private = stage.write_private
        for target, name, replacement in (
                (stage, 'source_directory', lambda *_: self.source),
                (stage, 'read_input', lambda path, file, uid: root_input(path, file, os.getuid() if uid == 0 else uid)),
                (stage, 'write_private', lambda path, data, gid, **options:
                    write_private(path, data, os.getgid() if gid == 0 else gid, **options)),
                (self.broker, 'read_private', lambda path, maximum=0: root_input(path.parent, path.name, os.getuid())),
                (self.broker, 'state_root', lambda path: path.mkdir(mode=0o700, exist_ok=True))):
            context = patch.object(target, name, replacement)
            context.start()
            self.addCleanup(context.stop)
        self.document = dict(proxies=[dict(name='remote', type='http', server='1.1.1.1', port=443)],
            **{'proxy-groups': [dict(name='Choice', type='select', proxies=['remote', 'DIRECT'])],
               'rules': ['DOMAIN,www.baidu.com,DIRECT', 'MATCH,Choice']})
        self.set_policy()
        self.set_domains('www.baidu.com')
        self.revision = self.register('first', 'none')

    def register(self, path, previous):
        data = self.broker.canonical(json.dumps({'id': 'fixture', 'subscription': {'url': 'https://fixture.example/' + path}}))
        revision = self.broker.digest(data)
        self.broker.register(self.store, data, revision, previous)
        return revision

    def set_policy(self, mode='rules', egress=None):
        strict = mode == 'proxy-only'
        self.policy.write_text(json.dumps({'lf-private-targets': {'proxy-only': strict, 'single-egress': strict}}))
        self.routing.write_text(json.dumps(dict(boot_id=self.boot, routing_requested=mode, routing_epoch=1,
            routing_policy_digest=stage.validator_policy_digest(self.settings), egress_epoch=1, egress_requested=egress)))

    def set_domains(self, name, extra=()):
        renderer = dict(forced_domains=['cp.cloudflare.com', name], probe_domains=[], remote_only_catchall=False)
        self.source_settings.update(renderer)
        self.settings['renderer_settings'] = renderer
        self.stage_settings.write_text(json.dumps(self.settings))
        self.audit_settings.write_text(json.dumps({'strict_semantics': True,
            'protected_routes': {domain: 'proxy' for domain in [*renderer['forced_domains'], *extra]}}))

    def download(self):
        def request(*_, **parameters):
            result = self.worker.prepare(json.loads(parameters['input']),
                download=lambda *_: json.dumps(self.document).encode())
            parameters['stdout'].write(json.dumps(result).encode())
            return subprocess.CompletedProcess([], 0)
        with patch.object(self.broker.subprocess, 'run', side_effect=request):
            self.broker.refresh_generation(self.source_settings, self.source)

    def cold(self):
        for name in ('config-digest', 'authorized-config', 'profile-refresh-settled.json'):
            (self.state / name).unlink(missing_ok=True)
        (self.state / 'state.json').write_text('{"mode":"blocked"}')
        (self.state / 'authorize-window').touch()

    def launch(self):
        stage.stage(self.settings, '/unused', lambda _: None)
        return stage.verify(self.runtime / 'effective', self.state / 'config-digest', self.state / 'authorized-config')

    def settle(self, healthy=True):
        pending = stage.read_record(self.state / 'profile-refresh-verifying.json')
        (self.state / 'state.json').write_text(json.dumps({'mode': 'proxy' if healthy else 'blocked'}))
        (self.state / 'health.json').write_text(json.dumps(dict(
            stage.routing_binding(self.settings), boot_id=self.boot, at=time.time(), ok=healthy, digest=pending['digest'])))
        stage.verify_pending_refresh(self.settings, pending)

    def seed(self):
        self.download()
        self.cold()
        identity = self.launch()
        self.settle()
        return identity

    def test_probe_migration_from_original_rules_is_idempotent(self):
        old = self.seed()
        before = self.audit.load(self.runtime / 'effective/clash-verge.yaml')
        self.assertEqual(self.audit.route(before, 'connectivity-check.ubuntu.com'), 'unknown')
        self.set_domains('connectivity-check.ubuntu.com')
        self.cold()
        self.broker.render_local(self.source_settings, self.source, 'rules', 'rules')
        new = self.launch()
        self.assertNotEqual(new, old)
        after = self.audit.load(self.runtime / 'effective/clash-verge.yaml')
        self.assertEqual(after['rules'][:4], ['IN-NAME,lf-strict,lf-remote', 'DOMAIN,cp.cloudflare.com,lf-remote',
            'DOMAIN,connectivity-check.ubuntu.com,lf-remote', 'DOMAIN,www.baidu.com,DIRECT'])
        self.assertEqual([self.audit.route(after, host) for host in
            ('cp.cloudflare.com', 'connectivity-check.ubuntu.com', 'www.baidu.com')], ['proxy', 'proxy', 'direct'])
        self.settle()
        self.cold()
        self.broker.render_local(self.source_settings, self.source, 'rules', 'rules')
        self.assertEqual(self.launch(), new)
        self.assertEqual(stage.read_record(self.state / 'profile-refresh.json')['result'], 'restored')
        self.assertFalse((self.state / 'profile-refresh-verifying.json').exists())

    def test_replaced_revision_is_comparison_only_in_rules_and_proxy_only(self):
        old = self.seed()
        self.cold()
        self.set_policy('proxy-only')
        egress = self.broker.render_local(self.source_settings, self.source, 'proxy-only', 'rules')
        self.set_policy('proxy-only', egress)
        strict = self.launch()
        self.settle()
        newer = self.register('second', self.revision)
        self.assertEqual(stage.snapshot_digest(self.runtime / 'effective'), strict)
        self.cold()
        with self.assertRaises(ValueError):
            self.broker.render_local(self.source_settings, self.source, 'proxy-only', 'proxy-only')
        with self.assertRaises(stage.RefreshError):
            self.launch()
        self.set_policy()
        self.set_domains('connectivity-check.ubuntu.com')
        with self.assertRaises(OSError):
            self.broker.render_local(self.source_settings, self.source, 'rules', 'proxy-only')
        self.download()
        self.broker.render_local(self.source_settings, self.source, 'rules', 'proxy-only')
        _, manifest = stage.current_generation(self.source)
        self.assertEqual(manifest['revision'], newer)
        self.assertEqual(stage.persistent_source(self.settings)[1], old)
        self.cold()
        current = self.launch()
        self.assertNotEqual(current, old)
        self.settle()
        self.cold()
        self.set_policy('proxy-only')
        egress = self.broker.render_local(self.source_settings, self.source, 'proxy-only', 'rules')
        self.set_policy('proxy-only', egress)
        self.launch()
        self.settle()
        manifest = stage.read_record(self.runtime / 'effective/source-manifest.json')
        self.assertEqual(manifest['revision'], newer)

    def test_incompatible_or_tampered_snapshot_cannot_rescue_unknown_candidate(self):
        old = self.seed()
        self.set_domains('connectivity-check.ubuntu.com', ('unproved.example',))
        self.cold()
        self.broker.render_local(self.source_settings, self.source, 'rules', 'rules')
        with self.assertRaisesRegex(stage.RefreshError, 'audit indeterminate'):
            self.launch()
        self.assertFalse((self.state / 'authorized-config').exists())
        self.assertEqual(stage.read_record(self.state / 'state.json')['mode'], 'blocked')
        approved = self.source / 'approved/rules' / old
        (approved / 'source-data.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'accepted source was modified'):
            self.broker.render_local(self.source_settings, self.source, 'rules', 'rules')

    def test_legacy_render_refuses_an_unrecognized_prefix_without_publishing_source_data(self):
        old = self.seed()
        folder = self.source / 'approved/rules'
        original = folder / old
        (original / 'source-data.json').unlink()
        manifest = stage.read_record(original / 'source-manifest.json')
        manifest.pop('source_data')
        (original / 'source-manifest.json').write_text(json.dumps(manifest))
        legacy = stage.snapshot_digest(original)
        original.rename(folder / legacy)
        stage.atomic_record(folder / 'settled.json', dict(digest=legacy, snapshot_at=time.time()), mode=0o600)
        (self.source / 'current').unlink()
        self.set_domains('connectivity-check.ubuntu.com')
        with self.assertRaisesRegex(ValueError, 'original Rule data is unavailable'):
            self.broker.render_local(self.source_settings, self.source, 'rules', 'rules')
        self.assertFalse((self.source / 'current').is_symlink())

    def test_health_rollback_is_bound_to_registration_and_current_audit(self):
        old = self.seed()
        self.document['rules'].insert(0, 'DOMAIN,fixture.example,DIRECT')
        self.download()
        self.assertNotEqual(self.launch(), old)
        self.settle(healthy=False)
        self.assertEqual((self.state / 'authorized-config').read_text().strip(), old)
        self.assertEqual(stage.read_record(self.state / 'profile-refresh.json')['result'], 'rolled_back')
        stage.clear_refresh(self.state, self.runtime, settings=self.settings)
        self.assertNotEqual(self.launch(), old)
        self.register('revoked', self.revision)
        self.settle(healthy=False)
        self.assertEqual(stage.read_record(self.state / 'profile-refresh.json')['result'], 'refused')
        self.assertFalse((self.state / 'authorized-config').exists())
        self.assertFalse((self.state / 'config-digest').exists())
        self.assertFalse((self.runtime / 'effective').is_symlink())
        with self.assertRaises(stage.RefreshError):
            self.launch()

    def test_selected_source_must_match_for_same_ready(self):
        self.seed()
        effective = (self.runtime / 'effective').resolve()
        other = self.store / 'sources/other'
        other.mkdir()
        (other / 'registry.json').write_text('{"id":"other"}')
        with patch.object(stage, 'source_directory',
                          side_effect=lambda settings, identity=None: self.source if identity == 'fixture' else other):
            self.assertTrue(stage.restart_eligible(self.settings, effective))
            self.assertFalse(stage.restart_eligible(self.settings, effective, selected=True))


if __name__ == '__main__':
    unittest.main(verbosity=2)
