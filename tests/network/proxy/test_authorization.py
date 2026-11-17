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


unittest.main()
