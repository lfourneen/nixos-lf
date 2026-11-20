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
    def test_unknown_egress_and_cross_egress_proofs_fail_closed(self):
        with self.assertRaises(ValueError):
            routing.begin(self.settings, 'proxy-only', '../node')
        record = routing.binding(self.settings)
        routing.activate(self.settings, 'proxy-only')
        record.update(routing_requested='proxy-only', egress_requested='a' * 64, egress_epoch=1,
            routing_policy_digest=routing.sha(Path(self.settings['policies']['proxy-only']).read_bytes()))
        routing.atomic(self.state / 'routing.json', record)
        (self.state / 'config-digest').write_text('snapshot')
        proof = dict(record, ok=True, digest='snapshot',
            scope={'requests': ['mixed-tcp', 'fake-ip-tcp', 'real-address-dns']},
            **dict.fromkeys(('data_plane_ok', 'route_ok', 'dns_ok', 'tun_ok', 'config_ok', 'listener_ok', 'real_dns_ok'), True))
        for change in (dict(egress_epoch=0), dict(egress_requested='b' * 64)):
            routing.atomic(self.state / 'health.json', proof | change)
            with self.assertRaises(ValueError):
                routing.applied(self.settings)
        routing.atomic(self.state / 'health.json', proof)
        routing.applied(self.settings)
        self.assertEqual(routing.binding(self.settings)['routing_phase'], 'ready')

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
                             ip_tunnel_cleanup={'ownership': self.state / 'ip-tunnel-owner.json', 'module': 'tunnel'},
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

    def test_direct_switch_rebinds_stale_policy_without_requesting_proxy(self):
        record = routing.binding(self.settings)
        routing.activate(self.settings, 'proxy-only')
        routing.atomic(self.state / 'routing.json', dict(record, routing_requested='proxy-only',
            routing_applied='proxy-only', routing_phase='ready', egress_requested='a' * 64,
            routing_policy_digest=routing.sha(Path(self.settings['policies']['proxy-only']).read_bytes())))
        Path(self.settings['policies']['proxy-only']).write_text('{"proxy-only":true,"changed":true}')
        (self.state / 'intent').write_text('direct')
        routing.initialize(self.settings, 'blocked')
        with self.assertRaises(routing.StalePolicy):
            routing.binding(self.settings)
        routing.initialize(self.settings, 'direct')
        current = routing.binding(self.settings)
        self.assertEqual(current['routing_epoch'], record['routing_epoch'] + 1)
        self.assertEqual(current['routing_requested'], 'rules')
        self.assertIsNone(current['routing_applied'])
        self.assertIsNone(current['egress_requested'])
        self.assertEqual((self.state / 'intent').read_text(), 'direct')
        self.assertFalse((self.state / 'authorized-config').exists())

    def test_blocked_transport_reloads_when_candidate_authority_appears(self):
        record = routing.binding(self.settings)
        routing.activate(self.settings, 'proxy-only')
        routing.atomic(self.state / 'routing.json', dict(record, routing_requested='proxy-only',
            routing_policy_digest=routing.sha(Path(self.settings['policies']['proxy-only']).read_bytes())))
        self.settings.update(topology={'core4': '10.255.253.2', 'core6': 'fd46:6c66:636f::2'},
            interface='clash0', uplinks=['uplink'], guest_interfaces=[], guests=[], dhcp_uid=101, stage_tool='stage')
        calls = []

        def firewall_call(settings, *args, **kwargs):
            if 'input' in kwargs:
                calls.append(kwargs['input'])
            return SimpleNamespace(stdout='{}')

        verify = Mock()
        with patch.object(routing, 'nft', side_effect=firewall_call), \
                patch.object(routing, 'table_view', return_value={}), \
                patch.object(routing, 'module', return_value=SimpleNamespace(verify=verify)):
            routing.enforce(self.settings, 'blocked')
            effective = Path(self.settings['runtime']) / 'effective'
            effective.mkdir(parents=True)
            (effective / 'source-manifest.json').write_text(json.dumps({'policy':
                routing.binding(self.settings)['routing_policy_digest']}))
            (effective / 'clash-verge.yaml').write_text('proxies: [{server: 1.1.1.1, port: 443, type: vless}]')
            (self.state / 'authorized-config').write_text('candidate')
            routing.enforce(self.settings, 'blocked')
            routing.enforce(self.settings, 'blocked')
        self.assertEqual(len(calls), 2)
        self.assertNotIn('elements =', calls[0])
        self.assertIn('1.1.1.1 . tcp . 443', calls[1])
        verify.assert_called_with(effective, self.state / 'config-digest', self.state / 'authorized-config', fast=True)

    def test_corrupt_candidate_cannot_stall_blocked_transport_enforcement(self):
        record = routing.binding(self.settings)
        routing.activate(self.settings, 'proxy-only')
        routing.atomic(self.state / 'routing.json', dict(record, routing_requested='proxy-only',
            routing_policy_digest=routing.sha(Path(self.settings['policies']['proxy-only']).read_bytes())))
        self.settings.update(topology={'core4': '10.255.253.2', 'core6': 'fd46:6c66:636f::2'},
            interface='clash0', uplinks=['uplink'], guest_interfaces=[], guests=[], dhcp_uid=101)
        with patch.object(routing, 'endpoints', side_effect=ValueError('invalid snapshot')):
            blocked = routing.firewall(self.settings, 'blocked')
            self.assertNotIn('elements =', blocked)
            self.assertIn('counter drop', blocked)
            with self.assertRaisesRegex(ValueError, 'invalid snapshot'):
                routing.firewall(self.settings, 'proxy')

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

    def test_new_generation_requires_explicit_rules_and_retires_old_egress(self):
        previous = dict(routing.binding(self.settings), routing_requested='proxy-only',
                        routing_applied='proxy-only', routing_epoch=3, routing_phase='ready',
                        egress_requested='a' * 64, egress_epoch=2,
                        routing_policy_digest=routing.sha(Path(self.settings['policies']['proxy-only']).read_bytes()))
        routing.activate(self.settings, 'proxy-only')
        routing.atomic(self.state / 'routing.json', previous)
        source = self.root / 'source'
        source.mkdir()
        source_settings = self.root / 'source-settings'
        source_settings.write_text(json.dumps({'root': str(source)}))
        next_artifact = self.root / 'updated-strict'
        next_artifact.mkdir()
        next_policy = next_artifact / 'policy.json'
        next_policy.write_text('{"proxy-only": true, "new-generation": true}')
        self.settings.update(source_settings=source_settings, stage_tool='stage', source_tool='source',
                             ip_tunnel=None)
        self.settings['policies']['proxy-only'] = next_policy
        self.settings['artifacts']['proxy-only'] = next_artifact
        (self.state / 'intent').write_text('proxy')
        (self.state / 'health.json').write_text('old')
        (self.state / 'authorize-window').touch()
        self.settings['ip_tunnel_cleanup']['ownership'].touch()

        routing.initialize(self.settings)
        self.assertEqual(routing.read(self.state / 'routing.json'), previous)
        with self.assertRaises(routing.StalePolicy):
            routing.binding(self.settings)
        with self.assertRaises(routing.StalePolicy):
            routing.begin(self.settings, 'proxy-only', 'b' * 64)
        (self.root / 'proxy-only/policy.json').unlink()
        (self.root / 'proxy-only').rmdir()
        routing.initialize(self.settings)

        calls = Mock()
        stage = SimpleNamespace(clear_refresh=Mock())
        broker = SimpleNamespace(selected=lambda root: root, render_local=calls.render)
        tools = {'stage': stage, 'source': broker, 'tunnel': SimpleNamespace(stop=calls.stop)}
        with patch.object(routing, 'module', side_effect=lambda path, _: tools[path]), \
                patch.object(routing, 'enforce', calls.enforce):
            routing.begin(self.settings, 'rules')

        record = routing.binding(self.settings)
        self.assertEqual((record['routing_requested'], record['routing_epoch']), ('rules', 4))
        self.assertEqual((record['egress_requested'], record['egress_epoch']), (None, 3))
        self.assertEqual(record['routing_phase'], 'verifying')
        self.assertFalse((self.state / 'health.json').exists())
        self.assertEqual([call[0] for call in calls.mock_calls], ['enforce', 'stop', 'render'])

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

    def test_interrupted_preparation_removes_partial_authority_and_restores_verifying_phase(self):
        record = dict(routing.binding(self.settings), routing_phase='preparing')
        routing.atomic(self.state / 'routing.json', record)
        routing.activate(self.settings, 'proxy-only')
        (self.state / 'intent').write_text('proxy')
        for name in ('authorized-config', 'config-digest', 'health.json'):
            (self.state / name).write_text('partial')

        routing.recover(self.settings)

        self.assertEqual(routing.binding(self.settings), dict(record, routing_phase='verifying'))
        self.assertEqual((self.state / 'intent').read_text(), 'proxy')
        for name in ('authorized-config', 'config-digest', 'health.json'):
            self.assertFalse((self.state / name).exists())

    def test_recovery_reopens_older_failed_records_with_existing_authority(self):
        record = dict(routing.binding(self.settings), routing_phase='failed')
        routing.atomic(self.state / 'routing.json', record)
        authority = self.state / 'authorized-config'
        authority.write_text('validated')

        routing.recover(self.settings)

        self.assertEqual(routing.binding(self.settings), dict(record, routing_phase='verifying'))
        self.assertEqual(authority.read_text(), 'validated')

    def test_recovery_never_adopts_a_changed_policy(self):
        record = dict(routing.binding(self.settings), routing_phase='failed')
        routing.atomic(self.state / 'routing.json', record)
        Path(self.settings['policies']['rules']).write_text('{}')

        with self.assertRaises(routing.StalePolicy):
            routing.recover(self.settings)

        self.assertEqual(routing.read(self.state / 'routing.json'), record)

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
