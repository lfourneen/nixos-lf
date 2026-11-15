"""Reviewed-update binding and credential-free terminal output."""
import contextlib
import copy
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('review', sys.argv.pop(1))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
broker_path = sys.argv.pop(1) if len(sys.argv) > 1 else Path(r.__file__).with_name('source_broker.py')
spec = importlib.util.spec_from_file_location('broker', broker_path)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.source = Path(temp.name)
        self.signature = dict(source_id='one', candidate='1' * 64, baseline='2' * 64,
            generation='3' * 64, revision='4' * 64, policy='5' * 64,
            categories=['node_identity'], audit={'deny': {'node_identity': 1}, 'unknown': {}})
        self.report = {'can_apply': True}
        context = patch.object(r, 'context', side_effect=lambda *_: (
            copy.deepcopy(self.signature), self.report, None, None))
        context.start()
        self.addCleanup(context.stop)

    @staticmethod
    def private(path):
        return path.read_bytes()

    @staticmethod
    def atomic(path, data):
        path.write_bytes(data)
        path.chmod(0o600)

    def compare(self):
        return r.compare({}, self.source, self.private, self.atomic)

    def apply(self):
        return r.approve_reviewed({}, self.source, self.private, self.atomic)

    def test_comparison_creates_a_private_review_without_approval(self):
        self.compare()
        self.assertTrue((self.source / 'review.json').exists())
        self.assertEqual((self.source / 'review.json').stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.source / 'approval.json').exists())
        self.apply()
        approved = json.loads((self.source / 'approval.json').read_bytes())
        for key in ('candidate', 'baseline', 'revision', 'policy', 'categories'):
            self.assertEqual(approved[key], self.signature[key])

    def test_apply_requires_a_prior_comparison(self):
        with self.assertRaisesRegex(r.ReviewError, 'clash-diff'):
            self.apply()
        self.assertFalse((self.source / 'approval.json').exists())

    def test_changed_context_refuses_the_reviewed_update(self):
        original = copy.deepcopy(self.signature)
        for key in ('source_id', 'candidate', 'baseline', 'generation', 'revision', 'policy', 'categories', 'audit'):
            with self.subTest(field=key):
                self.signature = copy.deepcopy(original)
                self.compare()
                self.signature[key] = [] if isinstance(original[key], list) else {} if isinstance(original[key], dict) else 'changed'
                with self.assertRaisesRegex(r.ReviewError, 'changed or expired'):
                    self.apply()
                self.assertFalse((self.source / 'approval.json').exists())

    def test_expired_or_future_review_does_not_grant_approval(self):
        for expires in (time.time() - 1, time.time() + 7200):
            with self.subTest(expires=expires):
                self.compare()
                path = self.source / 'review.json'
                path.write_text(json.dumps(dict(self.signature, expires=expires)))
                with self.assertRaises(r.ReviewError):
                    self.apply()
                self.assertFalse((self.source / 'approval.json').exists())

    def test_blocked_candidate_clears_an_earlier_review(self):
        self.compare()
        self.report['can_apply'] = False
        self.compare()
        self.assertFalse((self.source / 'review.json').exists())
        with self.assertRaises(r.ReviewError):
            self.apply()

    def test_identity_comparison_hides_authentication_and_nested_options(self):
        before = {'name': '节点', 'type': 'vless', 'server': '1.1.1.1', 'lf-source-server': 'old.example',
                  'port': 443, 'uuid': 'old-secret', 'ws-opts': {'headers': {'Authorization': 'old-token'}}}
        after = dict(before, server='8.8.8.8', **{'lf-source-server': 'new.example', 'uuid': 'new-secret',
                     'ws-opts': {'headers': {'Authorization': 'new-token'}}})
        nodes, removed = r.node_changes({'proxies': [before]}, {'proxies': [after]})
        output = json.dumps(nodes)
        for secret in ('old-secret', 'new-secret', 'old-token', 'new-token'):
            self.assertNotIn(secret, output)
        self.assertIn('new.example', output)
        self.assertEqual(removed, [])
        after = dict(before, server='8.8.8.8')
        self.assertEqual(r.node_changes({'proxies': [before]}, {'proxies': [after]}), ([], []))

    def test_terminal_output_wraps_unicode_and_removes_control_characters(self):
        report = dict(source='one', baseline='1' * 64, candidate='2' * 64,
            before_nodes=1, after_nodes=1, audit={'deny': {'node_identity': 1}}, can_apply=True,
            blocked_categories=[], nodes=[{'name': '节点' * 40 + '\x1b[31m', 'added': False,
            'changes': [{'field': 'logical-server', 'hidden': False,
                         'before': 'old.example', 'after': 'new.example'}]}],
            removed_nodes=[], removed_groups=[], rules=[], groups=[])
        output = io.StringIO()
        with patch.object(r.shutil, 'get_terminal_size', return_value=os.terminal_size((50, 24))), contextlib.redirect_stdout(output):
            r.render(report)
        self.assertNotIn('\x1b', output.getvalue())
        self.assertIn('old.example', output.getvalue())
        self.assertIn('new.example', output.getvalue())
        self.assertTrue(all(r.width(line) <= 50 for line in output.getvalue().splitlines()))


class ApplyModesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'sources/one'
        self.source.mkdir(parents=True, mode=0o700)
        self.state = self.root / 'state'
        self.state.mkdir()
        (self.root / 'selected').symlink_to('sources/one')
        b.atomic(self.source / 'registry.json', b'{"id":"one"}')
        self.stage_settings = {'state': str(self.state), 'runtime': str(self.root / 'runtime')}
        settings_file = self.root / 'stage.json'
        settings_file.write_text(json.dumps(self.stage_settings))
        self.settings = {'stageSettings': str(settings_file), 'launcher': '/fixed/core', 'mode_script': '/fixed/mode'}
        self.signature = dict(source_id='one', candidate='1' * 64, baseline='2' * 64,
            generation='3' * 64, revision='4' * 64, policy='5' * 64,
            categories=['node_identity'], audit={'deny': {'node_identity': 1}, 'unknown': {}})
        self.stage = SimpleNamespace(restart_core=unittest.mock.Mock(side_effect=self.restart),
            snapshot_digest=lambda _: self.signature['candidate'],
            read_record=lambda path: json.loads(path.read_text()))
        self.locks_checked = 0
        self.requests = unittest.mock.Mock(side_effect=self.request_proxy)
        for target, replacement in (
            (r, ('context', lambda *_: (copy.deepcopy(self.signature), {'can_apply': True}, self.stage, self.stage_settings))),
            (b, ('read_private', lambda path, *_: path.read_bytes())),
            (b.subprocess, ('run', self.requests)),
        ):
            context = patch.object(target, *replacement)
            context.start()
            self.addCleanup(context.stop)
        r.compare(self.settings, self.source, b.read_private, b.atomic)

    def request_proxy(self, command, **kwargs):
        self.assertEqual(command, ['/fixed/mode', '--request-proxy'])
        (self.state / 'state.json').write_text('{"mode":"blocked"}')

    def restart(self, settings):
        self.assertEqual(json.loads((self.state / 'state.json').read_text())['mode'], 'blocked')
        # Helper staging must be able to take SH, while refresh/selection and
        # another on/off/apply cannot enter the same transaction.
        with (self.root / 'source.lock').open('a') as shared:
            fcntl.flock(shared, fcntl.LOCK_SH | fcntl.LOCK_NB)
        for path in (self.root / 'source.lock', self.state / 'operator.lock'):
            with path.open('a') as exclusive, self.assertRaises(BlockingIOError):
                fcntl.flock(exclusive, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with (self.state / 'authorization.lock').open('a') as authorization:
            fcntl.flock(authorization, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.locks_checked += 1
        (self.source / 'approval.json').unlink()  # The real stage consumes it.

    def test_modes_follow_the_latest_operator_intent(self):
        for mode, intent in (('direct', 'direct'), ('proxy', 'proxy'),
                             ('blocked', 'proxy'), ('blocked', 'direct')):
            with self.subTest(mode=mode, intent=intent):
                (self.state / 'state.json').write_text(json.dumps({'mode': mode}))
                (self.state / 'intent').write_text(intent)
                self.stage.restart_core.reset_mock()
                self.requests.reset_mock()
                r.compare(self.settings, self.source, b.read_private, b.atomic)
                result = b.apply_reviewed(self.settings, self.root, r)
                self.assertEqual(result['mode'], 'blocked' if intent == 'proxy' else mode)
                self.assertEqual(result['intent'], intent)
                self.assertEqual(result['result'], 'applied' if intent == 'proxy' else 'approved')
                self.assertEqual(self.stage.restart_core.call_count, int(intent == 'proxy'))
                self.assertEqual(self.requests.call_count, int(intent == 'proxy'))
                self.assertEqual((self.state / 'intent').read_text(), intent)
                self.assertFalse((self.source / 'review.json').exists())
                self.assertEqual((self.source / 'approval.json').exists(), intent == 'direct')
                with (self.state / 'operator.lock').open('a') as operator:
                    fcntl.flock(operator, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(self.locks_checked, 2)

    def test_failed_restart_removes_unused_approval(self):
        (self.state / 'intent').write_text('proxy')
        for error in (OSError('fixture restart failure'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                self.stage.restart_core.side_effect = error
                with self.assertRaises(type(error)):
                    b.apply_reviewed(self.settings, self.root, r)
                self.assertFalse((self.source / 'approval.json').exists())
                self.assertTrue((self.source / 'review.json').exists())


if __name__ == '__main__':
    unittest.main()
