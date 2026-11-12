"""Upgrade review gates: same-version changes and incomplete plans fail closed."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

script, manifest = map(Path, sys.argv[1:3])
sys.argv = sys.argv[:1]
spec = importlib.util.spec_from_file_location('upgrades', script)
upgrades = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrades)
review = json.loads(manifest.read_text())


class UpgradeTests(unittest.TestCase):
    def test_reviewed_inputs_need_no_new_plan(self):
        self.assertEqual(upgrades.plan(review, review['sources'])['checks'], [])

    def test_same_version_source_dependency_or_recipe_change_triggers_all_vendored_components(self):
        for field in ('source', 'dependencies', 'recipe'):
            selected = copy.deepcopy(review['sources'])
            selected['mihomo'][field] = '0' * 64
            result = upgrades.plan(review, selected)
            self.assertEqual(result['changed'], ['mihomo', 'sing-tun', 'socks'])
            self.assertIn('proxy-uniform-vm', result['checks'])
            self.assertIn('proxy-network-vm', result['checks'])
            self.assertIn('browser-ech-uniform-vm', result['checks'])

    def test_browser_confinement_dependency_requires_actual_browser_tests(self):
        selected = copy.deepcopy(review['sources'])
        selected['dbusProxy']['version'] += '.changed'
        self.assertEqual(upgrades.plan(review, selected)['changed'], ['firefox'])
        self.assertIn('browser-sandbox-vm', upgrades.plan(review, selected)['checks'])

    def test_required_regression_or_input_cannot_be_removed(self):
        for name in review['components']:
            for field in ('sources', 'checks', 'entrypoints', 'invariants'):
                candidate = copy.deepcopy(review)
                candidate['components'][name][field] = []
                with self.subTest(name=name, field=field), self.assertRaises(ValueError):
                    upgrades.plan(candidate, review['sources'])

    def test_inventory_addition_or_removal_cannot_be_ignored(self):
        for mutation in ('add', 'remove'):
            selected = copy.deepcopy(review['sources'])
            if mutation == 'add':
                selected['new'] = selected['gost']
            else:
                del selected['gost']
            with self.assertRaises(ValueError):
                upgrades.plan(review, selected)

    def test_unknown_component_cannot_yield_an_empty_success(self):
        with self.assertRaises(ValueError):
            upgrades.plan(review, review['sources'], ['typo'])

    def test_check_command_rejects_drift_without_rewriting_baseline(self):
        before = manifest.read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            selected = copy.deepcopy(review['sources'])
            selected['gost']['version'] += '.changed'
            path = Path(directory) / 'selected.json'
            path.write_text(json.dumps(selected))
            result = subprocess.run([sys.executable, str(script), 'check', str(manifest), str(path)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('proxy-gost', result.stderr)
            self.assertEqual(manifest.read_bytes(), before)


unittest.main(verbosity=2)
