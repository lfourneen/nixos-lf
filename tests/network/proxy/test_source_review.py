"""Read-only source diagnostics, credential hiding and retired-command refusal."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('review', sys.argv.pop(1))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
broker_path = Path(sys.argv.pop(1))


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'one'
        self.source.mkdir()
        self.state = self.root / 'state'
        self.state.mkdir()
        (self.state / 'state.json').write_text('{"mode":"proxy"}')
        (self.state / 'intent').write_text('proxy')
        stage_settings = self.root / 'stage.json'
        stage_settings.write_text(json.dumps(dict(state=str(self.state), snapshot_tool='stage')))
        self.settings = dict(stageSettings=str(stage_settings), worker='worker')
        self.before = {'proxies': [{'name': 'Fixture', 'type': 'vless', 'server': '1.1.1.1',
            'lf-source-server': 'old.example', 'port': 443, 'uuid': 'secret'}],
            'proxy-groups': [], 'rules': ['MATCH,Fixture']}
        self.candidate = self.source / 'candidate'
        self.candidate.mkdir()
        after = json.loads(json.dumps(self.before))
        after['proxies'][0]['lf-source-server'] = 'new.example'
        (self.candidate / 'clash-verge.yaml').write_text(json.dumps(after))
        baseline_id = hashlib.sha256(json.dumps(self.before).encode()).hexdigest()
        self.baseline = self.source / 'approved' / baseline_id
        self.baseline.mkdir(parents=True)
        (self.baseline / 'clash-verge.yaml').write_text(json.dumps(self.before))
        (self.baseline / 'source-manifest.json').write_text('{"source_id":"one"}')
        (self.source / 'approved/settled.json').write_text(json.dumps(dict(digest=baseline_id)))
        self.stage = SimpleNamespace(
            source_input=lambda _: ({}, self.candidate / 'clash-verge.yaml'),
            source_directory=lambda _: self.source,
            approved_root=lambda _, source: source / 'approved',
            snapshot_metadata=lambda _: None,
            snapshot_digest=lambda directory: hashlib.sha256((directory / 'clash-verge.yaml').read_bytes()).hexdigest(),
            read_record=lambda path: json.loads(path.read_text()))
        self.findings = dict(verdict='deny', deny={'node_identity': 1}, warn={}, unknown={})
        self.record = dict(digest=self.stage.snapshot_digest(self.candidate), audit=self.findings, result='fallback')
        (self.state / 'profile-refresh.json').write_text(json.dumps(self.record))
        modules = patch.object(r, 'module', side_effect=lambda name, _: (
            self.stage if name == 'review_stage' else SimpleNamespace(load=json.loads)))
        modules.start()
        self.addCleanup(modules.stop)

    def compare(self):
        return r.compare(self.settings, self.source, lambda path, *_: path.read_bytes())

    def test_comparison_leaves_snapshots_mode_and_old_records_untouched(self):
        (self.source / 'review.json').write_text('{"retired":"review"}')
        (self.source / 'approval.json').write_text('{"old":"approval"}')
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        report = self.compare()
        self.assertTrue(report['audit_current'])
        self.assertEqual(report['audit'], self.findings)
        self.assertNotIn('can_apply', report)
        self.assertEqual(report['nodes'][0]['changes'], [dict(field='logical-server', hidden=False,
            before='old.example', after='new.example')])
        self.assertEqual({str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)

    def test_diagnostics_do_not_attach_another_candidates_audit(self):
        self.record['digest'] = '0' * 64
        (self.state / 'profile-refresh.json').write_text(json.dumps(self.record))
        report = self.compare()
        self.assertFalse(report['audit_current'])
        self.assertEqual(report['audit'], {})

    def test_changed_baseline_and_source_are_refused(self):
        original = (self.baseline / 'clash-verge.yaml').read_bytes()
        (self.baseline / 'clash-verge.yaml').write_text('{}')
        with self.assertRaisesRegex(r.ReviewError, 'baseline has changed'):
            self.compare()
        (self.baseline / 'clash-verge.yaml').write_bytes(original)
        self.stage.source_directory = lambda _: self.root / 'other'
        with self.assertRaisesRegex(r.ReviewError, 'Source changed'):
            self.compare()

    def test_retired_approval_commands_are_rejected_before_reading_private_state(self):
        for command in ('approve', 'apply-reviewed'):
            result = subprocess.run([sys.executable, str(broker_path), '/nonexistent/settings', command],
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2)
            self.assertIn('invalid choice', result.stderr)

    def test_cli_only_dispatches_read_only_comparison(self):
        self.settings.update(python=sys.executable, broker=str(broker_path))
        settings = self.root / 'settings.json'
        settings.write_text(json.dumps(self.settings))
        output = io.StringIO()
        with patch.object(sys, 'argv', ['clash-diff', str(settings), '--json']), contextlib.redirect_stdout(output), \
                patch.object(r.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='{"mode":"proxy"}')) as run:
            r.main()
        self.assertEqual(run.call_args.args[0][-2:], [str(settings), 'diff'])
        self.assertEqual(json.loads(output.getvalue()), {'mode': 'proxy'})

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
            before_nodes=1, after_nodes=1, audit={'deny': {'node_identity': 1}}, audit_current=True, nodes=[{'name': '节点' * 40 + '\x1b[31m', 'added': False,
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


if __name__ == '__main__':
    unittest.main()
