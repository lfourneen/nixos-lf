"""Offline subscription audit regressions using in-memory documents."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

source = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else (
    Path(__file__).resolve().parents[3] / 'system/programs/clash/service/subscription_audit.py')
spec = importlib.util.spec_from_file_location('subscription_audit', source)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

NODE = {'name': 'node', 'type': 'ss', 'server': '1.2.3.4', 'port': 8388,
        'cipher': 'aes-128-gcm', 'password': 'x'}
GROUP = {'name': 'Proxy', 'type': 'select', 'proxies': ['node']}


def document(rules=None, nodes=None, groups=None, **extra):
    value = {'proxies': [dict(NODE)] if nodes is None else nodes,
             'proxy-groups': [dict(GROUP)] if groups is None else groups,
             'rules': ['MATCH,Proxy'] if rules is None else rules}
    value.update(extra)
    return value


class AuditTests(unittest.TestCase):
    def check(self, candidate, baseline=None, **settings):
        return audit.audit(candidate, baseline, settings)

    def test_clean_rotation_passes(self):
        result = self.check(document())
        self.assertEqual((result['verdict'], result['deny'], result['warn']), ('pass', {}, {}))

    def test_strict_semantic_changes_and_both_route_directions(self):
        original = document()
        for change, category in [({'server': '8.8.8.8'}, 'node_identity'),
                                 ({'password': 'new-fixture'}, 'node_authentication'),
                                 ({'tls': True}, 'node_transport')]:
            node = dict(NODE, **change)
            self.assertIn(category, self.check(document(nodes=[node]), original, strict_semantics=True)['deny'])
        plain = dict(NODE, type='http', username='fixture', password='dummy')
        self.assertIn('plaintext_credentials', self.check(document(nodes=[plain]), strict_semantics=True)['deny'])
        old = document(rules=['DOMAIN,account.example,Proxy', 'MATCH,Proxy'])
        new = document(rules=['DOMAIN,account.example,DIRECT', 'MATCH,Proxy'])
        self.assertIn('sensitive_route', self.check(new, old, strict_semantics=True, sensitive_domains=['account.example'])['deny'])
        # Pinned IP rotation is safe when the registered logical identity stays.
        before = dict(NODE, server='1.1.1.1', **{'lf-source-server':'proxy.example'})
        after = dict(before, server='8.8.8.8')
        self.assertNotIn('node_identity', self.check(document(nodes=[after]), document(nodes=[before]), strict_semantics=True)['deny'])

    def test_new_node_names_cannot_hide_new_permissions(self):
        original = document()
        alias = document(nodes=[dict(NODE, name='alias')], groups=[dict(GROUP, proxies=['alias'])])
        self.assertEqual(self.check(alias, original, strict_semantics=True)['deny'], {})
        for changes in ({'server': '8.8.8.8'}, {'PASSWORD': 'rotated-fixture'}, {'udp': True}):
            with self.subTest(changes=changes):
                node = dict(NODE, name='alias')
                if 'PASSWORD' in changes:
                    node.pop('password')
                node.update(changes)
                result = self.check(dict(alias, proxies=[node]), original, strict_semantics=True)
                self.assertEqual(result['deny'], {'node_identity': 1, 'node_authentication': 1, 'node_transport': 1})
                self.assertEqual(result['changed_nodes'], 1)
        self.assertEqual(self.check(alias, strict_semantics=True)['deny'], {})

    def test_authentication_and_other_transport_fields_are_compared(self):
        old = document(nodes=[dict(NODE, auth='first', udp=True)])
        new = document(nodes=[dict(NODE, auth='second', udp=False)])
        result = self.check(new, old, strict_semantics=True)
        self.assertEqual(result['deny'], {'node_authentication': 1, 'node_transport': 1})
        self.assertEqual(result['changed_nodes'], 1)

    def test_reality_identity_and_authentication_are_not_ordinary_transport_changes(self):
        original = dict(NODE, type='vless', **{
            'reality-opts': {'public-key': 'first-key', 'short-id': 'aabb'}})
        for options, expected in (
                ({'PUBLIC_KEY': 'next-key', 'short-id': 'aabb'}, 'node_identity'),
                ({'public-key': 'first-key', 'SHORT_ID': 'ccdd'}, 'node_authentication')):
            result = self.check(document(nodes=[dict(original, **{'reality-opts': options})]),
                                document(nodes=[original]), strict_semantics=True)
            self.assertEqual(result['deny'], {expected: 1})

    def test_private_and_reserved_servers_are_denied(self):
        for server in ('192.168.1.10', '127.0.0.1', '169.254.1.1', '100.64.0.1', '::1',
                       '224.0.0.1', 'ff02::1'):
            node = dict(NODE, server=server)
            result = self.check(document(nodes=[node]))
            self.assertEqual(result['verdict'], 'deny', server)
            self.assertEqual(result['deny'].get('private_server'), 1, server)
        allowed = self.check(document(nodes=[dict(NODE, server='192.168.1.10')]),
                             allowed_server_cidrs=['192.168.0.0/16'])
        self.assertEqual(allowed['verdict'], 'pass')

    def test_certificate_bypass_is_refused_independent_of_leaf_name(self):
        for flag in (True, 'yes', 1, None, {'unexpected': True}):
            node = dict(NODE, **{'transport-opts': {'SKIP_CERT_VERIFY': flag}})
            denied = self.check(document(nodes=[node]), insecure_exceptions=['node'])
            self.assertEqual(denied['deny'], {'certificate': 1}, flag)
        for flag in (False, 'false', '0', 0):
            node = dict(NODE, **{'skip-cert-verify': flag})
            self.assertEqual(self.check(document(nodes=[node]))['deny'], {}, flag)

    def test_hosts_override_requires_root_permission_on_every_import(self):
        baseline = document(hosts={'a.example': '1.1.1.1'})
        changed = document(hosts={'a.example': '2.2.2.2'})
        self.assertEqual(self.check(changed, baseline)['deny'].get('hosts_override'), 1)
        first = self.check(changed)
        self.assertEqual(first['verdict'], 'deny')
        self.assertEqual(first['deny'].get('hosts_override'), 1)
        declared = self.check(changed, allowed_hosts={'a.example': '2.2.2.2'})
        self.assertEqual(declared['verdict'], 'pass')
        self.assertEqual(self.check(changed, changed)['deny'].get('hosts_override'), 1,
                         'an imported snapshot cannot authorize its own hosts')
        approved = {'a.example': '2.2.2.2', 'other.example': ['1.1.1.1', '8.8.8.8']}
        self.assertEqual(self.check(changed, baseline, allowed_hosts=approved)['verdict'], 'pass')
        self.assertEqual(self.check(document(), allowed_hosts=approved)['verdict'], 'pass')
        self.assertEqual(self.check(changed, allowed_hosts=None)['verdict'], 'deny')

    def test_provider_changes_and_fixed_trust(self):
        provider = {'p': {'type': 'http', 'url': 'https://example.invalid/list.yaml'}}
        baseline = document(**{'proxy-providers': provider})
        changed = document(**{'proxy-providers': {'p': dict(provider['p'], interval=60)}})
        self.assertEqual(self.check(changed, baseline)['deny'].get('provider_change'), 1)
        fixed = self.check(document(**{'proxy-providers': provider}),
                           subscription_trust='fixed')
        self.assertEqual(fixed['deny'].get('provider_change'), 1)
        source = self.check(document(**{'proxy-providers': provider}),
                            subscription_trust='source')
        self.assertEqual(source['verdict'], 'pass')

    def test_sensitive_route_transition_blocks(self):
        baseline = document(rules=['DOMAIN,sensitive.example,DIRECT', 'MATCH,Proxy'])
        candidate = document(rules=['DOMAIN,sensitive.example,Proxy', 'MATCH,Proxy'])
        result = self.check(candidate, baseline, sensitive_domains=['sensitive.example'])
        self.assertEqual(result['verdict'], 'deny')
        self.assertEqual(result['deny'].get('sensitive_route'), 1)

    def test_rule_volume_and_group_references(self):
        baseline = document(rules=['MATCH,Proxy'])
        candidate = document(rules=['DOMAIN,x%d.example,DIRECT' % index for index in range(8)]
                             + ['MATCH,Proxy'])
        self.assertEqual(self.check(candidate, baseline)['warn'].get('rule_volume'), 1)
        broken = document(groups=[dict(GROUP, proxies=['missing'])])
        self.assertEqual(self.check(broken)['deny'].get('group_reference'), 1)

    def test_unprovable_routes_are_unknown(self):
        baseline = document(rules=['GEOSITE,cn,DIRECT', 'MATCH,Proxy'])
        candidate = document(rules=['GEOSITE,cn,Proxy', 'MATCH,Proxy'])
        result = self.check(candidate, baseline, sensitive_domains=['sensitive.example'])
        self.assertEqual(result['verdict'], 'unknown')
        self.assertEqual(result['unknown'].get('route_unknown'), 1)

    def test_malformed_yaml_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'duplicate.yaml'
            path.write_text('proxies: []\nproxies: []\n')
            with self.assertRaises(audit.InvalidYaml):
                audit.load(path)
            path.write_text('1: value\n')
            with self.assertRaises(audit.InvalidYaml):
                audit.load(path)
            path.write_text('proxies: []\n')
            self.assertEqual(audit.load(path), {'proxies': []})

    def test_settings_are_not_echoed(self):
        result = self.check(document(nodes=[dict(NODE, server='10.0.0.1')]))
        text = json.dumps(result)
        self.assertNotIn('10.0.0.1', text)
        self.assertNotIn('password', text)

    def test_keyword_override_below_sensitive_suffix_is_checked(self):
        baseline = document(rules=['DOMAIN-SUFFIX,sensitive.example,DIRECT', 'MATCH,Proxy'])
        candidate = document(rules=['DOMAIN-KEYWORD,payments,Proxy', *baseline['rules']])
        result = self.check(candidate, baseline, sensitive_domains=['SENSITIVE.example'])
        self.assertEqual(result['deny'].get('sensitive_route'), 1)

    def test_declared_probe_route_is_independent_from_sensitive_delta(self):
        candidate = document(rules=['DOMAIN,probe.example,DIRECT', 'MATCH,Proxy'])
        result = self.check(candidate, protected_routes={'probe.example': 'proxy'})
        self.assertEqual(result['deny'].get('protected_route'), 1)

    def test_protected_routes_check_first_import_and_changed_rule_order(self):
        settings = {'strict_semantics': True, 'protected_routes': {
            'proxy.example': 'proxy', 'local.example': 'direct', 'blocked.example': 'reject'}}
        baseline = document(rules=['DOMAIN,local.example,DIRECT', 'DOMAIN,blocked.example,REJECT', 'MATCH,Proxy'])
        self.assertEqual(self.check(baseline, **settings)['deny'], {})
        shifted = document(rules=['MATCH,Proxy', *baseline['rules']])
        for approved in (None, baseline):
            self.assertEqual(self.check(shifted, approved, **settings)['deny'], {'protected_route': 2})
        unknown = document(rules=baseline['rules'][:-1])
        self.assertEqual(self.check(unknown, baseline, **settings)['unknown'], {'route_unknown': 1})

    def test_protected_route_follows_renamed_groups_and_checks_every_leaf(self):
        settings = {'protected_routes': {'proxy.example': 'proxy'}, 'strict_semantics': True}
        groups = [dict(GROUP, name='Renamed'), {'name': 'Edge', 'type': 'select', 'proxies': ['Renamed']}]
        rules = ['MATCH,Edge']
        candidate = document(rules=rules, groups=groups)
        self.assertEqual(self.check(candidate, document(), **settings)['verdict'], 'pass')
        groups[0]['proxies'] = ['DIRECT', 'node']
        self.assertEqual(self.check(candidate, document(), **settings)['unknown'], {'route_unknown': 1})
        missing_kind = dict(NODE)
        missing_kind.pop('type')
        self.assertEqual(self.check(document(nodes=[missing_kind]), **settings)['unknown'], {'route_unknown': 1})
        rematch = dict(NODE, type='rematch')
        self.assertEqual(self.check(document(nodes=[rematch]), **settings)['unknown'], {'route_unknown': 1})

    def test_added_probe_requires_proof_only_from_candidate(self):
        baseline = document(rules=['GEOIP,CN,DIRECT', 'MATCH,Proxy'])
        candidate = document(rules=['DOMAIN,probe.example,Proxy', *baseline['rules']])
        self.assertEqual(audit.route(baseline, 'probe.example'), 'unknown')
        self.assertEqual(audit.route(candidate, 'probe.example'), 'proxy')
        settings = {'protected_routes': {'probe.example': 'proxy'}}
        result = self.check(candidate, baseline, **settings)
        self.assertEqual((result['deny'], result['unknown']), ({}, {}))
        self.assertEqual(self.check(baseline, candidate, **settings)['unknown'], {'route_unknown': 1})
        self.assertEqual(self.check(candidate, baseline, sensitive_domains=['example'], **settings)['unknown'],
                         {'route_unknown': 2})

    def test_exact_name_shadow_does_not_prove_the_whole_suffix_safe(self):
        baseline = document(rules=['DOMAIN-SUFFIX,sensitive.example,DIRECT', 'MATCH,Proxy'])
        candidate = document(rules=['DOMAIN,payments.sensitive.example,DIRECT',
                                    'DOMAIN-KEYWORD,payments,Proxy', *baseline['rules']])
        result = self.check(candidate, baseline, sensitive_domains=['sensitive.example'])
        self.assertIn(result['verdict'], ('unknown', 'deny'))
        self.assertEqual(result['unknown'].get('route_unknown'), 1)


if __name__ == '__main__':
    unittest.main()
