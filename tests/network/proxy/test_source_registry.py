"""Selection transactions with synthetic root-approved registries and no network."""
import importlib.util
import fcntl
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('broker', sys.argv.pop(1))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
worker = sys.argv.pop(1)


class RegistryTests(unittest.TestCase):
    def test_add_confirms_replaces_current_revision_and_verifies_after_publication(self):
        self.register('source-one')
        previous = self.register('source-two')
        catalog = self.root / 'catalog'
        definition = {'id': 'source-two', 'subscription': {'url': 'https://fixture.example/new'}}
        b.atomic(catalog, json.dumps({'sources': [definition]}).encode())
        settings = self.root / 'settings'
        stage_settings = self.root / 'stage-settings'
        stage_settings.write_text(json.dumps(dict(state=str(self.root), snapshot_tool='/fixed/stage')))
        settings.write_text(json.dumps(dict(root=str(self.root), catalog=str(catalog),
            stageSettings=str(stage_settings), manual_refresh='/fixed/refresh', launcher='/fixed/launcher')))
        candidate = 'a' * 64

        def download(config, path):
            self.assertEqual(config['_expected_selection'].name, 'source-one')
            self.assertEqual(b.selected(self.root).name, 'source-one')
            self.assertEqual(b.read_private(path / 'registry.json'), b.canonical(json.dumps(definition)))
            generation = path / 'generation'
            generation.mkdir()
            (path / 'current').symlink_to(generation)

        def verify(config, stage, digest, mode, acquire):
            with (self.root / 'source.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(b.selected(self.root).name, 'source-two')
            self.assertEqual((digest, mode), (candidate, 'proxy'))
            return dict(result='accepted', reason=None, active_source='source-two')

        stage = SimpleNamespace(read_record=lambda _: {'mode': 'proxy'}, snapshot_digest=lambda _: candidate)
        manual = SimpleNamespace(module=lambda *_: stage, verify_selection=verify)
        with patch.object(sys, 'argv', ['clash-source', str(settings), 'add', 'source-two']), \
                patch.object(b.os, 'geteuid', return_value=0), patch.object(b, 'load_module', return_value=manual), \
                patch.object(b, 'refresh', side_effect=download), patch('builtins.input', return_value='yes'), \
                patch.object(sys, 'stdout', new_callable=StringIO) as output:
            b.main()
        reviewed, verdict = map(json.loads, output.getvalue().splitlines())
        self.assertEqual(reviewed['previous'], b.digest(previous))
        self.assertEqual(verdict, dict(requested_source='source-two', active_source='source-two',
                                      result='accepted', reason=None))
        self.assertNotIn('https://', output.getvalue())

    def test_add_refuses_catalog_changes_while_awaiting_confirmation(self):
        previous = self.register('source-one')
        catalog = self.root / 'catalog'
        definition = json.loads(previous)
        b.atomic(catalog, previous)

        def change_catalog():
            definition['subscription']['url'] = 'https://fixture.example/changed'
            b.atomic(catalog, json.dumps(definition).encode())
            return 'yes'

        with (self.root / 'source.lock').open('a') as lock, patch('builtins.input', side_effect=change_catalog), \
                patch.object(sys, 'stdout', new_callable=StringIO):
            b.acquire(lock)
            with self.assertRaisesRegex(b.DefinitionError, 'changed during confirmation'):
                b.confirm_add({'catalog': catalog}, self.root, lock, 'source-one')
        self.assertEqual(b.read_private(self.root / 'sources/source-one/registry.json'), previous)

    def test_authorized_sources_parse_and_fail_closed(self):
        directory = Path(tempfile.mkdtemp())
        path = directory / 'sources'
        path.write_text('one\nbad id\n\none\nTWO\n')
        self.assertEqual(b.authorized_sources({'insecure_sources': str(path)}), {'one'})
        self.assertEqual(b.authorized_sources({}), set())
        self.assertEqual(b.authorized_sources({'insecure_sources': str(directory / 'missing')}), set())

    def test_refresh_requests_sanitizing_only_for_authorized_sources(self):
        self.register('one')
        self.register('two')
        source = self.root / 'sources/one'
        policy = self.root / 'policy.json'
        policy.write_text('{}')
        whitelist = self.root / 'whitelist'
        whitelist.write_text('one\n')
        settings = dict(root=str(self.root), policy=str(policy), forced_domains=[], assets={},
                        setpriv='/fixed/setpriv', python='/fixed/python', worker=worker, ca='/fixed/ca',
                        insecure_sources=str(whitelist))
        seen = []
        def download(*args, **kwargs):
            seen.append(json.loads(kwargs['input']))
            kwargs['stdout'].write(json.dumps(dict(yaml='proxies: []\n', responses=[], dropped=0)).encode())
            return SimpleNamespace(returncode=0)
        with patch.object(b.subprocess, 'run', side_effect=download):
            b.refresh(settings, source)
            self.assertIs(seen[-1].get('sanitize_insecure'), True)
            b.refresh(settings, self.root / 'sources/two')
            self.assertIs(seen[-1].get('sanitize_insecure'), False)
            whitelist.unlink()
            b.refresh(settings, source)
            self.assertIs(seen[-1].get('sanitize_insecure'), False)

    def test_network_wait_releases_publication_lock_and_rechecks_registration_and_selection(self):
        self.register('one')
        self.register('two')
        source = self.root / 'sources/one'
        policy = self.root / 'policy.json'
        policy.write_text('{}')
        settings = dict(root=str(self.root), policy=str(policy), forced_domains=[], assets={},
                        setpriv='/fixed/setpriv', python='/fixed/python', worker=worker, ca='/fixed/ca')
        def download(*args, **kwargs):
            with (self.root / 'source.lock').open('a') as foreground:
                fcntl.flock(foreground, fcntl.LOCK_EX | fcntl.LOCK_NB)
            kwargs['stdout'].write(json.dumps(dict(yaml='proxies: []\n', responses=[], dropped=0)).encode())
            return SimpleNamespace(returncode=0)
        with (self.root / 'source.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            settings['_fetch_lock'] = lock
            with patch.object(b.subprocess, 'run', side_effect=download):
                b.refresh(settings, source)
            current = (source / 'current').readlink()
            original = (source / 'registry.json').read_bytes()
            def revoked(*args, **kwargs):
                result = download(*args, **kwargs)
                b.atomic(source / 'registry.json', original + b'\n')
                return result
            with patch.object(b.subprocess, 'run', side_effect=revoked), self.assertRaisesRegex(ValueError, 'changed during download'):
                b.refresh(settings, source)
            self.assertEqual((source / 'current').readlink(), current)
            b.atomic(source / 'registry.json', original)
            def switched(*args, **kwargs):
                result = download(*args, **kwargs)
                b.select(self.root, self.root / 'sources/two')
                return result
            with patch.object(b.subprocess, 'run', side_effect=switched), self.assertRaisesRegex(ValueError, 'changed during download'):
                b.refresh(settings, source)
            self.assertEqual((source / 'current').readlink(), current)

    def test_fetch_diagnostics_follow_worker_outcome_without_private_error_text(self):
        state = self.root / 'state'
        state.mkdir()
        (state / 'boot-id').write_text('fixture-boot')
        settings_file = self.root / 'stage.json'
        settings_file.write_text(json.dumps(dict(state=str(state))))
        settings = dict(stageSettings=str(settings_file))
        with patch.object(b, 'refresh_generation'):
            b.refresh(settings, self.root)
        self.assertEqual(json.loads((state / 'source-fetch.json').read_text())['result'], 'downloaded')
        with patch.object(b, 'refresh_generation', side_effect=b.SourceFailure(
                'https://private.invalid/token', 'fetch', 'http rejected', 503)):
            with self.assertRaises(b.SourceFailure):
                b.refresh(settings, self.root)
        record = json.loads((state / 'source-fetch.json').read_text())
        self.assertEqual((record['result'], record['reason'], record['http_status']), ('failed', 'http rejected', 503))
        self.assertNotIn('private', json.dumps(record))
        (state / 'boot-id').unlink()
        with patch.object(b, 'refresh_generation') as generation:
            b.refresh(settings, self.root)
        generation.assert_called_once()

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
        b.atomic(source / 'review.json', b'{"fixture":true}')
        (source / 'approved').mkdir()
        b.atomic(source / 'approved/settled.json', b'{"digest":"fixture"}')
        entries = [json.loads(first), {'id': 'two', 'subscription': {'url': 'https://subscription.example/two'}}]
        values = b.definitions(json.dumps({'sources': entries}), 'two')
        data = b.registration(values)
        b.register(self.root, data, b.digest(data), 'none')
        self.assertEqual(b.selected(self.root).name, 'one')
        self.assertEqual((source / 'registry.json').read_bytes(), first)
        self.assertTrue((source / 'approval.json').exists())
        self.assertTrue((source / 'review.json').exists())
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
        b.atomic(self.root / 'sources/two/review.json', b'{}')
        with patch.object(b, 'refresh', side_effect=ValueError('fetch')):
            with self.assertRaises(ValueError): b.use({}, self.root, 'two')
        self.assertEqual(b.selected(self.root).name, 'one')
        with patch.object(b, 'refresh') as refresh:
            self.assertTrue(b.use({}, self.root, 'two'))
            self.assertFalse((self.root / 'sources/two/review.json').exists())
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
