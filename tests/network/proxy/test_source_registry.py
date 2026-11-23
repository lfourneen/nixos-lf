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
refresh_tool = b.load_module('source_refresh', sys.argv.pop(1))
stage_tool = b.load_module('source_stage', sys.argv.pop(1))


class RegistryTests(unittest.TestCase):
    def test_recovery_flush_failure_leaves_an_invalidated_marker_for_retry(self):
        recovery = b.load_module('source_recovery', os.environ.get('SOURCE_RECOVERY',
            Path(worker).with_name('source_recovery.py')))
        marker = self.root / 'source-recovery'
        marker.write_text('42\n')
        failure = b.subprocess.CalledProcessError(1, ['nft'])
        with patch.object(recovery, 'STATE', self.root), patch.object(recovery, 'ACTIVE', marker), \
                patch.object(recovery, 'nft', side_effect=[failure, None]) as nft:
            with self.assertRaises(b.subprocess.CalledProcessError):
                recovery.revoke({})
            self.assertNotEqual(marker.read_text(), '42\n')
            recovery.revoke({})
            self.assertFalse(marker.exists())
            self.assertEqual([call.args[1] for call in nft.call_args_list], [recovery.FLUSH] * 2)

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
        path = self.root / 'allowlist'
        settings = {'insecure_sources': str(path)}
        for data, expected in ((b'one\n\ntwo\none\n', {'one', 'two'}),
                               (b'one\nbad id\n', set()), (b'one\nTWO\n', set()),
                               (b'one\n\xff\n', set()), (b'\n', set())):
            with self.subTest(data=data):
                path.write_bytes(data)
                self.assertEqual(b.authorized_sources(settings), expected)
        path.unlink()
        self.assertEqual(b.authorized_sources(settings), set())
        self.assertEqual(b.authorized_sources({}), set())

    def test_refresh_requests_sanitizing_only_for_authorized_sources(self):
        self.register('one')
        self.register('two')
        source = self.root / 'sources/one'
        policy = self.root / 'policy.json'
        policy.write_text('{}')
        whitelist = self.root / 'whitelist'
        settings = dict(root=str(self.root), policy=str(policy), forced_domains=[], assets={},
                        setpriv='/fixed/setpriv', python='/fixed/python', worker=worker, ca='/fixed/ca',
                        insecure_sources=str(whitelist))
        w = b.load_module('worker_fixture', worker)
        audit = b.load_module('audit_fixture', os.environ['SOURCE_AUDIT'])
        good = dict(name='good', type='http', server='1.1.1.1', port=443)
        bad = dict(good, name='rejected', **{'skip-cert-verify': True})
        named = dict(bad, name='named')
        document = dict(proxies=[good, bad, named], rules=['MATCH,good'])
        exceptions = {'insecure-proxy-exceptions': ['named']}
        policy.write_text(json.dumps({'lf-private-targets': exceptions}))
        def download(*args, **kwargs):
            request = json.loads(kwargs['input'])
            result = w.prepare(request, download=lambda *_: json.dumps(document).encode())
            kwargs['stdout'].write(json.dumps(result).encode())
            return SimpleNamespace(returncode=0)
        with patch.object(b.subprocess, 'run', side_effect=download):
            for content, selected, dropped in (
                    (b'one\none\n', source, 2), (b'one\n', self.root / 'sources/two', 0),
                    (b'one\nbad id\n', source, 0), (b'one\n\xff', source, 0), (None, source, 0)):
                with self.subTest(content=content, selected=selected.name):
                    if content is None:
                        whitelist.unlink()
                    else:
                        whitelist.write_bytes(content)
                    b.refresh(settings, selected)
                    generation = selected / 'current'
                    result = json.loads((generation / 'source-manifest.json').read_text())
                    config = audit.load(generation / 'clash-verge.yaml')
                    findings = audit.audit(config, settings={'insecure_exceptions': ['named']})
                    self.assertEqual(result['dropped'], dropped)
                    self.assertEqual('named' in {node['name'] for node in config['proxies']}, not dropped)
                    self.assertEqual(findings['deny'], {} if dropped else {'certificate': 2})

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
            def re_registered(*args, **kwargs):
                result = download(*args, **kwargs)
                b.atomic(source / 'registry.json', original)
                return result
            with patch.object(b.subprocess, 'run', side_effect=re_registered), self.assertRaises(b.RequestChanged):
                b.refresh(settings, source)
            self.assertEqual((source / 'current').readlink(), current)

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

    def test_cached_recovery_uses_the_recorded_download_and_refuses_stale_authority(self):
        registry = self.register('one')
        source = b.selected(self.root)
        policy = self.root / 'policy.json'
        policy.write_text('{}')
        config = b'proxies: []\n'
        manifest = {'source_id': 'one', 'revision': b.digest(registry), 'yaml': b.digest(config),
                    'policy': b.digest(policy.read_bytes())}
        downloaded = source / 'generations' / b.digest(json.dumps(manifest, sort_keys=True).encode())
        downloaded.mkdir(mode=0o700, parents=True)
        b.atomic(downloaded / 'source-manifest.json', json.dumps(manifest).encode())
        b.atomic(downloaded / 'clash-verge.yaml', config)
        b.atomic(source / 'status.json', json.dumps(dict(state='fetched', generation=downloaded.name)).encode())
        local = source / 'generations' / ('a' * 64)
        local.mkdir()
        b.select_generation(source, local)
        settings = dict(managed_policy=str(policy))
        read_input = stage_tool.read_input
        expected = stage_tool.snapshot_digest(downloaded)
        with patch.object(stage_tool, 'read_input', side_effect=lambda directory, name, _: read_input(directory, name, os.geteuid())):
            self.assertEqual(b.cached_generation(source, stage_tool, settings), expected)
            self.assertEqual((source / 'current').resolve(), downloaded)
            b.select_generation(source, local)
            approved = source / 'approved'
            approved.mkdir()
            b.atomic(approved / 'settled.json', json.dumps({'digest': expected}).encode())
            with self.assertRaisesRegex(ValueError, 'already the accepted snapshot'):
                b.cached_generation(source, stage_tool, settings)
            policy.write_text('{"changed": true}')
            with self.assertRaisesRegex(ValueError, 'does not match the active routing policy'):
                b.cached_generation(source, stage_tool, settings)
            policy.write_text('{}')
            b.atomic(source / 'registry.json', json.dumps({'id': 'one', 'subscription': {'url': 'https://fixture.example/new'}}).encode())
            with self.assertRaisesRegex(ValueError, 'source revision revoked'):
                b.cached_generation(source, stage_tool, settings)
            self.assertEqual((source / 'current').resolve(), local)

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

    def test_registration_checks_approved_revision_and_selection(self):
        a = self.register('one')
        self.register('two')
        self.assertEqual(b.selected(self.root).name, 'one')
        b.atomic(self.root / 'sources/two/review.json', b'{}')
        b.select(self.root, b.source_path(self.root, 'two'))
        self.assertEqual(b.selected(self.root).name, 'two')
        self.assertFalse((self.root / 'sources/two/review.json').exists())
        for identity in ('../one', 'missing', '/one', 'ONE'):
            with self.assertRaises(ValueError):
                b.source_path(self.root, identity)
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
        self.assertEqual(b.gui_choice(settings, self.root), self.root / 'sources/two')
        self.assertEqual(b.selected(self.root).name, 'one')
        self.assertEqual(sorted(p.name for p in (self.root / 'sources').iterdir()), ['one', 'two'])

    def test_gui_fetch_reuses_generation_and_rechecks_selection_after_network_wait(self):
        self.register('one', 'uid-one')
        self.register('two', 'uid-two')
        index = self.root / 'profiles.yaml'
        state = self.root / 'state'
        state.mkdir()
        boot = '00000000-1111-2222-3333-444444444444'
        (state / 'boot-id').write_text(boot)
        (state / 'intent').write_text('proxy')
        state_file = state / 'state.json'
        state_file.write_text('{"mode":"blocked"}')
        policy = self.root / 'policy.json'
        policy.write_text('{}')
        stage_settings = dict(state=str(state), source_root=str(self.root), managed_policy=str(policy))
        stage_file = self.root / 'stage.json'
        stage_file.write_text(json.dumps(stage_settings))
        settings = dict(root=str(self.root), gui_index=str(index), desktop_uid=os.getuid(),
            worker=worker, policy=str(policy), assets={}, forced_domains=[], setpriv='/fixed/setpriv',
            python='/fixed/python', ca='/fixed/ca', stageSettings=str(stage_file))
        candidate = self.root / 'sources/two/current'
        gui_request = state / 'source-gui-request.json'
        application = state / 'source-apply.json'
        attempts = []
        responses = ['subscription', 'provider']

        def download(*args, **kwargs):
            with (self.root / 'source.lock').open('a') as other:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                attempts.append(self.mode)
                if self.mode == 'stale':
                    index.write_text('current: uid-one\n')
                if self.mode == 'timeout':
                    kwargs['stdout'].write(b'{"error":"fetch","reason":"connection timeout"}')
                    self.mode = 'success'
                    return SimpleNamespace(returncode=1)
            kwargs['stdout'].write(json.dumps(dict(yaml='proxies: []\n', responses=responses, dropped=0)).encode())
            return SimpleNamespace(returncode=0)

        def prepare_gui():
            index.write_text('current: uid-two\n')
            self.assertTrue(b.request_gui(settings, self.root))

        with patch.object(b, 'refresh_context', return_value=(refresh_tool, stage_tool, stage_settings)), \
                patch.object(stage_tool, 'source_directory', side_effect=lambda _: b.selected(self.root)), \
                patch.object(refresh_tool, 'healthy_runtime', return_value='a' * 64), \
                patch.object(refresh_tool.time, 'clock_gettime', return_value=120), \
                patch.object(b.subprocess, 'run', side_effect=download), \
                (self.root / 'source.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            prepare_gui()
            b.automatic_refresh(settings, self.root, lock)
            self.assertEqual(attempts, [])
            self.assertTrue(gui_request.exists())
            self.assertEqual(b.selected(self.root).name, 'one')

            def allow_proxy_download():
                state_file.write_text('{"mode":"proxy","policy_version":1}')
                stage_tool.atomic_record(state / 'source-refresh-stable.json',
                    dict(since=0, digest='a' * 64, boot_id=boot, policy_version=1, authorization_epoch=''))

            allow_proxy_download()
            self.mode = 'stale'
            b.automatic_refresh(settings, self.root, lock)
            self.assertFalse(candidate.exists())
            self.assertFalse(application.exists())
            self.assertEqual(b.selected(self.root).name, 'one')

            prepare_gui()
            self.mode = 'timeout'
            def backoff(seconds):
                self.assertEqual(seconds, 15)
                self.assertEqual(b.selected(self.root).name, 'one')
                self.assertEqual(stage_tool.read_record(state / 'source-fetch.json')['reason'], 'connection timeout')
                with (self.root / 'source.lock').open('a') as other:
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            b.automatic_refresh(settings, self.root, lock, wait=backoff)
            self.assertEqual(attempts, ['stale', 'timeout', 'success'])
            self.assertEqual(b.selected(self.root).name, 'two')
            self.assertFalse(gui_request.exists())
            queued = stage_tool.read_record(application)['binding']
            self.assertEqual(queued['candidate'], stage_tool.snapshot_digest(candidate.resolve()))
            manifest = stage_tool.read_record(candidate / 'source-manifest.json')
            self.assertEqual(manifest['responses'], responses)
            self.assertEqual(stage_tool.read_record(state / 'source-fetch.json')['result'], 'downloaded')

            refresh_tool.cancel_automatic(stage_settings)
            b.select(self.root, self.root / 'sources/one')
            state_file.write_text('{"mode":"direct","policy_version":2}')
            (state / 'intent').write_text('direct')
            prepare_gui()
            b.automatic_refresh(settings, self.root, lock)
            self.assertEqual(attempts, ['stale', 'timeout', 'success', 'success'])
            self.assertEqual(b.selected(self.root).name, 'two')
            self.assertFalse(application.exists(), 'the GUI must not request proxy operation in A')

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

    def test_revoking_selected_legacy_source_in_a_resumes_retired_directory_cleanup(self):
        data = b.canonical(json.dumps({'id': 'legacy', 'subscription': {'url': 'https://source.example/'}}))
        b.atomic(self.root / 'registry.json', data)
        state, runtime = self.root / 'state', self.root / 'runtime'
        state.mkdir()
        runtime.mkdir()
        (state / 'intent').write_text('direct')
        settings = dict(state=str(state), runtime=str(runtime), source_root=str(self.root))
        with patch.object(b, 'refresh_context', return_value=(refresh_tool, stage_tool, settings)), \
                patch.object(b.subprocess, 'run') as commands:
            with patch.object(b.shutil, 'rmtree', side_effect=OSError('fixture interrupted cleanup')), \
                    self.assertRaisesRegex(OSError, 'interrupted cleanup'):
                b.revoke_source({}, self.root, 'legacy', b.digest(data))
            self.assertEqual(b.pending_revocation(self.root)['protect'], False)
            self.assertTrue((self.root / 'sources/.revoked-legacy/registry.json').is_file())
            self.assertIsNone(b.requested_source(self.root))
            result = b.revoke_source({}, self.root, 'legacy', b.digest(data))
            self.assertEqual(result['result'], 'revoked')
            self.assertIsNone(result['active_source'])
            commands.assert_not_called()
        b.migrate(self.root)
        self.assertIsNone(b.requested_source(self.root))
        self.assertFalse((self.root / 'sources/.revoked-legacy').exists())
        self.assertEqual(b.pending_revocation(self.root), {})
        with self.assertRaisesRegex(ValueError, 'no selected source'):
            stage_tool.source_directory(settings)

    def test_active_revocation_protects_before_retiring_old_revision_and_is_retriable(self):
        active_registry = self.register('one')
        two = self.register('two')
        state, runtime = self.root / 'state', self.root / 'runtime'
        state.mkdir()
        (state / 'boot-id').write_text('00000000-1111-2222-3333-444444444444')
        (state / 'intent').write_text('proxy')
        snapshots = runtime / 'snapshots'
        candidate = snapshots / 'candidate'
        candidate.mkdir(parents=True)
        (candidate / 'clash-verge.yaml').write_text('proxies: []\n')
        manifest = dict(source_id='one', revision=b.digest(active_registry))
        (candidate / 'source-manifest.json').write_text(json.dumps(manifest))
        snapshot = stage_tool.snapshot_digest(candidate)
        active = snapshots / snapshot
        candidate.rename(active)
        (runtime / 'effective').symlink_to(active)
        for name in ('config-digest', 'authorized-config'):
            (state / name).write_text(snapshot)
        new_registry = self.register('one', 'replacement', b.digest(active_registry))
        b.select(self.root, self.root / 'sources/two')
        (state / 'source-apply.json').write_text('{"binding":{"source_id":"one"}}')
        (state / 'profile-refresh-commit.json').write_text(json.dumps(dict(target=snapshot)))
        settings = dict(state=str(state), runtime=str(runtime), source_root=str(self.root))

        fail_stop = True
        actions = []
        def command(argv, **kwargs):
            action = argv[-2:]
            actions.append(action)
            self.assertEqual(b.pending_revocation(self.root)['revision'], b.digest(new_registry))
            self.assertTrue((self.root / 'sources/one/registry.json').is_file())
            self.assertTrue(active.is_dir())
            if action[0] == 'start':
                with (state / 'authorization.lock').open('a') as observer, self.assertRaises(BlockingIOError):
                    fcntl.flock(observer, fcntl.LOCK_EX | fcntl.LOCK_NB)
                for name in ('config-digest', 'authorized-config'):
                    (state / name).unlink(missing_ok=True)
            else:
                with (state / 'authorization.lock').open('a') as observer:
                    fcntl.flock(observer, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if fail_stop:
                    raise b.subprocess.CalledProcessError(1, argv)

        with patch.object(b, 'refresh_context', return_value=(refresh_tool, stage_tool, settings)), \
                patch.object(b.subprocess, 'run', side_effect=command):
            with self.assertRaisesRegex(ValueError, 'revision changed'):
                b.revoke_source({'systemctl': '/fixed/systemctl'}, self.root, 'one', b.digest(active_registry))
            with self.assertRaises(b.subprocess.CalledProcessError):
                b.revoke_source({'systemctl': '/fixed/systemctl'}, self.root, 'one', b.digest(new_registry))
            self.assertTrue((self.root / 'revoking.json').exists())
            fail_stop = False
            result = b.revoke_source({'systemctl': '/fixed/systemctl'}, self.root, 'one', b.digest(new_registry))
        self.assertEqual(actions, [['start', 'clash-source-revoke.service'], ['stop', 'clash-verge.service']] * 2)
        self.assertEqual(result['result'], 'revoked')
        self.assertIsNone(result['active_source'])
        self.assertFalse(active.exists())
        self.assertFalse((runtime / 'effective').is_symlink())
        self.assertFalse((state / 'source-apply.json').exists())
        self.assertFalse((state / 'profile-refresh-commit.json').exists())
        self.assertFalse((self.root / 'revoking.json').exists())
        self.assertFalse((self.root / 'sources/one').exists())
        self.assertEqual(b.selected(self.root).name, 'two')
        self.assertEqual((self.root / 'sources/two/registry.json').read_bytes(), two)


if __name__ == '__main__':
    unittest.main()
