"""Authorization/permission regressions using real files, without root or VM."""

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

source = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else (
    Path(__file__).resolve().parents[3] / 'system/programs/clash/service/clash-verge-stage.py')
health_source = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else Path(__file__).resolve().parents[3] / 'system/config/network/proxy/health.py'
spec = importlib.util.spec_from_file_location('stage', source)
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        read = Path.read_text
        def boot_identity(path, *args, **kwargs):
            if str(path) in ('/proc/sys/kernel/random/boot_id', '/run/proxy-mode/boot-id'):
                return 'fixture-boot'
            return read(path, *args, **kwargs)
        self.boot_patch = patch.object(Path, 'read_text', boot_identity)
        self.boot_patch.start()
        self.addCleanup(self.boot_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gui, self.runtime, self.state, self.data = [self.root / n for n in ('gui', 'run', 'state', 'data')]
        for path in (self.gui, self.runtime, self.state, self.data):
            path.mkdir()
        (self.state / 'boot-id').write_text('00000000-1111-2222-3333-444444444444\n')
        self.settings = dict(gui_data=str(self.gui), runtime=str(self.runtime), state=str(self.state),
                             data=str(self.data), desktop_uid=os.getuid(), core_gid=os.getgid())
        (self.state / 'intent').write_text('proxy\n')
        self.config = self.gui / 'clash-verge.yaml'
        self.config.write_text('external-controller-unix: /old.sock\nmode: rule\nrules: [MATCH,Proxy]\n')
        (self.gui / 'geosite.dat').write_bytes(b'authorized fixture database')
        (self.data / 'cache.db').touch()
        self.validated = []

    def validate(self, directory):
        self.validated.append(directory)
        self.assertTrue((directory / 'clash-verge.yaml').is_file())

    def authorize(self, validate=None):
        for name in ('authorized-config', 'config-digest'):
            (self.state / name).unlink(missing_ok=True)
        (self.state / 'authorize-window').touch()
        stage.stage(self.settings, str(self.config), validate or self.validate)

    def restart(self):
        stage.stage(self.settings, str(self.config), self.validate)

    def verify(self):
        return stage.verify(self.runtime / 'effective', self.state / 'config-digest', self.state / 'authorized-config')

    def test_provider_cache_is_separate_from_snapshot_authority(self):
        self.authorize()
        effective=self.runtime/'effective'
        self.assertEqual((effective/'proxies').readlink(),self.data/'proxies')
        (self.data/'proxies').mkdir()
        (self.data/'proxies/provider').write_bytes(b'first contents')
        digest=self.verify()
        (self.data/'proxies/provider').write_bytes(b'updated contents')
        self.assertEqual(self.verify(),digest)
        (effective/'proxies').unlink()
        self.restart()
        self.assertEqual((effective/'proxies').readlink(),self.data/'proxies')
        (effective/'proxies').unlink()
        (effective/'proxies').symlink_to('/tmp/wrong-cache')
        with self.assertRaises(ValueError):
            self.restart()


    def test_import_binds_config_and_databases_and_restarts_use_snapshot(self):
        self.authorize()
        digest = self.verify()
        (self.gui / 'geosite.dat').write_bytes(b'unapproved replacement')
        self.restart()
        self.assertEqual(self.verify(), digest)
        self.assertEqual((self.runtime / 'effective/GeoSite.dat').read_bytes(), b'authorized fixture database')
        self.authorize()
        self.assertNotEqual(self.verify(), digest)
        self.assertEqual((self.runtime / 'effective/GeoSite.dat').read_bytes(), b'unapproved replacement')

    def test_changed_config_requires_another_operator_authorization(self):
        self.authorize()
        old = self.verify()
        self.config.write_text(self.config.read_text() + 'log-level: debug\n')
        with self.assertRaisesRegex(ValueError, 'configuration changed'):
            self.restart()
        self.assertEqual(self.verify(), old)
        self.authorize()
        self.assertNotEqual(self.verify(), old)

    def test_window_is_single_use_and_failed_validation_does_not_authorize(self):
        def reject(directory):
            raise ValueError('invalid database')
        with self.assertRaisesRegex(ValueError, 'invalid database'):
            self.authorize(reject)
        self.assertFalse((self.state / 'authorize-window').exists())
        self.assertFalse((self.state / 'authorized-config').exists())
        self.assertEqual(list((self.runtime / 'snapshots').iterdir()), [])
        with self.assertRaises((ValueError, OSError)):
            self.restart()

    def test_expired_and_future_windows_are_not_authorization(self):
        for age in (301, -60):
            window = self.state / 'authorize-window'
            window.touch()
            os.utime(window, (time.time() - age, time.time() - age))
            with self.assertRaises((ValueError, OSError)):
                self.restart()
            self.assertFalse(window.exists())

    def test_intent_is_checked_again_after_validation(self):
        def revoke(directory):
            (self.state / 'intent').write_text('direct')
        with self.assertRaisesRegex(ValueError, 'revoked'):
            self.authorize(revoke)
        self.assertFalse((self.state / 'authorized-config').exists())

    def test_private_modes_and_cache_is_the_only_writable_input(self):
        self.authorize()
        effective = self.runtime / 'effective'
        for path in (effective, self.runtime / 'snapshots'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o750)
        for path in (effective / 'clash-verge.yaml', effective / 'GeoSite.dat'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE((self.state / 'authorized-config').stat().st_mode), 0o600)
        digest = self.verify()
        (effective / 'cache.db').write_bytes(b'new runtime cache')
        self.assertEqual(self.verify(), digest)
        (effective / 'GeoSite.dat').write_bytes(b'changed snapshot')
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            self.verify()
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            self.restart()

    def test_absent_database_is_bound_and_missing_database_is_detected(self):
        (self.gui / 'geosite.dat').unlink()
        self.authorize()
        old = self.verify()
        (self.gui / 'geosite.dat').write_bytes(b'new database')
        self.restart()
        self.assertEqual(self.verify(), old)
        self.authorize()
        (self.runtime / 'effective/GeoSite.dat').unlink()
        with self.assertRaises(ValueError):
            self.verify()

    def test_symlink_and_nonregular_sources_are_rejected_without_hanging(self):
        for kind in ('symlink', 'fifo'):
            self.config.unlink()
            if kind == 'symlink':
                self.config.symlink_to('/etc/passwd')
            else:
                os.mkfifo(self.config)
            with self.assertRaises((ValueError, OSError)):
                self.authorize()

    def test_controller_field_is_required_and_cannot_be_duplicated(self):
        for text in ('mode: rule\n', 'external-controller-unix: /a\nexternal-controller-unix: /b\n'):
            self.config.write_text(text)
            with self.assertRaisesRegex(ValueError, 'one external-controller'):
                self.authorize()

    def test_outside_path_and_ambiguous_databases_are_rejected(self):
        with self.assertRaises(ValueError):
            stage.config_input(self.settings, str(self.root / 'config.yaml'))
        (self.gui / 'GeoSite.dat').write_bytes(b'ambiguous')
        with self.assertRaisesRegex(ValueError, 'ambiguous database'):
            self.authorize()

    def test_fast_snapshot_check_detects_changes_and_periodically_rehashes(self):
        self.authorize()
        effective = self.runtime / 'effective'
        auth, digest = self.state / 'authorized-config', self.state / 'config-digest'
        stage.verify(effective, digest, auth, fast=True)
        record = self.state / 'snapshot-verification.json'
        before = record.read_text()
        stage.verify(effective, digest, auth, fast=True, cached_only=True)
        stage.verify(effective, digest, auth, fast=True)
        self.assertEqual(record.read_text(), before)
        original = (effective / 'GeoSite.dat').read_bytes()
        (effective / 'GeoSite.dat').write_bytes(b'x' * len(original))
        with self.assertRaises(stage.FullVerificationRequired):
            stage.verify(effective, digest, auth, fast=True, cached_only=True)
        with self.assertRaises(ValueError): stage.verify(effective, digest, auth, fast=True)

    def test_hidden_boot_identity_requires_full_verification(self):
        self.authorize()
        effective = self.runtime / 'effective'
        auth, digest = self.state / 'authorized-config', self.state / 'config-digest'
        stage.verify(effective, digest, auth)
        record = self.state / 'snapshot-verification.json'
        import json
        before = json.loads(record.read_text())['full_verifications']
        read = Path.read_text
        def restricted(path, *args, **kwargs):
            if str(path) == '/proc/sys/kernel/random/boot_id':
                raise FileNotFoundError()
            return read(path, *args, **kwargs)
        with patch.object(stage, 'boot_id', side_effect=FileNotFoundError('identity unavailable')):
            stage.verify(effective, digest, auth, fast=True)
            stage.verify(effective, digest, auth, fast=True)
        self.assertEqual(json.loads(record.read_text())['full_verifications'], before + 2)

    def test_health_reuses_protected_metadata_without_spawning_a_verifier(self):
        self.authorize()
        effective = self.runtime / 'effective'
        auth, digest = self.state / 'authorized-config', self.state / 'config-digest'
        stage.verify(effective, digest, auth)
        spec = importlib.util.spec_from_file_location('health_probe', health_source)
        health = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(health)
        settings = dict(expected={}, effective_directory=str(effective), snapshot_required=True,
                        digest_file=str(digest), authorization_file=str(auth), snapshot_tool=str(source))
        with patch.object(health.subprocess, 'run', side_effect=AssertionError('unnecessary verifier process')):
            health.check_config({'mode':'rule'}, settings)
        effective.chmod(0o777)
        with self.assertRaises(ValueError):
            stage.verify(effective, digest, auth, fast=True)

    def record(self, name='profile-refresh.json'):
        return json.loads((self.state / name).read_text())

    def settle(self, ok=True, reason='node', restart=None):
        path = self.state / 'profile-refresh-verifying.json'
        pending = self.record(path.name)
        pending['monotonic_seconds'] -= 61
        path.write_text(json.dumps(pending))
        (self.state / 'state.json').write_text(json.dumps(dict(mode='proxy' if ok else 'blocked', reason=reason)))
        (self.state / 'health.json').write_text(json.dumps(dict(
            ok=ok, reason=reason, at=time.time(), boot_id='00000000-1111-2222-3333-444444444444', digest=self.verify())))
        restarts = []
        stage.verify_refresh(self.settings, restart=restart or (lambda: restarts.append(self.verify())))
        return restarts

    def auto_baseline(self):
        self.settings['refresh_policy'] = 'auto'
        self.authorize()
        self.settle()
        return self.verify()

    def test_auto_accepts_and_settles_without_a_window_retaining_one_previous(self):
        old = self.auto_baseline()
        for index in range(3):
            self.config.write_text(self.config.read_text() + f'# refresh {index}\n')
            self.restart()
            pending = self.record()
            self.assertEqual(pending['result'], 'verifying')
            self.assertEqual(pending['previous_digest'], old)
            self.assertEqual(len(list((self.runtime / 'snapshots').iterdir())), 2)
            self.assertEqual(self.settle(), [])
            self.assertEqual(self.record()['result'], 'accepted')
            self.assertEqual(self.record()['snapshot_at'], pending['snapshot_at'])
            self.assertFalse((self.state / 'profile-refresh-pin.json').exists())
            old = self.verify()
        for name in ('profile-refresh.json',):
            self.assertEqual(stat.S_IMODE((self.state / name).stat().st_mode), 0o644)

    def test_auto_bad_configuration_falls_back_and_pins_without_revalidation(self):
        old = self.auto_baseline()
        self.config.write_text(self.config.read_text() + '# bad configuration\n')
        calls = []
        def reject(directory):
            calls.append(directory)
            raise ValueError('https://secret.invalid/?token=private')
        stage.stage(self.settings, str(self.config), reject)
        record = self.record()
        self.assertEqual(record['result'], 'fallback')
        self.assertEqual(record['reason'], 'configuration invalid')
        self.assertEqual(self.verify(), old)
        stage.stage(self.settings, str(self.config), reject)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.record(), record)
        self.assertEqual(stat.S_IMODE((self.state / 'profile-refresh-pin.json').stat().st_mode), 0o644)
        self.assertNotIn('secret', json.dumps(record))
        pin = self.record('profile-refresh-pin.json')
        pin['at'] -= stage.PIN_TTL + 1
        (self.state / 'profile-refresh-pin.json').write_text(json.dumps(pin))
        stage.stage(self.settings, str(self.config), reject)
        self.assertEqual(len(calls), 2)
        self.config.write_text(self.config.read_text() + '# repaired\n')
        self.restart()
        self.assertNotEqual(self.verify(), old)

    def test_auto_input_errors_timeout_and_mutating_validator_are_sanitized(self):
        old = self.auto_baseline()
        for kind, expected in [('timeout', 'validation timeout'), ('mutate', 'snapshot mismatch'),
                               ('unreadable', 'input unreadable'), ('malformed', 'configuration invalid')]:
            stage.clear_refresh(self.state)
            self.config.write_text('external-controller-unix: /old.sock\n# ' + kind + '\n')
            def reject(directory):
                if kind == 'timeout':
                    raise subprocess.TimeoutExpired('secret command', 60)
                (directory / 'clash-verge.yaml').write_text('mutated')
            if kind == 'unreadable':
                self.config.unlink()
            if kind == 'malformed':
                self.config.write_bytes(b'\xff')
            stage.stage(self.settings, str(self.config), reject)
            self.assertEqual(self.verify(), old)
            self.assertEqual(self.record()['reason'], expected)
            self.assertEqual(self.record()['result'], 'fallback')
            self.assertFalse(any(p.name.startswith('.candidate-') for p in (self.runtime / 'snapshots').iterdir()))

    def test_auto_first_import_failure_is_refused_and_tampered_history_never_falls_back(self):
        self.settings['refresh_policy'] = 'auto'
        def reject(directory):
            raise ValueError('invalid')
        with self.assertRaises(ValueError):
            stage.stage(self.settings, str(self.config), reject)
        self.assertEqual(self.record()['result'], 'refused')
        self.auto_baseline()
        (self.runtime / 'effective/clash-verge.yaml').write_text('tampered')
        self.config.write_text(self.config.read_text() + '# changed\n')
        with self.assertRaisesRegex(ValueError, 'digest mismatch'):
            self.restart()

    def test_health_rollback_restores_authority_and_pin_survives_gui_restart(self):
        old = self.auto_baseline()
        self.config.write_text(self.config.read_text() + '# dead nodes\n')
        self.restart()
        rejected = self.verify()
        self.assertNotEqual(old, rejected)
        self.assertEqual(self.settle(ok=False), [old])
        record = self.record()
        self.assertEqual((record['result'], record['reason']), ('rolled_back', 'node'))
        self.assertEqual(self.record('profile-refresh-pin.json')['digest'], rejected)
        self.validated.clear()
        self.restart()
        self.assertEqual(self.verify(), old)
        self.assertEqual(self.validated, [])
        self.assertEqual(self.record(), record)
        (self.state / 'authorize-window').touch()
        self.restart()
        self.assertEqual(self.verify(), rejected)
        self.assertFalse((self.state / 'profile-refresh-pin.json').exists())

    def test_verifier_waits_and_cannot_settle_on_stale_or_foreign_health(self):
        self.auto_baseline()
        self.config.write_text(self.config.read_text() + '# new\n')
        self.restart()
        stage.verify_refresh(self.settings, restart=lambda: self.fail('too early'))
        self.assertEqual(self.record()['result'], 'verifying')
        self.settle(ok=False, reason='no-tun')
        self.assertEqual(self.record()['reason'], 'tun')

    def test_consecutive_refreshes_during_probation_keep_last_settled_snapshot(self):
        old = self.auto_baseline()
        for suffix in ('first', 'second'):
            self.config.write_text(self.config.read_text() + '# ' + suffix + '\n')
            self.restart()
        self.assertEqual(self.record()['previous_digest'], old)
        self.assertEqual(self.settle(ok=False, reason='dns'), [old])
        self.assertEqual(self.record()['reason'], 'dns')

    def test_first_import_chain_never_borrows_an_unsettled_rollback_target(self):
        self.settings['refresh_policy'] = 'auto'
        self.authorize()
        self.config.write_text(self.config.read_text() + '# another candidate\n')
        self.restart()
        self.assertFalse(self.record()['previous_digest'])
        self.assertEqual(self.settle(ok=False), [])
        self.assertEqual(self.record()['result'], 'refused')

    def test_audit_baseline_remains_settled_during_consecutive_imports(self):
        original = self.auto_baseline()
        for name in ('first', 'second'):
            self.config.write_text(self.config.read_text() + '# ' + name + '\n')
            with patch.object(stage, 'run_audit', return_value=None) as worker:
                self.restart()
            self.assertEqual(worker.call_args.args[2], self.runtime / 'snapshots' / original)

    def test_corrupt_settled_authority_never_relearns_from_current_candidate(self):
        self.auto_baseline()
        record = self.state / 'profile-refresh-settled.json'
        record.write_text('{broken')
        with self.assertRaisesRegex(ValueError, 'invalid settled authority'):
            self.restart()

    def test_interrupted_authority_replacements_are_replayed_before_start(self):
        self.auto_baseline()
        for filename in ('authorized-config', 'config-digest', 'profile-refresh-verifying.json',
                         'profile-refresh.json', 'effective-config.json'):
            self.config.write_text(self.config.read_text() + '# ' + filename + '\n')
            writer_name = 'atomic_authority' if filename in ('authorized-config', 'config-digest') else 'atomic_record'
            original = getattr(stage, writer_name)
            def interrupt(path, *args, **kwargs):
                original(path, *args, **kwargs)
                if path.name == filename:
                    raise OSError('simulated commit interruption')
            with self.subTest(filename=filename), patch.object(stage, writer_name, interrupt):
                with self.assertRaises(OSError):
                    self.restart()
            self.assertTrue((self.state / 'profile-refresh-commit.json').exists())
            self.restart()
            self.verify()
            self.assertFalse((self.state / 'profile-refresh-commit.json').exists())
            self.assertEqual(self.record()['result'], 'verifying')
            self.settle()

    def test_failed_restart_retains_recovery_work_for_the_next_timer(self):
        old = self.auto_baseline()
        self.config.write_text(self.config.read_text() + '# bad nodes\n')
        self.restart()
        def fail():
            raise OSError('simulated restart failure')
        with self.assertRaises(OSError):
            self.settle(ok=False, restart=fail)
        self.assertEqual(self.verify(), old)
        self.assertTrue((self.state / 'profile-refresh-restart.json').exists())
        called = []
        stage.verify_refresh(self.settings, restart=lambda: called.append(self.verify()))
        self.assertEqual(called, [old])
        self.assertFalse((self.state / 'profile-refresh-restart.json').exists())

    def test_auto_explicit_window_can_fall_back_to_valid_authority(self):
        old = self.auto_baseline()
        (self.state / 'authorize-window').touch()
        self.config.write_text(self.config.read_text() + '# invalid\n')
        def reject(directory):
            raise ValueError('invalid')
        stage.stage(self.settings, str(self.config), reject)
        self.assertEqual(self.verify(), old)
        self.assertEqual(self.record()['result'], 'fallback')


    def enable_audit(self):
        self.audit_settings = self.root / 'audit-settings.json'
        self.audit_settings.write_text('{}')
        self.audit_tool = self.root / 'audit.py'
        self.audit_tool.write_text("""import json, sys
text = open(sys.argv[2]).read()
def result(verdict, field=None, category=None):
    value = {"verdict": verdict, "deny": {}, "warn": {}, "unknown": {}}
    if field:
        value[field][category] = 1
    print(json.dumps(value))
if '# audit fail' in text:
    raise SystemExit(1)
if '# audit deny' in text:
    result('deny', 'deny', 'sensitive_route')
elif '# audit unknown' in text:
    result('unknown', 'unknown', 'route_unknown')
elif '# audit warn' in text:
    result('warn', 'warn', 'rule_volume')
else:
    result('pass')
""")
        self.settings.update(audit_policy='enforce', audit_python=sys.executable,
                             audit_tool=str(self.audit_tool),
                             audit_settings=str(self.audit_settings))
        self.settings.pop('setpriv', None)

    def test_audit_deny_falls_back_and_pins(self):
        old = self.auto_baseline()
        self.enable_audit()
        self.config.write_text(self.config.read_text() + '# audit deny\n')
        self.restart()
        record = self.record()
        self.assertEqual(record['result'], 'fallback')
        self.assertEqual(record['reason'], 'blocked by audit')
        self.assertEqual(record['audit']['verdict'], 'deny')
        self.assertEqual(self.verify(), old)
        self.assertTrue((self.state / 'profile-refresh-pin.json').exists())

    def test_audit_indeterminate_blocks_in_enforce(self):
        old = self.auto_baseline()
        self.enable_audit()
        for marker, reason in (('# audit unknown', 'audit indeterminate'),
                               ('# audit fail', 'audit indeterminate')):
            stage.clear_refresh(self.state)
            self.config.write_text(self.config.read_text() + '\n' + marker + '\n')
            self.restart()
            self.assertEqual(self.record()['result'], 'fallback')
            self.assertEqual(self.record()['reason'], reason)
            self.assertEqual(self.verify(), old)

    def test_audit_warn_imports_and_records_findings(self):
        self.auto_baseline()
        self.enable_audit()
        self.config.write_text(self.config.read_text() + '# audit warn\n')
        self.restart()
        self.assertEqual(self.record()['result'], 'verifying')
        self.assertEqual(self.record()['audit']['verdict'], 'warn')
        self.settle()
        self.assertEqual(self.record()['result'], 'accepted')
        self.assertEqual(self.record()['audit']['verdict'], 'warn')

    def test_slow_audit_shares_the_preparation_deadline_and_falls_back(self):
        old = self.auto_baseline()
        self.enable_audit()
        self.audit_tool.write_text('import time; time.sleep(10)\n')
        self.config.write_text(self.config.read_text() + '# slow audit\n')
        self.settings['_prepare_deadline'] = time.monotonic() + .1
        started = time.monotonic()
        self.restart()
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual((self.record()['result'], self.record()['reason']),
                         ('fallback', 'audit indeterminate'))
        self.assertEqual(self.verify(), old)

    def test_audit_deny_refuses_explicit_authorization(self):
        self.settings['refresh_policy'] = 'strict'
        self.enable_audit()
        self.config.write_text(self.config.read_text() + '# audit deny\n')
        with self.assertRaisesRegex(ValueError, 'blocked by audit'):
            self.authorize()
        self.assertEqual(self.record()['result'], 'refused')
        self.assertEqual(self.record()['reason'], 'blocked by audit')



if __name__ == '__main__':
    unittest.main(verbosity=2)
