"""Immutable selection, failure boundaries, status binding and real DNS isolation."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else Path(__file__).resolve().parents[3]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


routing = module('routing', 'system/config/network/proxy/routing.py')
state = module('state', 'system/config/network/proxy/state.py')
dns = module('real_dns', 'system/config/network/dns/real_proxy.py')


class RoutingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state = self.root / 'proxy-mode'
        self.state.mkdir()
        (self.state / 'boot-id').write_text('fixture-boot')
        self.settings = dict(enabled=True, state=str(self.state), runtime=str(self.root / 'core'),
                             authorization_tool=str(ROOT / 'system/config/network/proxy/authorization.py'),
                             rules_tool=str(ROOT / 'system/config/network/firewall/rules.py'),
                             policies={}, artifacts={})
        for mode in routing.MODES:
            artifact = self.root / mode
            artifact.mkdir()
            policy = artifact / 'policy.json'
            policy.write_text(json.dumps({'proxy-only': mode == 'proxy-only'}))
            self.settings['policies'][mode] = str(policy)
            self.settings['artifacts'][mode] = str(artifact)
        routing.initialize(self.settings)
        permission = patch.object(routing, 'safe_file', side_effect=lambda path, private=False: Path(path))
        permission.start()
        self.addCleanup(permission.stop)

    def test_boot_defaults_to_rules_and_restarts_do_not_reset_selection(self):
        record = routing.binding(self.settings)
        self.assertEqual(record['routing_requested'], 'rules')
        routing.activate(self.settings, 'proxy-only')
        routing.atomic(self.state / 'routing.json', dict(record, routing_requested='proxy-only',
            routing_policy_digest=routing.sha(Path(self.settings['policies']['proxy-only']).read_bytes()), routing_epoch=2))
        routing.initialize(self.settings)
        self.assertEqual(routing.binding(self.settings)['routing_requested'], 'proxy-only')
        (self.state / 'boot-id').write_text('another-boot')
        with self.assertRaises(ValueError):
            routing.initialize(self.settings)

    def test_unknown_stale_digest_and_foreign_policy_paths_never_become_rules(self):
        original = json.loads((self.state / 'routing.json').read_text())
        for change in (dict(routing_requested='global'), dict(routing_epoch=0), dict(boot_id='old'),
                       dict(routing_policy_digest='0' * 64), dict(routing_phase='accepted')):
            routing.atomic(self.state / 'routing.json', original | change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                routing.binding(self.settings)
        routing.atomic(self.state / 'routing.json', original)
        (self.state / 'routing-active').unlink()
        (self.state / 'routing-active').symlink_to(self.root)
        with self.assertRaises(ValueError):
            routing.binding(self.settings)

    def test_partial_cross_epoch_and_cross_digest_proofs_cannot_apply_routing(self):
        record = routing.binding(self.settings)
        (self.state / 'config-digest').write_text('snapshot')
        proof = dict(ok=True, boot_id='fixture-boot', digest='snapshot',
                     routing_epoch=record['routing_epoch'], routing_policy_digest=record['routing_policy_digest'],
                     scope={'requests': ['mixed-tcp', 'fake-ip-tcp']},
                     **dict.fromkeys(('data_plane_ok', 'route_ok', 'dns_ok', 'tun_ok', 'config_ok', 'listener_ok'), True))
        for change in (dict(ok=False, data_plane_ok=True), dict(routing_epoch=0),
                       dict(routing_policy_digest='b' * 64), dict(digest='another'), dict(boot_id='old'),
                       dict(scope={'requests': []}), dict(tun_ok=False), dict(dns_ok=False)):
            routing.atomic(self.state / 'health.json', proof | change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                routing.applied(self.settings)
        self.assertIsNone(routing.binding(self.settings)['routing_applied'])
        routing.atomic(self.state / 'health.json', proof)
        routing.applied(self.settings)
        applied = routing.binding(self.settings)
        self.assertEqual((applied['routing_applied'], applied['routing_phase']), ('rules', 'ready'))

    def test_preparation_failure_revokes_authority_and_preserves_the_requested_mode(self):
        source = self.root / 'source'
        source.mkdir()
        source_settings = self.root / 'source-settings'
        source_settings.write_text(json.dumps({'root': str(source)}))
        self.settings.update(source_settings=str(source_settings), stage_tool='stage', source_tool='source')
        for name in ('authorized-config', 'config-digest', 'health.json'):
            (self.state / name).write_text('old')
        (self.state / 'intent').write_text('proxy')
        stage = SimpleNamespace(clear_refresh=Mock())
        broker = SimpleNamespace(selected=lambda root: root, render_local=Mock(side_effect=ValueError('refused')))
        with patch.object(routing, 'module', side_effect=lambda path, _: stage if path == 'stage' else broker), \
                patch.object(routing, 'enforce') as enforce:
            with self.assertRaises(ValueError):
                routing.begin(self.settings, 'proxy-only')
        self.assertFalse((self.state / 'authorized-config').exists())
        self.assertFalse((self.state / 'health.json').exists())
        self.assertEqual(routing.binding(self.settings)['routing_requested'], 'proxy-only')
        self.assertEqual((self.state / 'intent').read_text(), 'proxy')
        enforce.assert_called_once_with(self.settings, 'blocked')

    def test_status_redacts_bad_records_and_marks_applied_policy_mismatch(self):
        record = routing.binding(self.settings)
        info = SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o644)
        with patch.object(Path, 'lstat', return_value=info):
            view = state.routing_view(self.root, record, 'fixture-boot')
            self.assertTrue(view['routing_consistent'])
            view = state.routing_view(self.root, record | dict(routing_epoch=2), 'fixture-boot')
            self.assertFalse(view['routing_consistent'])
            routing.atomic(self.state / 'routing.json', record | dict(routing_requested='private-token'))
            view = state.routing_view(self.root, record, 'fixture-boot')
            self.assertEqual(view['routing_requested'], 'unknown')
            self.assertNotIn('private-token', json.dumps(view))

    def test_strict_dns_failure_does_not_fall_back_to_host_unbound(self):
        record = routing.binding(self.settings)
        routing.atomic(self.state / 'routing.json', record | dict(routing_requested='proxy-only'))
        (self.state / 'intent').write_text('proxy')
        packet = b'\x12\x34\x01\x00\x00\x01' + b'\x00' * 6 + b'\x01a\x00\x00\x01\x00\x01'
        fake = Mock()
        fake.__enter__ = Mock(return_value=fake)
        fake.__exit__ = Mock(return_value=False)
        fake.connect.side_effect = ConnectionRefusedError()
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o644)), \
                patch.object(dns.socket, 'socket', return_value=fake), patch.object(dns.socket, 'create_connection') as host:
            with self.assertRaises(ConnectionRefusedError):
                dns.query({'state': str(self.state)}, packet)
            host.assert_not_called()
        reply = dns.error_reply(packet)
        self.assertEqual(reply[:2], packet[:2])
        self.assertEqual(reply[3] & 15, 2)
        self.assertFalse(reply[3] & 32, 'SERVFAIL cannot claim authenticated data')

    def test_dns_stream_deadline_is_shared_across_all_bytes(self):
        stream = Mock()
        stream.recv.return_value = b'x'
        with patch.object(dns.time, 'monotonic', side_effect=[0.0, .4, 1.1]):
            with self.assertRaises(TimeoutError):
                dns.receive(stream, 4, 1)
        self.assertEqual(stream.recv.call_count, 2)

    def test_nft_echo_and_listing_preserve_exact_node_tuples(self):
        table = {'table': {'family': 'inet', 'name': 'lf_routing'}}
        declaration = dict(family='inet', table='lf_routing', name='nodes4',
                           type=['ipv4_addr', 'inet_proto', 'inet_service'])
        echo = {'nftables': [{'add': entry} for entry in [table, {'set': declaration}, {'element': dict(
            family='inet', table='lf_routing', name='nodes4', elem={'set': [{'concat': ['1.1.1.1', 6, 443]}]})}]]}
        live = {'nftables': [table, {'set': declaration | dict(elem=[{'concat': ['1.1.1.1', 'tcp', 443]}])}]}
        self.assertEqual(routing.table_view(self.settings, echo), routing.table_view(self.settings, live))
        changed = copy.deepcopy(live)
        changed['nftables'][1]['set']['elem'][0]['concat'][2] = 8443
        self.assertNotEqual(routing.table_view(self.settings, echo), routing.table_view(self.settings, changed))


if __name__ == '__main__':
    unittest.main()
