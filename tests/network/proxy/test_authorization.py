"""Authorization replay, generation, suspend and ambiguous-commit regressions."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('authorization', sys.argv.pop(1))
authorization = importlib.util.module_from_spec(spec)
spec.loader.exec_module(authorization)


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = self.root / 'proxy-mode'
        self.state.mkdir()
        (self.root / 'dns-policy').mkdir()
        self.settings = {'nft': 'nft', 'seconds': 60}
        self.proof = dict(ok=True, boot_id='boot', digest='digest', policy_version=7,
                          monotonic_seconds=100., proof_id='one', authorization_epoch='0')
        self.write('health.json', self.proof)
        self.write('state.json', dict(mode='proxy', policy_version=8))
        for name, value in [('boot-id', 'boot'), ('config-digest', 'digest'), ('intent', 'proxy')]:
            (self.state / name).write_text(value + '\n')
        self.transactions = []

    def write(self, name, value):
        (self.state / name).write_text(json.dumps(value))

    def nft(self, argv, **kwargs):
        self.transactions.append(kwargs['input'])
        return subprocess.CompletedProcess(argv, 0)

    def grant(self, now=110, run=None):
        authorization.grant(self.settings, 7, self.root, now=now, run=run or self.nft)

    def test_once_only_remaining_lifetime_and_fresh_recovery(self):
        self.grant()
        self.assertIn('timeout 50000ms', self.transactions[0])
        self.assertEqual(json.loads((self.state / 'authorization.json').read_text())['until'], 160)
        with self.assertRaises(ValueError):
            self.grant(now=115)
        self.assertEqual(len(self.transactions), 1)
        self.write('health.json', dict(self.proof, proof_id='two', monotonic_seconds=120))
        self.grant(now=125)
        self.assertEqual(len(self.transactions), 2)

    def test_stale_failed_cross_boot_generation_digest_and_future_proofs(self):
        invalid = [dict(ok=False), dict(ok=False, data_plane_ok=True, dns_ok=False, tun_ok=False),
                   dict(ok=False, data_plane_ok=True, dns_ok=True, tun_ok=False),
                   dict(boot_id='old'), dict(digest='old'), dict(policy_version=6),
                   dict(monotonic_seconds=0), dict(monotonic_seconds=120), dict(monotonic_seconds=float('nan')),
                   dict(proof_id=''), dict(authorization_epoch='old')]
        for change in invalid:
            with self.subTest(change=change):
                self.write('health.json', dict(self.proof, **change))
                with self.assertRaises(ValueError):
                    self.grant()
        self.assertFalse(self.transactions)

    def test_operator_changes_sleep_and_resume_require_new_proof(self):
        for policy in [dict(mode='blocked', policy_version=8), dict(mode='proxy', policy_version=9)]:
            self.write('state.json', policy)
            with self.assertRaises(ValueError): self.grant()
        self.write('state.json', dict(mode='proxy', policy_version=8))
        (self.root / 'dns-policy/sleeping').touch()
        with self.assertRaises(ValueError): self.grant()
        (self.root / 'dns-policy/sleeping').unlink()
        (self.state / 'authorization-epoch').write_text('1\n')
        with self.assertRaises(ValueError): self.grant()
        self.write('health.json', dict(self.proof, authorization_epoch='1'))
        self.grant()

    def test_ambiguous_kernel_apply_consumes_proof_without_claiming_access(self):
        def failed(argv, **kwargs):
            raise subprocess.CalledProcessError(1, argv)
        with self.assertRaises(subprocess.CalledProcessError): self.grant(run=failed)
        self.assertEqual(json.loads((self.state / 'authorization.json').read_text())['until'], 0)
        with self.assertRaises(ValueError): self.grant()
        self.assertFalse(self.transactions)

    def test_renewal_keeps_only_the_last_committed_deadline_until_nft_returns(self):
        self.grant()
        public = self.state / 'authorization.json'
        old = public.read_bytes()
        self.write('health.json', dict(self.proof, proof_id='two', monotonic_seconds=120))

        def interrupted(*args, **kwargs):
            self.assertEqual(public.read_bytes(), old)
            raise subprocess.TimeoutExpired('nft', 5)

        with self.assertRaises(subprocess.TimeoutExpired):
            self.grant(now=125, run=interrupted)
        self.assertEqual(public.read_bytes(), old)
        with self.assertRaises(ValueError):
            self.grant(now=125)
        self.write('health.json', dict(self.proof, proof_id='three', monotonic_seconds=130))
        def commit(*args, **kwargs):
            self.assertEqual(public.read_bytes(), old)
            return self.nft(*args, **kwargs)
        self.grant(now=135, run=commit)
        self.assertEqual(json.loads(public.read_text())['until'], 190)
        self.write('health.json', self.proof)
        with self.assertRaises(ValueError):
            self.grant(now=135)

    def test_legacy_or_rebound_lease_is_never_carried_across_commit(self):
        public = self.state / 'authorization.json'
        self.write(public, dict(boot_id='boot', proof_id='one', policy_version=8, until=160))
        with self.assertRaises(ValueError):
            self.grant()
        def pending(*args, **kwargs):
            self.assertEqual(json.loads(public.read_text())['until'], 0)
            return self.nft(*args, **kwargs)
        self.write('health.json', dict(self.proof, proof_id='two', monotonic_seconds=115))
        self.grant(now=120, run=pending)
        (self.state / 'authorization-epoch').write_text('1')
        with self.assertRaises(ValueError):
            self.grant(now=120)
        self.write('health.json', dict(self.proof, proof_id='three', authorization_epoch='1', monotonic_seconds=125))
        self.grant(now=130, run=pending)

    def test_desktop_fallback_is_published_only_by_a_successful_commit(self):
        self.grant()
        public = self.state / 'authorization.json'
        fallback = 'e' * 64
        self.write('health.json', dict(self.proof, proof_id='two', monotonic_seconds=115, delay_fallback=fallback))
        def pending(*args, **kwargs):
            self.assertNotIn('delay_fallback', json.loads(public.read_text()))
            return self.nft(*args, **kwargs)
        self.grant(now=120, run=pending)
        lease = json.loads(public.read_text())
        self.assertEqual((lease['delay_fallback'], lease['monotonic_seconds']), (fallback, 115))
        self.write('health.json', dict(self.proof, proof_id='three', monotonic_seconds=125))
        self.grant(now=130)
        self.assertNotIn('delay_fallback', json.loads(public.read_text()))

    def test_routing_epoch_and_policy_digest_cannot_reuse_an_old_full_proof(self):
        binding = dict(routing_requested='proxy-only', routing_applied='proxy-only',
                       routing_phase='ready', routing_epoch=4, routing_policy_digest='a' * 64,
                       egress_requested='c' * 64, egress_epoch=2)
        self.write('state.json', dict(mode='proxy', policy_version=8, **binding))
        self.write('routing.json', dict(boot_id='boot', **binding))
        proof = dict(self.proof, **binding, scope={'requests': ['mixed-tcp', 'fake-ip-tcp', 'real-address-dns']},
                     **dict.fromkeys(('data_plane_ok', 'route_ok', 'dns_ok', 'tun_ok', 'config_ok', 'listener_ok', 'real_dns_ok'), True))
        self.write('health.json', proof)
        for change in (dict(routing_epoch=3), dict(routing_policy_digest='b' * 64),
                       dict(egress_requested='d' * 64), dict(egress_epoch=1)):
            self.write('health.json', proof | change)
            with self.assertRaises(ValueError):
                self.grant()
        self.assertFalse(self.transactions)
        self.write('health.json', proof)
        self.grant()
        lease = json.loads((self.state / 'authorization.json').read_text())
        self.assertEqual(lease['routing_epoch'], 4)
        self.assertEqual(lease['routing_policy_digest'], 'a' * 64)

    def test_wireguard_lease_needs_both_echo_paths(self):
        proof = dict(ok=True, scope={'requests': ['mixed-tcp', 'fake-ip-tcp', 'real-address-dns',
                                                  'icmp4-echo', 'icmp6-echo']},
                     **dict.fromkeys(('data_plane_ok', 'route_ok', 'dns_ok', 'tun_ok', 'config_ok',
                                      'listener_ok', 'real_dns_ok', 'ip_tunnel_ok'), True))
        for family in ('icmp4-echo', 'icmp6-echo'):
            without_echo = dict(proof, scope={'requests': [name for name in proof['scope']['requests'] if name != family]})
            self.assertFalse(authorization.full_routing_proof(without_echo, 'proxy-only', {}))
        self.assertFalse(authorization.full_routing_proof(dict(proof, ip_tunnel_ok=False), 'proxy-only', {}))
        self.assertTrue(authorization.full_routing_proof(proof, 'proxy-only', {}))


unittest.main()
