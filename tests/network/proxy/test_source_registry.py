"""Selection transactions with synthetic root-approved registries and no network."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('broker', sys.argv.pop(1))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
worker = sys.argv.pop(1)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'sources').mkdir()
        # Nix builders are unprivileged. Root file ownership is exercised in
        # isolation-vm; here replace only that prerequisite, keeping real I/O.
        def private(path, maximum=1024 * 1024):
            path = Path(path)
            if path.is_symlink() or path.stat().st_mode & 0o077:
                raise ValueError('private fixture required')
            return path.read_bytes()
        for target, replacement in (('read_private', private),
                                    ('state_root', lambda path: path.mkdir(mode=0o700, exist_ok=True))):
            context = patch.object(b, target, replacement)
            context.start()
            self.addCleanup(context.stop)

    def register(self, identity, mapping=None, previous='none'):
        registry = {'id': identity, 'subscription': {'url': 'https://subscription.example/' + identity}}
        if mapping:
            registry['gui-profile'] = mapping
        data = b.canonical(json.dumps(registry))
        b.register(self.root, data, b.digest(data), previous)
        return data

    def test_catalog_preserves_legacy_source_revisions(self):
        legacy = {'id': 'one', 'subscription': {'url': 'https://subscription.example/one'}, 'providers': {}}
        other = dict(legacy, id='two', subscription={'url': 'https://subscription.example/two'})
        old = b.canonical(json.dumps(legacy))
        catalog = json.dumps({'sources': [legacy, other]})
        self.assertEqual(b.definitions(json.dumps(legacy)), {'one': old})
        self.assertEqual(b.definitions(catalog, 'one'), {'one': old})
        self.assertEqual(b.registration(b.definitions(catalog, 'one')), old)
        with self.assertRaisesRegex(b.DefinitionError, 'register with --id'):
            b.registration(b.definitions(catalog))
        with self.assertRaisesRegex(b.DefinitionError, 'absent'):
            b.definitions(catalog, '../two')

    def test_review_catalog_never_prints_endpoints_or_credentials(self):
        self.register('one')
        entries = [{'id': name, 'subscription': {'url': 'https://private.example/' + name,
                    'headers': {'Authorization': 'Bearer private-fixture'}}} for name in ('one', 'two')]
        review = b.review_definitions(self.root, b.definitions(json.dumps({'sources': entries})))
        self.assertEqual([v['source_id'] for v in review], ['one', 'two'])
        self.assertNotEqual(review[0]['previous'], 'none')
        self.assertEqual(review[1]['previous'], 'none')
        self.assertNotIn('private', json.dumps(review))
        self.assertNotIn('https://', json.dumps(review))

    def test_registering_one_catalog_entry_preserves_the_others(self):
        first = self.register('one')
        source = self.root / 'sources/one'
        b.atomic(source / 'approval.json', b'{"fixture":true}')
        (source / 'approved').mkdir()
        b.atomic(source / 'approved/settled.json', b'{"digest":"fixture"}')
        entries = [json.loads(first), {'id': 'two', 'subscription': {'url': 'https://subscription.example/two'}}]
        values = b.definitions(json.dumps({'sources': entries}), 'two')
        data = b.registration(values)
        b.register(self.root, data, b.digest(data), 'none')
        self.assertEqual(b.selected(self.root).name, 'one')
        self.assertEqual((source / 'registry.json').read_bytes(), first)
        self.assertTrue((source / 'approval.json').exists())
        self.assertTrue((source / 'approved/settled.json').exists())
        with self.assertRaises(ValueError):
            b.register(self.root, first, b.digest(data), b.digest(first))

    def test_catalog_rejects_duplicates_empty_and_unknown_fields(self):
        entry = {'id': 'one', 'subscription': {'url': 'https://subscription.example/one'}}
        bad = [{'sources': []}, {'sources': 'one'}, {'sources': [entry, entry]},
               {'sources': [entry] * 257}, {'sources': [entry], 'selected': 'one'},
               {'sources': [dict(entry, **{'gui-profile': 'same'}),
                            dict(entry, id='two', **{'gui-profile': 'same'})]}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                b.definitions(json.dumps(value))
        with self.assertRaisesRegex(b.DefinitionError, 'duplicate JSON field'):
            b.definitions('{"id":"one","id":"two","subscription":{"url":"https://example.invalid/"}}')

    def test_registration_selection_failure_and_old_revision(self):
        a = self.register('one')
        self.register('two')
        self.assertEqual(b.selected(self.root).name, 'one')
        with patch.object(b, 'refresh', side_effect=ValueError('fetch')):
            with self.assertRaises(ValueError): b.use({}, self.root, 'two')
        self.assertEqual(b.selected(self.root).name, 'one')
        with patch.object(b, 'refresh') as refresh:
            self.assertTrue(b.use({}, self.root, 'two'))
            refresh.assert_called_once_with({}, self.root / 'sources/two')
            self.assertTrue(b.use({}, self.root, 'one', b.digest(a)))
            for identity in ('../one', 'missing', '/one', 'ONE'):
                with self.assertRaises(ValueError): b.use({}, self.root, identity)
            with self.assertRaises(ValueError): b.use({}, self.root, 'two', '0' * 64)
        with self.assertRaises(ValueError): self.register('one', 'uid-1', '0' * 64)
        self.assertEqual((self.root / 'sources/one/registry.json').read_bytes(), a)

    def test_mapping_cannot_create_sources_or_read_gui_configuration(self):
        self.register('one', 'uid-one')
        self.register('two', 'uid-two')
        with self.assertRaises(ValueError): self.register('three', 'uid-one')
        index = self.root / 'profiles.yaml'
        settings = {'gui_index': str(index), 'worker': worker, 'desktop_uid': os.getuid()}
        for current in ('unknown', 'https://evil.example/', '../two'):
            index.write_text(json.dumps({'current': current, 'items': [{'url': 'https://evil.example/'}]}))
            self.assertFalse(b.gui_choice(settings, self.root))
            self.assertEqual(b.selected(self.root).name, 'one')
        index.write_text('current: uid-two\nitems: [{uid: uid-two, url: https://evil.example/}]')
        with patch.object(b, 'refresh') as refresh:
            self.assertTrue(b.gui_choice(settings, self.root))
            refresh.assert_called_once_with(settings, self.root / 'sources/two')
        self.assertEqual(b.selected(self.root).name, 'two')
        self.assertEqual(sorted(p.name for p in (self.root / 'sources').iterdir()), ['one', 'two'])

    def test_legacy_migration_retains_generations_and_approvals(self):
        data = b.canonical(json.dumps({'id': 'legacy', 'subscription': {'url': 'https://source.example/'}}))
        b.atomic(self.root / 'registry.json', data)
        generation = self.root / 'generations' / ('a' * 64)
        generation.mkdir(parents=True)
        b.atomic(generation / 'source-manifest.json', b'{"fixture":true}')
        (self.root / 'current').symlink_to(generation)
        (self.root / 'approved').mkdir()
        b.atomic(self.root / 'approved/settled.json', b'{"digest":"fixture"}')
        b.migrate(self.root)
        selected = b.selected(self.root)
        self.assertEqual((selected / 'current').resolve(), selected / 'generations' / ('a' * 64))
        self.assertTrue((selected / 'approved/settled.json').is_file())
        b.migrate(self.root)
        self.assertEqual(b.selected(self.root), selected)


if __name__ == '__main__':
    unittest.main()
