"""Read-only state views used by proxy-status and proxy-inspect."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

source = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else (
    Path(__file__).resolve().parents[3] / 'system/config/network/proxy/state.py')
spec = importlib.util.spec_from_file_location('network_state', source)
state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(state)

BOOT = '00000000-1111-2222-3333-444444444444'
NORMALIZED = 'external-controller-unix: /run/clash-verge-rev/core/mihomo.sock\nmode: rule\n'


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'proxy-mode').mkdir()
        (self.root / 'proxy-mode/boot-id').write_text(BOOT + '\n')
        self.profiles = self.root / 'profiles.yaml'
        self.profiles.write_text(
            'current: a\nitems:\n'
            '- {uid: a, type: remote, updated: 1000, option: {update_interval: 1440, allow_auto_update: true}}\n'
            '- {uid: b, type: remote, updated: 2000, option: {update_interval: 60}}\n')

    def write_refresh(self, **values):
        record = {'at': 1000, 'boot_id': BOOT, 'policy': 'auto', 'result': 'accepted',
                  'reason': 'unknown', 'digest': 'a' * 64, 'previous_digest': 'b' * 64,
                  'snapshot_at': 900, 'last_accepted_at': 1000, 'history': []}
        record.update(values)
        (self.root / 'proxy-mode/profile-refresh.json').write_text(json.dumps(record))

    def test_refresh_allowlist_rejects_unknown_values(self):
        self.write_refresh()
        value = state.profile_refresh(self.root, now=1100, boot=BOOT)
        self.assertEqual((value['result'], value['digest']), ('accepted', 'a' * 12))
        for field, bad in (('result', 'imported'), ('policy', 'loose'),
                           ('reason', 'https://secret.invalid'), ('digest', 'not-a-digest'),
                           ('boot_id', 'other-boot')):
            self.write_refresh(**{field: bad})
            self.assertIsNone(state.profile_refresh(self.root, now=1100, boot=BOOT), field)

    def test_history_is_sanitized_and_capped(self):
        self.write_refresh(history=[{'at': 1000, 'boot_id': BOOT, 'policy': 'auto',
                                     'result': 'accepted', 'reason': 'unknown',
                                     'digest': 'c' * 64, 'snapshot_at': 900}] * 30)
        value = state.profile_refresh(self.root, now=1100, boot=BOOT, history=True)
        self.assertEqual(len(value['history']), 20)
        self.assertNotIn('https://', json.dumps(value))

    def test_history_entries_without_policy_inherit_the_record_policy(self):
        entry = {'at': 1000, 'boot_id': BOOT, 'result': 'accepted',
                 'reason': 'unknown', 'digest': 'c' * 64, 'snapshot_at': 900}
        self.write_refresh(history=[entry, entry])
        value = state.profile_refresh(self.root, now=1100, boot=BOOT, history=True)
        self.assertEqual(len(value['history']), 2)
        self.assertEqual(value['history'][0]['result'], 'accepted')

    def test_auto_update_states(self):
        now = 5000
        self.assertEqual(state.auto_update(self.profiles, now=now, running=True)['state'], 'ok')
        overdue = state.auto_update(self.profiles, now=1000 + 86400 + 601, running=True)
        self.assertEqual(overdue['state'], 'overdue')
        closed = state.auto_update(self.profiles, now=now, running=False)
        self.assertEqual(closed['state'], 'gui-closed')
        self.profiles.write_text(
            'current: a\nitems:\n- {uid: a, type: remote, updated: 1000, '
            'option: {update_interval: 1440, allow_auto_update: false}}\n')
        self.assertEqual(state.auto_update(self.profiles, now=now, running=True)['state'],
                         'timer-off')
        self.profiles.write_text(
            'current: a\nitems:\n- {uid: a, type: remote, updated: 1000, option: {update_interval: 0}}\n')
        self.assertEqual(state.auto_update(self.profiles, now=now, running=True)['state'],
                         'timer-off')
        self.assertEqual(state.auto_update(self.profiles, now=now, running=True).get('reason'),
                         'no interval')

    def test_subscription_health_ignores_non_current_items(self):
        refresh = {'result': 'accepted', 'last_accepted_at': 1000}
        value = state.subscription_health(self.profiles, refresh, now=1000 + 4 * 86400)
        self.assertTrue(value['stale'], 'non-current item must not mask the current one')
        self.assertEqual(value['last_update'], 1000)
        value = state.subscription_health(self.profiles, refresh, now=1000 + 86400)
        self.assertFalse(value['stale'])
        failed = state.subscription_health(
            self.profiles, {'result': 'fallback', 'last_accepted_at': 1000}, now=1100)
        self.assertTrue(failed['stale'])

    def test_profile_vs_runtime_digest_reconciliation(self):
        (self.root / 'clash-verge.yaml').write_text(
            NORMALIZED.replace('/run/clash-verge-rev/core/mihomo.sock', '/old.sock'))
        digest = hashlib.sha256(NORMALIZED.encode()).hexdigest()
        (self.root / 'proxy-mode/effective-config.json').write_text(json.dumps(
            dict(boot_id=BOOT, yaml_digest=digest)))
        value = state.profile_vs_runtime(self.profiles, self.root)
        self.assertEqual((value['state'], value['runtime']), ('in sync', digest[:12]))
        (self.root / 'clash-verge.yaml').write_text(NORMALIZED + 'log-level: debug\n')
        self.assertEqual(state.profile_vs_runtime(self.profiles, self.root)['state'], 'differs')
        (self.root / 'clash-verge.yaml').write_text('mode: rule\n')
        self.assertEqual(state.profile_vs_runtime(self.profiles, self.root)['state'], 'unknown')

    def test_malformed_profiles_are_unknown_without_parser_output(self):
        self.profiles.write_text('url: "https://x.invalid/?token=TESTSECRET\n')
        self.assertEqual(state.auto_update(self.profiles)['state'], 'unknown')
        self.assertIsNone(state.subscription_health(self.profiles)['last_update'])

    def test_closed_gui_still_exposes_overdue_and_bad_dates_are_unknown(self):
        self.assertEqual(state.auto_update(self.profiles, now=1000 + 86400 + 601, running=False)['state'], 'overdue')
        self.profiles.write_text('current: a\nitems: [{uid: a, type: remote, updated: .inf, option: {update_interval: 1}}]\n')
        self.assertEqual(state.auto_update(self.profiles, running=True)['state'], 'unknown')

    def test_boot_identity_has_no_proc_fallback_and_rejects_symlinks(self):
        path = self.root / 'proxy-mode/boot-id'
        self.assertEqual(state.boot_identity(self.root), BOOT)
        path.unlink()
        self.assertEqual(state.boot_identity(self.root), '')
        path.symlink_to('/proc/sys/kernel/random/boot_id')
        self.assertEqual(state.boot_identity(self.root), '')

    def test_runtime_digest_requires_matching_boot_and_preserves_crlf(self):
        text = NORMALIZED.replace('\n', '\r\n')
        (self.root / 'clash-verge.yaml').write_bytes(text.encode())
        digest = hashlib.sha256(text.replace('mihomo.sock\r\n', 'mihomo.sock\n').encode()).hexdigest()
        record = dict(boot_id=BOOT, yaml_digest=digest)
        path = self.root / 'proxy-mode/effective-config.json'
        path.write_text(json.dumps(record))
        self.assertEqual(state.profile_vs_runtime(self.profiles, self.root)['state'], 'in sync')
        path.write_text(json.dumps(dict(record, boot_id='previous-boot')))
        self.assertEqual(state.profile_vs_runtime(self.profiles, self.root)['state'], 'unknown')


if __name__ == '__main__':
    unittest.main()
