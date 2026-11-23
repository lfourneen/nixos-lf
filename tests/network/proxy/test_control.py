"""Authorization/permission regressions using real files, without root or VM."""

import errno
import importlib.util
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

source = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else (
    Path(__file__).resolve().parents[3] / 'system/programs/clash/service/clash-verge-stage.py')
health_source = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else Path(__file__).resolve().parents[3] / 'system/config/network/proxy/health.py'
preflight_source = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else source.with_name('preflight.py')
spec = importlib.util.spec_from_file_location('stage', source)
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)
spec = importlib.util.spec_from_file_location('health_probe', health_source)
health_probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health_probe)
spec = importlib.util.spec_from_file_location('preflight', preflight_source)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class ControlledPreflightTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / 'source'
        self.settings = {'source_root': str(self.root), 'snapshot_tool': str(source)}

    def test_non_root_receives_sudo_instruction_before_accessing_private_state(self):
        with patch.object(preflight.os, 'geteuid', return_value=1000):
            result = preflight.inspect({'source_root': str(self.root)})
        self.assertFalse(result['ready'])
        self.assertEqual(result['phase'], 'prepare-source-access')
        self.assertIn('sudo proxy-control preflight', result['next_action'])
        self.assertNotIn('Controlled source has not been registered', result['issues'])

    def test_root_can_distinguish_missing_directory_permissions_and_registration(self):
        with patch.object(preflight.os, 'geteuid', return_value=0):
            missing = preflight.inspect(self.settings)
            self.root.mkdir(mode=0o700)
            with patch.object(Path, 'open', side_effect=PermissionError(errno.EACCES, 'denied')):
                unreadable = preflight.inspect(self.settings)
            unregistered = preflight.inspect(self.settings)
        for result in (missing, unreadable, unregistered):
            self.assertFalse(result['ready'])
        self.assertEqual(missing['issues'], ['Controlled source state directory is missing'])
        self.assertEqual(unreadable['issues'], ['Controlled source state is not readable'])
        self.assertEqual(unregistered['issues'], ['Controlled source has not been registered'])

    def test_registered_source_uses_existing_root_checks(self):
        self.root.mkdir(mode=0o700)
        (self.root / 'registry.json').write_text('{}')
        expected = {'ready': True, 'phase': 'prepared'}
        with patch.object(preflight.os, 'geteuid', return_value=0), \
                patch.object(preflight, 'inspect_unlocked', return_value=expected) as inspect:
            self.assertEqual(preflight.inspect(self.settings), expected)
        inspect.assert_called_once_with(self.settings)

    def test_revocation_can_leave_unselected_registrations(self):
        remaining = self.root / 'sources' / 'other'
        remaining.mkdir(parents=True)
        (remaining / 'registry.json').write_text('{"id":"other"}')
        (self.root / 'registry-migrated').touch()
        with patch.object(preflight.os, 'geteuid', return_value=0):
            self.assertEqual(preflight.inspect(self.settings)['issues'], ['No registered source is selected'])
            (self.root / 'selected').symlink_to('sources/gone')
            self.assertEqual(preflight.inspect(self.settings)['issues'], ['Controlled source selection is invalid'])


