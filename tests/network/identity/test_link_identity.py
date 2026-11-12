"""Identity migration atomicity, scope, persistence and rollback boundaries."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

script = Path(sys.argv.pop(1))
spec = importlib.util.spec_from_file_location('link_identity', script)
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)
UUID = '11111111-1111-4111-8111-111111111111'


class NM:
    def __init__(self):
        self.current = {'active': False, 'autoconnect': 'no',
                        'properties': dict.fromkeys(identity.PROPERTIES, '')}
        self.modifications = []
        self.fail = False

    def inspect(self, uuid):
        return copy.deepcopy(self.current)

    def modify(self, uuid, values):
        if self.fail:
            raise RuntimeError('rejected update')
        self.modifications.append(copy.deepcopy(values))
        self.current['properties'] = copy.deepcopy(values)


def settings(directory):
    return {'interface': 'wlo1', 'nmcli': '/unused/nmcli', 'stateDirectory': str(directory / 'state'),
            'wifiDefault': 'stable-ssid', 'wired': 'permanent',
            'profiles': {UUID: {'mode': 'stable', 'address': None, 'epoch': '1'}}}


class IdentityTests(unittest.TestCase):
    def test_network_epoch_and_address_changes_cover_mac_dhcp_and_ipv6(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy = settings(Path(temporary))
            identity.validate(policy)
            original = identity.desired(UUID, policy['profiles'][UUID])
            rotated = identity.desired(UUID, dict(policy['profiles'][UUID], epoch='2'))
            self.assertNotEqual(original['connection.stable-id'], rotated['connection.stable-id'])
            for field in ('ipv4.dhcp-client-id', 'ipv6.dhcp-duid', 'ipv6.addr-gen-mode'):
                self.assertTrue(original[field].startswith('stable'))
            self.assertIn('${RANDOM}', identity.desired(UUID, dict(policy['profiles'][UUID], mode='random'))['connection.stable-id'])

    def test_bad_uuid_multicast_and_ambiguous_fixed_policy_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            for address in ('01:00:5e:00:00:01', 'ff:ff:ff:ff:ff:ff', '00:00:00:00:00:00'):
                policy = settings(Path(temporary))
                policy['profiles'][UUID].update(mode='fixed', address=address)
                with self.assertRaises(ValueError):
                    identity.validate(policy)
            policy = settings(Path(temporary))
            policy['profiles']['typo'] = policy['profiles'].pop(UUID)
            with self.assertRaises(ValueError):
                identity.validate(policy)

    def test_active_or_autoconnect_profile_cannot_be_modified(self):
        for field, value in [('active', True), ('autoconnect', 'yes')]:
            with tempfile.TemporaryDirectory() as temporary:
                nm = NM()
                nm.current[field] = value
                with self.assertRaises(ValueError):
                    identity.change(settings(Path(temporary)), UUID, nm=nm)
                self.assertEqual(nm.modifications, [])

    def test_single_update_has_durable_private_backup_and_idempotent_reapply(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy, nm = settings(Path(temporary)), NM()
            original = copy.deepcopy(nm.current['properties'])
            self.assertEqual(identity.change(policy, UUID, nm=nm)['status'], 'applied')
            path = Path(policy['stateDirectory']) / (UUID + '.json')
            record = path.read_bytes()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(record)['before'], original)
            self.assertEqual(identity.change(policy, UUID, nm=nm)['status'], 'unchanged')
            self.assertEqual(path.read_bytes(), record)
            self.assertEqual(len(nm.modifications), 1)
            self.assertEqual(set(nm.modifications[0]), set(identity.PROPERTIES))
            self.assertFalse(any('dns' in key or 'password' in key or 'psk' in key for key in nm.modifications[0]))
            # Restoring a persisted profile also works after removing its Nix target.
            policy['profiles'] = {}
            self.assertEqual(identity.change(policy, UUID, restore=True, nm=nm)['status'], 'restored')
            self.assertEqual(nm.current['properties'], original)

    def test_restore_refuses_manual_edits_or_symlink_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy, nm = settings(Path(temporary)), NM()
            identity.change(policy, UUID, nm=nm)
            nm.current['properties']['ipv4.dhcp-client-id'] = 'mac'
            with self.assertRaises(ValueError):
                identity.change(policy, UUID, restore=True, nm=nm)
            self.assertEqual(len(nm.modifications), 1)
            path = Path(policy['stateDirectory']) / (UUID + '.json')
            path.unlink()
            path.symlink_to(Path(temporary) / 'outside')
            with self.assertRaises(OSError):
                identity.change(policy, UUID, restore=True, nm=nm)

    def test_rejected_nm_transaction_keeps_current_identity_and_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            policy, nm = settings(Path(temporary)), NM()
            before = copy.deepcopy(nm.current)
            nm.fail = True
            with self.assertRaises(RuntimeError):
                identity.change(policy, UUID, nm=nm)
            self.assertEqual(nm.current, before)
            path = Path(policy['stateDirectory']) / (UUID + '.json')
            self.assertEqual(json.loads(path.read_text())['before'], before['properties'])

    def test_plan_only_queries_selected_nonsecret_fields(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            value = ''
            if '-g' in args:
                key = args[args.index('-g') + 1]
                value = {'connection.type': '802-11-wireless', '802-11-wireless.mode': 'infrastructure',
                         'connection.autoconnect': 'no'}.get(key, '')
            return subprocess.CompletedProcess(args, 0, value + '\n', '')
        with tempfile.TemporaryDirectory() as temporary:
            nm = identity.NetworkManager(settings(Path(temporary)), run=run)
            nm.inspect(UUID)
        self.assertFalse(any('modify' in args or '--show-secrets' in args for args in calls))
        allowed = set(identity.PROPERTIES) | {'UUID', 'connection.type', 'connection.interface-name',
                                              '802-11-wireless.mode', 'connection.autoconnect'}
        self.assertTrue(all(args[args.index('-g') + 1] in allowed for args in calls))


unittest.main(verbosity=2)
