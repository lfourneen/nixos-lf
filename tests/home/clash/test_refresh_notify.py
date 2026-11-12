"""Local notification transactions; no D-Bus or network access."""
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
    def test_one_failure_with_stale_and_drift_is_one_notification(self):
        with tempfile.TemporaryDirectory() as directory:
            sent = []
            refresh = dict(result='fallback', reason='configuration invalid', digest='a' * 64)
            args = (refresh, dict(stale=True, age_days=0), directory, sent.append)
            for _ in range(3):
                notify.notify(*args, now=1000000, diagnostics=dict(content='differs'))
            self.assertEqual(len(sent), 1)
            self.assertIn('Profile refresh: fallback', sent[0])
            self.assertIn('GUI profile differs', sent[0])
            self.assertIn('Subscription health: stale', sent[0])
            notify.notify(*args, now=1000000 + 86400, diagnostics=dict(content='differs'))
            self.assertEqual(len(sent), 2)
            self.assertEqual((Path(directory) / 'refresh-notified.json').stat().st_mode & 0o777, 0o600)

    def test_audit_warning_and_transitions_are_not_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            sent = []
            refresh = dict(result='accepted', reason=None, digest='b' * 64,
                           audit=dict(verdict='warn', deny={}, warn={'rule_volume': 1}, unknown={}))
            notify.notify(refresh, {}, directory, sent.append)
            notify.notify(refresh, {}, directory, sent.append)
            self.assertEqual(len(sent), 1)
            for state in ('ok', 'overdue', 'overdue', 'ok', 'overdue'):
                notify.notify(None, {}, directory, sent.append, diagnostics=dict(update=state))
            self.assertEqual(len(sent), 3)

    def test_failed_send_does_not_advance_deduplication(self):
        with tempfile.TemporaryDirectory() as directory:
            def fail(message):
                raise RuntimeError('D-Bus unavailable')
            refresh = dict(result='refused', reason='blocked by audit', digest='c' * 64)
            with self.assertRaises(RuntimeError):
                notify.notify(refresh, {}, directory, fail)
            self.assertFalse((Path(directory) / 'refresh-notified.json').exists())
            sent = []
            notify.notify(refresh, {}, directory, sent.append)
            self.assertEqual(len(sent), 1)


if __name__ == '__main__':
    unittest.main()
