"""Authorization/permission regressions using real files, without root or VM."""

import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest

source = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else (
    Path(__file__).resolve().parents[2] / 'system/programs/clash/clash-verge-stage.py')
spec = importlib.util.spec_from_file_location('stage', source)
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gui, self.runtime, self.state, self.data = [self.root / n for n in ('gui', 'run', 'state', 'data')]
        for path in (self.gui, self.runtime, self.state, self.data):
            path.mkdir()
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