class SnapshotTests(unittest.TestCase):
    def test_snapshot_publication_holds_policy_lock_through_both_authority_writes(self):
        write_authority = stage.atomic_authority

        def observe(path, digest):
            if path.parent == self.state:
                with (self.state / 'lock').open('a') as observer:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(observer, fcntl.LOCK_SH | fcntl.LOCK_NB)
            write_authority(path, digest)

        with patch.object(stage, 'atomic_authority', side_effect=observe) as publish:
            self.authorize()
        self.assertEqual([call.args[0].name for call in publish.call_args_list],
                         ['authorized-config', 'config-digest'])
        with (self.state / 'lock').open('a') as observer:
            fcntl.flock(observer, fcntl.LOCK_SH | fcntl.LOCK_NB)

    def test_busy_source_fetch_does_not_block_the_verifier_or_operator(self):
        with (self.root / 'source.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with stage.source_lock({'source_root': str(self.root)}, blocking=False) as acquired:
                self.assertFalse(acquired)
            stage.verify_refresh(dict(self.settings, source_root=str(self.root)),
                                 restart=lambda: self.fail('busy fetch must defer verification'))
            with (self.state / 'operator.lock').open('a') as operator:
                fcntl.flock(operator, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with stage.source_lock({'source_root': str(self.root)}, blocking=False) as acquired:
            self.assertTrue(acquired)

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

    def test_system_controls_reuse_only_the_accepted_snapshot(self):
        yq = os.environ.get('LF_TEST_YQ')
        if not yq:
            self.skipTest('yq is not supplied by the test harness')
        self.settings['yq'] = yq
        accepted = self.runtime / 'effective'
        accepted.mkdir()
        (accepted / 'clash-verge.yaml').write_text(
            'external-controller-unix: /old.sock\nmode: rule\ntun:\n  enable: false\n'
            'rules: [MATCH,Proxy]\n')
        control_only = (
            'external-controller-unix: /old.sock\nmode: global\ntun:\n  enable: true\n'
            'rules: [MATCH,Proxy]\n').encode()
        self.assertEqual(stage.system_control_input(self.settings, control_only, accepted),
                         (accepted / 'clash-verge.yaml').read_bytes())
        changed_rule = (
            'external-controller-unix: /old.sock\nmode: global\ntun:\n  enable: true\n'
            'rules: [MATCH,DIRECT]\n').encode()
        self.assertEqual(stage.system_control_input(self.settings, changed_rule, accepted),
                         changed_rule)
        non_mapping = b'[\nexternal-controller-unix: /old.sock\n]\n'
        self.assertEqual(stage.system_control_input(self.settings, non_mapping, accepted), non_mapping)

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

    def test_early_rollback_requires_repeated_fresh_full_health_for_this_candidate(self):
        old = self.auto_baseline()
        self.config.write_text(self.config.read_text() + '# failed candidate\n')
        self.restart()
        pending_path = self.state / 'profile-refresh-verifying.json'
        pending = self.record(pending_path.name)
        pending['monotonic_seconds'] -= 21
        pending_path.write_text(json.dumps(pending))
        (self.state / 'state.json').write_text(json.dumps(dict(mode='blocked', reason='obs', policy_version=7)))
        health = dict(ok=False, reason='obs', at=time.time(), boot_id=pending['boot_id'],
            digest=pending['digest'], policy_version=7, failure_streak=2,
            scope={'requests': ['mixed-tcp', 'fake-ip-tcp']})
        self.settings['require_tun'] = True
        for wrong in (dict(ok=True), dict(boot_id='other'), dict(digest=old), dict(failure_streak=1),
                      dict(policy_version=6), dict(at=time.time() - 30), dict(scope={'requests': []})):
            (self.state / 'health.json').write_text(json.dumps(dict(health, **wrong)))
            stage.verify_refresh(self.settings, restart=lambda: self.fail('insufficient proof'))
            self.assertEqual(self.record()['result'], 'verifying')
        (self.state / 'health.json').write_text(json.dumps(health))
        restarted = []
        stage.verify_refresh(self.settings, restart=lambda: restarted.append(self.verify()))
        self.assertEqual(restarted, [old])
        self.assertEqual((self.record()['result'], self.record()['reason']), ('rolled_back', 'obs'))
        self.assertTrue(self.record('profile-refresh-pin.json')['health'])

    def test_startup_import_keeps_full_window_despite_repeated_cold_health_failures(self):
        self.auto_baseline()
        self.config.write_text(self.config.read_text() + '# startup candidate\n')
        window = self.state / 'authorize-window'
        window.touch()
        stamp = time.time() - 1
        os.utime(window, (stamp, stamp))
        self.restart()
        path = self.state / 'profile-refresh-verifying.json'
        pending = self.record(path.name)
        self.assertTrue(pending['startup'])
        pending['monotonic_seconds'] -= 25
        path.write_text(json.dumps(pending))
        (self.state / 'state.json').write_text(json.dumps(dict(mode='blocked', reason='obs', policy_version=7)))
        (self.state / 'health.json').write_text(json.dumps(dict(ok=False, reason='obs', at=time.time(),
            boot_id=pending['boot_id'], digest=pending['digest'], policy_version=7, failure_streak=4,
            scope={'requests': ['mixed-tcp', 'fake-ip-tcp']})))
        self.settings['require_tun'] = True
        stage.verify_refresh(self.settings, restart=lambda: self.fail('cold startup rolled back early'))
        self.assertEqual(self.record()['result'], 'verifying')
        self.settle(ok=True)
        self.assertEqual(self.record()['result'], 'accepted')

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

    def test_source_restart_restores_settled_authority_over_an_effective_link(self):
        old = self.auto_baseline()
        baseline = (self.runtime / 'effective').resolve()
        self.config.write_text(self.config.read_text() + '# unobserved candidate\n')
        self.restart()
        self.assertNotEqual(self.verify(), old)
        # Off revokes both authority records and leaves the effective link.
        for name in ('authorized-config', 'config-digest'):
            (self.state / name).unlink()
        self.settings['source_root'] = str(self.root)
        with patch.object(stage, 'source_baseline', return_value=(baseline, old, time.time())) as restore, \
                patch.object(stage, 'restart_eligible', return_value=True):
            stage.restore_persistent(self.settings, self.runtime, self.state, os.getgid(), lambda _: None)
        restore.assert_called_once()
        self.assertEqual(self.verify(), old)
        self.assertEqual((self.runtime / 'effective').resolve(), baseline)

    def test_partial_source_authority_is_not_silently_restored(self):
        self.auto_baseline()
        self.settings['source_root'] = str(self.root)
        for missing in ('authorized-config', 'config-digest'):
            original = (self.state / missing).read_bytes()
            (self.state / missing).unlink()
            with patch.object(stage, 'source_baseline', side_effect=AssertionError('partial authority restored')), \
                    self.assertRaises((ValueError, OSError)):
                stage.stage(self.settings, str(self.config), lambda _: None)
            (self.state / missing).write_bytes(original)
            (self.state / missing).chmod(0o600)


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



class HealthLeafTests(unittest.TestCase):
    url = 'https://probe.example/204'
    host = 'probe.example'

    def probe(self, delays=None, **values):
        settings = dict(budget_seconds=30, urls=[self.url], dns_port=5353, dns_leaf_group=True)
        probe = health_probe.Probe(settings | values, clock=lambda: 0)
        def api(path, *_):
            if path == '/proxies/lf-remote':
                return {'type': 'Selector', 'all': ['a', 'b', 'c', 'd', 'e'], 'now': 'b'}
            if path == '/proxies/lf-dns':
                return {'type': 'Selector', 'all': ['b', 'a', 'c'], 'now': 'b'}
            if isinstance(delays, Exception):
                raise delays
            return delays
        probe.api = Mock(side_effect=api)
        return probe

    def test_candidates_rank_positive_delays_and_keep_unmeasured_authorized_members(self):
        probe = self.probe({'b': 1, 'foreign': 2, 'c': 30, 'd': 0, 'e': 20})
        self.assertEqual(probe.leaf_candidates(self.url), ['e', 'c', 'd', 'a'])
        path, maximum = probe.api.call_args.args
        self.assertIn('/group/lf-remote/delay', path)
        self.assertIn('timeout=1200', path)
        self.assertEqual(maximum, 1.8)

    def test_empty_and_unavailable_group_delay_use_authorized_order_after_current_leaf(self):
        for delays in ({}, [], health_probe.ProbeFailure('obs', 'controller reply unavailable or invalid')):
            with self.subTest(delays=delays):
                probe = self.probe(delays)
                self.assertEqual(probe.leaf_candidates(self.url), ['c', 'd', 'e', 'a'])

    def test_rotation_requires_real_mixed_request_and_continues_to_unmeasured_members(self):
        probe = self.probe({'e': 20})
        mixed_failure = health_probe.ProbeFailure('obs', 'mixed HTTP request failed')
        probe.target = Mock(side_effect=[mixed_failure, mixed_failure, None])
        probe.select_leaf = Mock(return_value=True)
        self.assertTrue(probe.rotate_leaf(None, self.url))
        self.assertEqual([call.args[0] for call in probe.select_leaf.call_args_list], ['e', 'c', 'd'])
        self.assertEqual([call.args for call in probe.target.call_args_list], [(self.url, None)] * 3)

    def test_fixed_egress_never_tries_a_different_leaf(self):
        probe = self.probe({})
        probe.routing = {'routing_requested': 'proxy-only', 'egress_requested': 'a' * 64}
        probe.select_leaf = Mock()
        self.assertFalse(probe.rotate_leaf(None, self.url))
        probe.select_leaf.assert_not_called()
        probe.api.assert_not_called()

    def test_node_failure_reports_when_all_authorized_leaves_fail_mixed_request(self):
        probe = self.probe({})
        mixed_failure = health_probe.ProbeFailure('obs', 'mixed HTTP request failed')
        probe.attempt_targets = Mock(side_effect=mixed_failure)
        probe.target = Mock(side_effect=mixed_failure)
        probe.select_leaf = Mock(return_value=True)
        probe.dns_answer = Mock(return_value=True)
        probe.application_path = Mock()
        with self.assertRaises(health_probe.ProbeFailure) as failure:
            probe.data_paths(None)
        self.assertEqual(failure.exception.layer, 'node')
        self.assertEqual(health_probe.failure_stage(failure.exception.detail), 'leaf-unavailable')
        self.assertEqual([call.args[0] for call in probe.select_leaf.call_args_list], ['c', 'd', 'e', 'a'])
        probe.application_path.assert_called_once_with()

    def test_dns_failure_rotates_only_the_managed_dns_selector_and_retries_the_current_application_leaf(self):
        probe = self.probe({})
        probe.attempt_targets = Mock(side_effect=health_probe.ProbeFailure('obs', 'mixed HTTP request failed'))
        probe.target = Mock()
        probe.select_leaf = Mock(return_value=True)
        probe.dns_answer = Mock(side_effect=[False, True])
        probe.application_path = Mock()
        probe.application_dns = Mock()
        probe.tun_test = Mock()
        probe.data_paths(None)
        probe.select_leaf.assert_called_once_with('a', 'lf-dns')
        probe.target.assert_called_once_with(self.url, None, maximum=2.5)
        self.assertTrue(probe.diagnostics['dns_leaf_rotated'])
        self.assertTrue(probe.diagnostics['managed_dns_ok'])

    def test_managed_dns_failure_is_distinct_from_node_failure(self):
        probe = self.probe({})
        probe.attempt_targets = Mock(side_effect=health_probe.ProbeFailure('obs', 'mixed HTTP request failed'))
        probe.select_leaf = Mock(return_value=True)
        probe.dns_answer = Mock(return_value=False)
        probe.rotate_leaf = Mock(return_value=False)
        with self.assertRaises(health_probe.ProbeFailure) as failure:
            probe.data_paths(None)
        self.assertEqual(failure.exception.layer, 'dns')
        self.assertEqual(health_probe.failure_stage(failure.exception.detail), 'validator-dns')
        self.assertFalse(probe.diagnostics['managed_dns_ok'])
        probe.rotate_leaf.assert_called_once_with(None, self.url, self.host)

    def test_dns_failure_promotes_a_reachable_remote_leaf_and_retries_both_paths(self):
        probe = self.probe({'e': 15})
        probe.selector = Mock(side_effect=[('b', ['a', 'c', 'e']), ('e', ['b', 'a', 'c']), ('b', ['e'])])
        probe.attempt_targets = Mock(side_effect=health_probe.ProbeFailure('obs', 'mixed HTTP request failed'))
        probe.target = Mock()
        probe.select_leaf = Mock(return_value=True)
        probe.dns_answer = Mock(side_effect=[False, False, False, False, True])
        probe.application_path = Mock()
        probe.application_dns = Mock()
        probe.tun_test = Mock()
        probe.data_paths(None)
        self.assertEqual([call.args for call in probe.select_leaf.call_args_list],
                         [('a', 'lf-dns'), ('c', 'lf-dns'), ('e', 'lf-dns'), ('e',)])
        self.assertTrue(probe.diagnostics['managed_dns_ok'])
        probe.target.assert_called_once()
        probe.tun_test.assert_called_once()

    def test_working_dns_leaf_precedes_repeated_application_members(self):
        probe = self.probe(None, session_urls=['https://203.0.113.53/dns-query'])
        banners = [f'bad{index}' for index in range(29)]
        probe.selector = Mock(side_effect=[('good', banners), ('bad0', [*banners[1:], 'good'])])
        probe.dns_answer = Mock(return_value=True)
        probe.recover_dns(self.host)
        self.assertEqual(probe.leaf_candidates(self.url)[0], 'good')
        probe.api.assert_not_called()

    def test_explicit_authentication_evidence_does_not_report_validator_dns(self):
        probe = self.probe({}, session_urls=['https://203.0.113.53/dns-query'])
        probe.attempt_targets = Mock(side_effect=health_probe.ProbeFailure('obs', 'mixed HTTP request failed'))
        original_api = probe.api.side_effect
        probe.api.side_effect = lambda path, *_: {'session': 'node-auth'} if '/session?' in path else original_api(path)
        probe.select_leaf = Mock(return_value=True)
        probe.target = Mock()
        probe.dns_answer = Mock(return_value=False)
        with self.assertRaises(health_probe.ProbeFailure) as failure:
            probe.data_paths(None)
        self.assertEqual(health_probe.failure_stage(failure.exception.detail), 'node-auth')
        self.assertEqual(probe.diagnostics['dns_leaf_session'], 'node-auth')
        self.assertEqual(probe.diagnostics['leaf_sessions']['node-auth'], 3)

    def test_validator_probe_requires_a_real_address(self):
        probe = self.probe()
        probe.api = Mock(return_value={'Status': 0, 'Answer': [{'type': 1, 'data': '198.18.0.42'}]})
        self.assertFalse(probe.dns_answer(self.host))
        probe.api.return_value['Answer'].append({'type': 1, 'data': '203.0.113.10'})
        self.assertTrue(probe.dns_answer(self.host))

    def test_dns_recovery_preserves_a_working_session_during_validator_warmup(self):
        probe = self.probe()
        probe.selector = Mock(side_effect=[('bad', ['also-bad', 'good', 'unchecked']),
                                           ('good', ['bad', 'also-bad', 'unchecked']), ('bad', ['good', 'also-bad'])])
        probe.sessions = {'bad': 'session-unavailable', 'also-bad': 'session-unavailable', 'good': 'ok'}
        probe.select_leaf = Mock(return_value=True)
        probe.dns_answer = Mock(return_value=False)
        with self.assertRaises(health_probe.ProbeFailure):
            probe.recover_dns(self.host)
        self.assertFalse(probe.rotate_leaf(None, self.url, self.host))
        self.assertEqual([call.args for call in probe.select_leaf.call_args_list], [('good', 'lf-dns'), ('good',)])

    def test_application_lookup_runs_through_system_stub_in_b_and_reports_failure_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            argv = ['systemctl', 'start', 'proxy-application-dns-probe.service']
            probe = self.probe(runtime=directory, application_dns_check=argv)
            status = Path(directory) / 'status'
            with patch.object(health_probe.subprocess, 'run', return_value=Mock(returncode=1)) as run:
                status.write_text('blocked')
                probe.application_path()
                run.assert_not_called()
                status.write_text('proxy')
                with self.assertRaises(health_probe.ProbeFailure) as failure:
                    probe.application_path()
        self.assertEqual(failure.exception.layer, 'application')
        self.assertEqual(health_probe.failure_stage(failure.exception.detail), 'application-dns')
        run.assert_called_once_with(argv, capture_output=True, timeout=3.5, check=False)

    def test_healthy_round_avoids_recovery_queries(self):
        probe = self.probe()
        probe.attempt_targets = Mock()
        probe.application_dns = Mock()
        probe.tun_test = Mock()
        probe.real_dns = Mock()
        probe.dns_answer = Mock()
        probe.data_paths(None)
        probe.dns_answer.assert_not_called()
        probe.api.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
