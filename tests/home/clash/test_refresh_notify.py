"""Local failure notifications; no D-Bus or network access."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

source = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else (
    Path(__file__).resolve().parents[3] / 'home/config/clash/refresh_notify.py')
spec = importlib.util.spec_from_file_location('refresh_notify', source)
notify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notify)


class NotificationTests(unittest.TestCase):
    def test_failure_is_deduplicated_and_state_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            sent = []
            refresh = dict(result='fallback', reason='configuration invalid', digest='a' * 64)
            for _ in range(3):
                notify.notify(refresh, directory, sent.append)
            self.assertEqual(len(sent), 1)
            self.assertIn('Profile refresh: fallback', sent[0])
            self.assertEqual((Path(directory) / 'refresh-notified.json').stat().st_mode & 0o777, 0o600)

    def test_success_observation_restoration_and_audit_warnings_stay_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            sent = []
            automatic = dict(at=1000, nodes=45, categories={'node_identity': 45})
            refresh = dict(reason=None, digest='b' * 64,
                audit=dict(verdict='warn', deny={}, warn={'node_identity': 45, 'rule_volume': 1},
                           unknown={}, auto_approval=automatic))
            for result in ('verifying', 'accepted', 'restored', 'accepted'):
                notify.notify(dict(refresh, result=result), directory, sent.append,
                              fetch=dict(result='downloaded', at=1000), health={'ok': True})
            self.assertEqual(sent, [])

    def test_failed_send_does_not_advance_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            def fail(message):
                raise RuntimeError('D-Bus unavailable')
            refresh = dict(result='refused', reason='blocked by audit', digest='c' * 64)
            with self.assertRaises(RuntimeError):
                notify.notify(refresh, directory, fail)
            self.assertFalse((Path(directory) / 'refresh-notified.json').exists())
            sent = []
            notify.notify(refresh, directory, sent.append)
            self.assertEqual(len(sent), 1)

    def test_health_failures_are_private_and_recovery_stays_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            sent = []
            refresh = dict(result='rolled_back', reason='node', digest='a' * 64)
            health = {'ok': False, 'reason': 'https://private.invalid/token', 'node': 'private-node'}
            for _ in range(2):
                notify.notify(refresh, directory, sent.append, health=health)
            self.assertEqual(len(sent), 1)
            self.assertIn('rolled_back', sent[0])
            self.assertIn('Proxy health warning: obs', sent[0])
            self.assertNotIn('private', sent[0])
            notify.notify(None, directory, sent.append, health={'ok': True})
            self.assertEqual(len(sent), 1)
            notify.notify(None, directory, sent.append, health=health)
            self.assertEqual(len(sent), 2)

    def test_download_failure_is_deduplicated_without_claiming_update_success(self):
        with tempfile.TemporaryDirectory() as directory:
            sent = []
            fetch = dict(result='failed', at=1000, phase='fetch', reason='dns lookup failed')
            for _ in range(2):
                notify.notify(None, directory, sent.append, fetch=fetch)
            self.assertEqual(len(sent), 1)
            self.assertTrue(sent[0].startswith('Subscription download failed · dns lookup failed'))
            notify.notify(None, directory, sent.append, fetch=dict(result='downloaded', at=1010))
            self.assertEqual(len(sent), 1)
            notify.notify(None, directory, sent.append, fetch=dict(result='deferred', at=1020))
            self.assertEqual(len(sent), 1)


if __name__ == '__main__':
    unittest.main()
