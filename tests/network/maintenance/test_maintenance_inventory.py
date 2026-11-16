"""Offline tests for scripts/check-maintenance.py and maintenance/inventory.json."""
import copy
import importlib.util
from unittest.mock import patch
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
CHECKER = ROOT / 'scripts/check-maintenance.py'
REAL_INVENTORY = ROOT / 'maintenance/inventory.json'
REAL_SOURCES = ROOT / 'system/config/network/maintenance/sources.json'
FIXTURE_REV = 'a' * 40

FIXTURE_INVENTORY = {
    'schema': 1,
    'network_sources': 'system/config/network/maintenance/sources.json',
    'items': [
        {
            'id': 'flake-example',
            'owner': 'lfour',
            'kind': 'nix-flake-pin',
            'upstream': 'example flake input',
            'update_mode': 'update the input and review the lock diff',
            'current': {'summary': 'locked fixture revision'},
            'signature': 'fixture revision',
            'fallback': 'restore the previous lock file',
            'max_patch_delay_days': 14,
            'review_command': 'nix flake metadata example',
            'pins': [],
            'flake': {'input': 'nixpkgs', 'rev': FIXTURE_REV},
        },
        {
            'id': 'extension-example',
            'owner': 'lfour',
            'kind': 'extension-auto-update',
            'upstream': 'example extension',
            'update_mode': 'policy install from the extension store',
            'current': {'summary': 'fixture extension mode'},
            'signature': 'store signature',
            'fallback': 'disable the extension',
            'max_patch_delay_days': 7,
            'review_command': 'inspect the extension pin',
            'pins': [{'file': 'config/example.nix', 'pattern': 'example-version = "1.0";'}],
        },
        {
            'id': 'network-alpha',
            'owner': 'lfour',
            'kind': 'nix-flake-pin',
            'upstream': 'example network component',
            'update_mode': 'review sources.json and update its baseline',
            'current': {'summary': 'version data stays in sources.json'},
            'signature': 'source fingerprint in sources.json',
            'fallback': 'revert the package update',
            'max_patch_delay_days': 7,
            'review_command': 'run upgrades.py check',
            'pins': [],
            'network_ref': 'alpha',
        },
    ],
}

FIXTURE_SOURCES = {
    'schema': 1,
    'sources': {'alpha': {'version': '1.0', 'source': 'sha256-fixture', 'dependencies': None, 'recipe': '0' * 64}},
    'components': {'alpha': {'sources': ['alpha'], 'checks': ['alpha-check'],
                            'entrypoints': ['src/alpha'], 'invariants': ['fixture invariant']}},
}

FIXTURE_LOCK = {
    'nodes': {
        'root': {'inputs': {'nixpkgs': 'nixpkgs'}},
        'nixpkgs': {'locked': {'rev': FIXTURE_REV, 'type': 'github'}},
    },
    'root': 'root',
    'version': 7,
}


class MaintenanceInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.write_repo()

    def write_repo(self):
        (self.root / 'scripts').mkdir(parents=True)
        (self.root / 'maintenance').mkdir()
        (self.root / 'system/config/network/maintenance').mkdir(parents=True)
        shutil.copy2(CHECKER, self.root / 'scripts/check-maintenance.py')
        self.write_json('maintenance/inventory.json', FIXTURE_INVENTORY)
        self.write_json('system/config/network/maintenance/sources.json', FIXTURE_SOURCES)
        self.write_json('flake.lock', FIXTURE_LOCK)
        self.write_text('config/example.nix', '{ example-version = "1.0"; }\n')

    def write_json(self, relative, data):
        self.write_text(relative, json.dumps(data, indent=2) + '\n')

    def write_text(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def inventory(self):
        return json.loads((self.root / 'maintenance/inventory.json').read_text())

    def save_inventory(self, inventory):
        self.write_json('maintenance/inventory.json', inventory)

    def run_checker(self, *arguments):
        result = subprocess.run(
            [sys.executable, str(self.root / 'scripts/check-maintenance.py'), '--json', *arguments],
            capture_output=True, text=True, timeout=30,
        )
        return result, json.loads(result.stdout) if result.stdout.strip() else None

    def assert_error(self, outcome, code):
        self.assertFalse(outcome['ok'])
        self.assertIn(code, [error['code'] for error in outcome['errors']])

    def test_valid_inventory_passes(self):
        result, outcome = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(outcome['ok'])
        self.assertEqual(outcome['stats']['items'], 3)
        self.assertEqual(outcome['stats']['pins'], 1)
        self.assertEqual(outcome['stats']['network_components'], 1)
        self.assertEqual(outcome['stats']['network_refs'], 1)
        self.assertEqual(outcome['stats']['flake_inputs'], 1)

    def test_stale_pin_fails(self):
        inventory = self.inventory()
        inventory['items'][1]['pins'][0]['pattern'] = 'example-version = "2.0";'
        self.save_inventory(inventory)
        result, outcome = self.run_checker()
        self.assertEqual(result.returncode, 1)
        self.assert_error(outcome, 'pin-stale')
        self.assertIn('extension-example', result.stdout)

    def test_missing_network_ref_fails(self):
        inventory = self.inventory()
        inventory['items'] = [item for item in inventory['items'] if 'network_ref' not in item]
        self.save_inventory(inventory)
        result, outcome = self.run_checker()
        self.assertEqual(result.returncode, 1)
        self.assert_error(outcome, 'network-ref-missing')
        self.assertIn('alpha', result.stdout)

    def test_network_item_cannot_duplicate_version_or_source_data(self):
        inventory = self.inventory()
        item = next(item for item in inventory['items'] if 'network_ref' in item)
        item['current']['version'] = '1.0'
        item['pins'] = [{'file': 'system/config/network/maintenance/sources.json', 'pattern': '"version": "1.0"'}]
        self.save_inventory(inventory)
        result, outcome = self.run_checker()
        self.assertEqual(result.returncode, 1)
        self.assert_error(outcome, 'network-duplicate-version')
        self.assert_error(outcome, 'network-duplicate-pin')

    def test_duplicate_ids_fail(self):
        inventory = self.inventory()
        inventory['items'].append(copy.deepcopy(inventory['items'][1]))
        self.save_inventory(inventory)
        result, outcome = self.run_checker()
        self.assertEqual(result.returncode, 1)
        self.assert_error(outcome, 'duplicate-id')
        self.assertIn('extension-example', result.stdout)

    def test_unknown_schema_fails(self):
        inventory = self.inventory()
        inventory['schema'] = 2
        self.save_inventory(inventory)
        result, outcome = self.run_checker()
        self.assertEqual(result.returncode, 1)
        self.assert_error(outcome, 'schema')
        self.assertIn('unsupported inventory schema', result.stdout)

    def test_real_inventory_passes(self):
        result = subprocess.run(
            [sys.executable, str(CHECKER), '--json'],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        outcome = json.loads(result.stdout)
        self.assertTrue(outcome['ok'])
        inventory = json.loads(REAL_INVENTORY.read_text())
        sources = json.loads(REAL_SOURCES.read_text())
        self.assertEqual(outcome['stats']['items'], len(inventory['items']))
        self.assertEqual(outcome['stats']['network_components'], len(sources['components']))
        self.assertEqual(outcome['stats']['network_refs'], len(sources['components']))

    def test_online_observations_use_official_metadata(self):
        spec = importlib.util.spec_from_file_location("checker", CHECKER)
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        class Response:
            url = "https://addons.mozilla.org/api/v5/addons/addon/fixture/"
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit):
                return json.dumps({"current_version": {"version": "1.2", "file": {
                    "hash": "sha256:fixture", "permissions": ["tabs"], "is_mozilla_signed": True}}}).encode()
        with patch.object(checker.urllib.request, 'urlopen', return_value=Response()):
            observed = checker.online_observations()
        self.assertEqual(observed['ublock-origin']['permissions'], ['tabs'])
        self.assertEqual(observed['kiss-translator']['version'], '1.2')


if __name__ == '__main__':
    unittest.main(verbosity=2)
