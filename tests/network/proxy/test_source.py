"""Controlled-source attack fixtures; no personal subscriptions or credentials."""
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import ssl
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('source_worker', sys.argv.pop(1))
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

NODE = {'name': 'n', 'type': 'ss', 'server': 'proxy.example', 'port': 443, 'cipher': 'aes-128-gcm', 'password': 'test-only'}
DOC = {'proxies': [NODE], 'proxy-groups': [{'name': 'P', 'type': 'select', 'proxies': ['n']}], 'rules': ['MATCH,P']}


def public(*args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('1.1.1.1', 443))]


class Response:
    def __init__(self, status=200, body=b'proxies: []', **headers):
        self.status, self.body, self.headers = status, io.BytesIO(body), headers
    def getheader(self, name, default=None):
        return self.headers.get(name, default)
    def read1(self, count):
        return self.body.read(count)


class SourceTests(unittest.TestCase):
    def test_single_egress_flattens_dynamic_groups_and_rejects_lost_identity(self):
        original = json.loads(json.dumps(DOC))
        original['proxies'].append(dict(original['proxies'][0], name='other', server='8.8.8.8'))
        original['proxy-groups'][0].update(type='fallback', proxies=['DIRECT', 'n', 'other'], url='https://example.com/')
        policy = {'lf-private-targets': {'proxy-only': True, 'single-egress': True}}
        saved = {}
        strict = w.load(w.render(original, {}, policy, source=saved))
        self.assertEqual(strict['rules'], ['IN-NAME,lf-strict,lf-remote', 'MATCH,lf-remote'])
        self.assertEqual(strict['proxy-groups'], [{'name': 'lf-remote', 'type': 'select', 'proxies': ['n']}])
        identity = w.node_identity(original['proxies'][1])
        strict = w.load(w.render(saved, {}, policy, egress_id=identity))
        self.assertEqual([node['name'] for node in strict['proxies']], ['other'])
        self.assertEqual(len(saved['proxies']), 2)
        self.assertEqual(saved['proxy-groups'][0]['type'], 'fallback')
        with self.assertRaisesRegex(w.Refused, 'selected egress unavailable'):
            w.render(saved, {}, policy, egress_id='0' * 64)
        changed = json.loads(json.dumps(saved))
        changed['proxies'][1]['password'] = 'rotated'
        with self.assertRaises(w.Refused):
            w.render(changed, {}, policy, egress_id=identity)

    def test_wireguard_egress_requires_the_declared_udp_relay(self):
        other = dict(NODE, server='8.8.4.4')
        relay = {'name': 'relay', 'type': 'socks5', 'server': '1.1.1.1', 'port': 1080, 'udp': True}
        tcp_only = dict(relay, name='tcp-only', udp=False)
        document = dict(DOC, proxies=[other, tcp_only, relay],
                        **{'proxy-groups': [dict(DOC['proxy-groups'][0], proxies=['n', 'tcp-only', 'relay'])]})
        policy = {'lf-private-targets': {'proxy-only': True, 'single-egress': True},
                  'lf-ip-tunnel': {'relayAddress': relay['server'], 'relayPort': relay['port']}}
        candidate = w.load(w.render(document, {}, policy))
        self.assertEqual(candidate['proxies'], [relay])
        self.assertNotIn('lf-ip-tunnel', candidate)
        with self.assertRaisesRegex(w.Refused, 'selected egress unavailable'):
            w.render(document, {}, policy, egress_id=w.node_identity(tcp_only))

    def test_single_egress_refresh_pins_all_source_nodes_before_selecting(self):
        document = json.loads(json.dumps(DOC))
        document['proxies'].append(dict(document['proxies'][0], name='other'))
        document['proxy-groups'][0]['proxies'].append('other')
        request = {'registry': {'subscription': {'url': 'https://source.example/sub'}},
                   'policy': {'lf-private-targets': {'proxy-only': True, 'single-egress': True}}}
        first = w.prepare(request, lambda *_: json.dumps(document).encode(), public)
        source = json.loads(first['source_data'])
        self.assertEqual(len(source['proxies']), 2)
        requested = w.node_identity(source['proxies'][1])
        second = w.prepare(dict(request, egress_id=requested), lambda *_: json.dumps(document).encode(), public)
        self.assertEqual(second['egress_id'], requested)
        self.assertEqual([node['name'] for node in w.load(second['yaml'].encode())['proxies']], ['other'])

    def test_proxy_only_rewrites_direct_and_reject_without_destroying_rule_data(self):
        original = json.loads(json.dumps(DOC))
        original['rules'] = ['DOMAIN,domestic.example,DIRECT', 'DOMAIN,denied.example,REJECT', 'MATCH,P']
        original['proxy-groups'][0]['proxies'] = ['DIRECT', 'n']
        saved = {}
        strict = w.load(w.render(original, {}, {'lf-private-targets': {'proxy-only': True}}, source=saved))
        self.assertEqual(strict['rules'], ['IN-NAME,lf-strict,lf-remote', 'MATCH,P'])
        self.assertEqual(strict['proxy-groups'][0]['proxies'], ['n'])
        self.assertEqual(saved['rules'], original['rules'])
        self.assertEqual(saved['proxy-groups'][0]['proxies'], ['DIRECT', 'n'])
        restored = w.load(w.render(saved, {}, {'lf-private-targets': {'proxy-only': False}}))
        self.assertEqual(restored['rules'][1:], original['rules'])
        self.assertEqual(original['proxy-groups'][0]['proxies'], ['DIRECT', 'n'])

    def test_pinned_canonical_source_retains_original_rules_for_offline_switches(self):
        original = json.loads(json.dumps(DOC))
        original['rules'].insert(0, 'DOMAIN,domestic.example,DIRECT')
        request = {'registry': {'subscription': {'url': 'https://source.example/sub'}},
                   'policy': {'lf-private-targets': {'proxy-only': True}}}
        result = w.prepare(request, download=lambda *_: json.dumps(original).encode(), resolver=public)
        saved = json.loads(result['source_data'])
        self.assertEqual(saved['rules'], original['rules'])
        self.assertEqual(saved['proxies'][0]['server'], '1.1.1.1')
        self.assertEqual(saved['proxies'][0]['lf-source-server'], 'proxy.example')
        self.assertNotIn('lf-remote', [group['name'] for group in saved['proxy-groups']])

    def test_a_validated_family_survives_an_unavailable_other_family(self):
        import dns.resolver
        class Backend:
            def resolve(self, host, kind, **kwargs):
                if kind == 'AAAA':
                    raise dns.resolver.NoNameservers()
                return [SimpleNamespace(address='1.1.1.1')]
        with patch('dns.resolver.Resolver', return_value=Backend()), patch.object(
                socket, 'getaddrinfo', side_effect=AssertionError('unsafe system DNS fallback')):
            lookup = w.real_resolver({'address': '127.0.0.1', 'port': 1056})
            self.assertEqual(lookup('source.example', 443)[0][4], ('1.1.1.1', 443))

    def test_fetch_tries_other_validated_addresses_without_reresolving(self):
        addresses = ['2606:4700:4700::1111', '8.8.8.8', '1.1.1.1']
        resolver = unittest.mock.Mock(return_value=[(socket.AF_INET, 1, 6, '', (ip, 443)) for ip in addresses])
        contacted = []
        class Connection:
            sock = None
            def __init__(self, host, ip, timeout, context):
                self.ip = ip
                contacted.append((host, ip, timeout))
            def request(self, *args, **kwargs):
                if self.ip == '1.1.1.1':
                    raise ConnectionRefusedError('private endpoint must stay out of diagnostics')
            def getresponse(self): return Response()
            def close(self): pass
        data = w.fetch({'url': 'https://source.example/sub'}, time.monotonic()+5, [w.TOTAL], resolver, Connection)
        self.assertTrue(data)
        resolver.assert_called_once()
        self.assertEqual([ip for _,ip,_ in contacted], ['1.1.1.1', '8.8.8.8'])
        self.assertTrue(all(host == 'source.example' and 0 < timeout <= 5 for host,_,timeout in contacted))
        resolver.return_value.append((socket.AF_INET, 1, 6, '', ('169.254.169.254', 443)))
        contacted.clear()
        with self.assertRaises(w.Refused):
            w.fetch({'url': 'https://source.example/sub'}, time.monotonic()+5, [w.TOTAL], resolver, Connection)
        self.assertEqual(contacted, [], 'all addresses must be checked before any connection')

    def test_node_pinning_prefers_ipv4_and_keeps_ipv6_only_nodes(self):
        def resolver(*args, **kwargs):
            return [(socket.AF_INET6, 1, 6, '', ('2606:4700:4700::1111', 443)),
                    (socket.AF_INET, 1, 6, '', ('8.8.8.8', 443))]
        config, _ = w.pin_servers(json.dumps(DOC).encode(), resolver)
        self.assertEqual(w.load(config)['proxies'][0]['server'], '8.8.8.8')
        config, _ = w.pin_servers(json.dumps(DOC).encode(), lambda *a,**k: resolver()[0:1])
        self.assertEqual(w.load(config)['proxies'][0]['server'], '2606:4700:4700::1111')

    def test_failure_diagnostics_use_only_fixed_categories(self):
        for error, reason in ((socket.gaierror('secret-domain'), 'dns lookup failed'),
                (ssl.SSLCertVerificationError('secret-certificate'), 'certificate rejected'),
                (ConnectionRefusedError('secret-address'), 'connection refused'),
                (TimeoutError('secret-url'), 'connection timeout'),
                (w.Refused('http response', 'http rejected', 403), 'http rejected')):
            def fail(): raise error
            with self.assertRaises(w.Refused) as result:
                w.step('fetch', fail)
            self.assertEqual(str(result.exception), 'fetch')
            self.assertEqual(result.exception.reason, reason)
            self.assertNotIn('secret', str(result.exception))

    def test_real_resolver_uses_only_the_fixed_gateway(self):
        import dns.resolver

        answers = {'A': ['1.1.1.1'], 'AAAA': ['2606:4700:4700::1111']}
        calls = []
        class Backend:
            def resolve(self, host, kind, **kwargs):
                calls.append((host, kind, kwargs))
                return [SimpleNamespace(address=value) for value in answers[kind]]
        backend = Backend()
        with patch('dns.resolver.Resolver', return_value=backend) as factory, patch.object(
                socket, 'getaddrinfo', side_effect=AssertionError('system fake-IP resolver used')):
            lookup = w.real_resolver({'address': '127.0.0.1', 'port': 1056})
            result = lookup('source.example', 443, type=socket.SOCK_STREAM)
            self.assertEqual({x[4][0] for x in result}, {'1.1.1.1', '2606:4700:4700::1111'})
            self.assertEqual(lookup('1.1.1.1', 8443)[0][4], ('1.1.1.1', 8443))
        factory.assert_called_once_with(configure=False)
        self.assertEqual((backend.nameservers, backend.port), (['127.0.0.1'], 1056))
        self.assertEqual([x[1] for x in calls], ['A', 'AAAA'])
        self.assertTrue(all(x[2]['search'] is False and 0 < x[2]['lifetime'] <= 4 for x in calls))

        with patch('dns.resolver.Resolver', return_value=backend):
            lookup = w.real_resolver({'address': '127.0.0.1', 'port': 1056})
            for error in (dns.resolver.NXDOMAIN(), dns.resolver.NoNameservers()):
                with patch.object(backend, 'resolve', side_effect=error), patch.object(
                        socket, 'getaddrinfo', side_effect=AssertionError('fell back to system DNS')):
                    with self.assertRaises(socket.gaierror):
                        lookup('source.example', 443)
        for settings in ({'address': '8.8.8.8', 'port': 53}, {'address': '127.0.0.1', 'port': True}):
            with self.assertRaises(w.Refused):
                w.real_resolver(settings)

    def test_prepare_shares_real_resolution_between_fetch_and_nodes(self):
        request = {'registry': {'id': 'fixture', 'subscription': {'url': 'https://source.example/sub'}},
                   'policy': {}}
        hosts = []
        def resolver(host, port, **kwargs):
            hosts.append(host)
            return public(host, port, **kwargs)
        def download(spec, deadline, budget, **kwargs):
            kwargs['resolver']('source.example', 443, type=socket.SOCK_STREAM)
            return json.dumps(DOC).encode()
        with patch.object(w, 'fetch', side_effect=download):
            w.prepare(request, resolver=resolver)
        self.assertEqual(hosts, ['source.example', 'proxy.example'])

    def test_ua_negotiation_on_the_same_endpoint(self):
        class Connection:
            sock = None
            def __init__(self, *args): pass
            def request(self, method, path, headers): self.agent = headers['User-Agent']
            def getresponse(self):
                return Response(body=json.dumps(DOC).encode() if self.agent == 'clash.meta' else b'dHJvamFuOi8vZXhhbXBsZQ==')
            def close(self): pass
        def fetch(spec):
            return w.fetch(spec, time.monotonic() + 5, [w.TOTAL], public, Connection)
        spec = {'url': 'https://source.example/sub'}
        self.assertIn('proxies', w.load(fetch(spec)))
        with self.assertRaises(w.Refused):
            w.load(fetch(dict(spec, **{'user-agent': 'lf-subscription/1'})))
        for agent in ('bad\r\nX: value', 'na\u00efve', '', 'x' * 257):
            with self.assertRaises(w.Refused):
                fetch(dict(spec, **{'user-agent': agent}))

    def test_auto_groups_are_validated_preserved_and_audited(self):
        audit_path = Path(os.environ['SOURCE_AUDIT']) if 'SOURCE_AUDIT' in os.environ else (
            Path(__file__).resolve().parents[3] / 'system/programs/clash/service/subscription_audit.py')
        spec = importlib.util.spec_from_file_location('source_audit', audit_path)
        audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(audit)
        for kind in ('url-test', 'fallback'):
            group = dict(DOC['proxy-groups'][0], type=kind, url='https://probe.example/204',
                         interval=300, tolerance=50, lazy=True, timeout=5000)
            out = w.load(w.render(dict(DOC, **{'proxy-groups': [group]}), {}, {}))
            self.assertEqual(out['proxy-groups'][0], group)
            self.assertEqual(audit.route(out, 'probe.example'), 'proxy')
            plain = dict(group, url='http://probe.example/204')
            self.assertEqual(w.load(w.render(dict(DOC, **{'proxy-groups': [plain]}), {}, {}))['proxy-groups'][0], plain)
            for key, value in (('url', 'http://probe.example:8080/204'),
                               ('url', 'ftp://probe.example/204'),
                               ('interval', True), ('tolerance', -1),
                               ('lazy', 'true'), ('unknown', 1), ('filter', '.*'), ('proxies', 'n')):
                with self.assertRaises((w.Refused, ValueError)):
                    w.render(dict(DOC, **{'proxy-groups': [dict(group, **{key: value})]}), {}, {})
        with self.assertRaises(w.Refused):
            w.render(dict(DOC, **{'proxy-groups': [dict(group, type='load-balance')]}), {}, {})
        with self.assertRaises(w.Refused):
            w.render(dict(DOC, **{'rule-providers': {'remote': {'url': 'https://rules.example/'}}}), {}, {})

    def test_anytls_nodes_pin_with_tls_identity(self):
        node = {'name': 'a', 'type': 'anytls', 'server': 'any.example', 'port': 443, 'password': 'test-only'}
        document = dict(DOC, proxies=[node],
                        **{'proxy-groups': [{'name': 'P', 'type': 'select', 'proxies': ['a']}]})
        pinned, dropped = w.pin_servers(json.dumps(document).encode(), public)
        out = w.load(pinned)
        self.assertEqual(dropped, 0)
        self.assertEqual(out['proxies'][0]['server'], '1.1.1.1')
        self.assertEqual(out['proxies'][0]['lf-source-server'], 'any.example')
        self.assertEqual(out['proxies'][0]['servername'], 'any.example')
        self.assertEqual(out['proxies'][0]['sni'], 'any.example')

    def test_insecure_nodes_are_dropped_unless_explicitly_allowed(self):
        insecure = dict(NODE, name='insecure', **{'skip-cert-verify': True})
        allowed = dict(NODE, name='allowed', **{'skip-cert-verify': True})
        document = dict(DOC, proxies=[NODE, insecure, allowed],
                        **{'proxy-groups': [{'name': 'P', 'type': 'select',
                                             'proxies': ['n', 'insecure', 'allowed']}]})
        pinned, dropped = w.pin_servers(json.dumps(document).encode(), public, ['allowed'], True)
        out = w.load(pinned)
        self.assertEqual(dropped, 1)
        self.assertEqual([node['name'] for node in out['proxies']], ['n', 'allowed'])
        self.assertEqual(out['proxy-groups'][0]['proxies'], ['n', 'allowed'])
        kept, dropped = w.pin_servers(json.dumps(document).encode(), public, ['allowed'], False)
        self.assertEqual(dropped, 0)
        self.assertEqual([node['name'] for node in w.load(kept)['proxies']], ['n', 'insecure', 'allowed'])

    def test_ech_query_nodes_stay_out_of_the_system_leaf(self):
        queried = dict(NODE, name='ech', **{'ech-opts': {'enable': True, 'query-server-name': 'cloudflare-ech.com'}})
        inline = dict(NODE, name='inline', **{'ech-opts': {'enable': True, 'config': 'AEX+'}})
        document = dict(DOC, proxies=[queried, NODE, inline],
                        **{'proxy-groups': [{'name': 'P', 'type': 'select', 'proxies': ['ech', 'n', 'inline']}]})
        out = w.load(w.render(document, {}, {}))
        leaf = next(group for group in out['proxy-groups'] if group['name'] == 'lf-remote')
        self.assertEqual(leaf['proxies'], ['n', 'inline'])
        self.assertEqual([node['name'] for node in out['proxies']], ['ech', 'n', 'inline'])
        saved = {}
        w.render(document, {}, {'lf-private-targets': {'proxy-only': True}}, source=saved)
        self.assertEqual([node['name'] for node in w.egress_nodes(saved)], ['n', 'inline'])
        with self.assertRaises(w.Refused):
            w.render(dict(DOC, proxies=[queried],
                          **{'proxy-groups': [{'name': 'P', 'type': 'select', 'proxies': ['ech']}]}), {}, {})

    def test_prepare_reports_the_failing_pipeline_stage(self):
        request = {'registry': {'id': 'fixture', 'subscription': {'url': 'https://s.example/'}}, 'policy': {}}
        with self.assertRaisesRegex(w.Refused, '^parse$'):
            w.prepare(request, lambda *_: b'base64', public)
        with self.assertRaisesRegex(w.Refused, '^render$'):
            w.prepare(request, lambda *_: b'{"proxy-groups":[],"rules":[]}', public)
        with self.assertRaisesRegex(w.Refused, '^pin-addresses$'):
            w.prepare(request, lambda *_: json.dumps(DOC).encode(), lambda *_a, **_k: [])

    def fetch(self, responses, spec=None, resolver=public):
        observed = []
        class Connection:
            sock = None
            def __init__(self, host, ip, timeout, context):
                observed.append({'host': host, 'ip': ip})
            def request(self, method, path, headers):
                observed[-1].update(path=path, headers=headers)
            def getresponse(self):
                return responses.pop(0)
            def close(self):
                pass
        result = w.fetch(spec or {'url': 'https://source.example/sub'}, time.monotonic() + 5, [w.TOTAL], resolver, Connection)
        return result, observed

    def test_public_pin_and_canonical_hosts(self):
        for address in ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '::ffff:127.0.0.1', '64:ff9b::a00:1', '2002:7f00:1::1']:
            self.assertFalse(w.public_address(address), address)
        for url in ['http://x/sub', 'https://u:p@x/sub', 'https://x:444/sub', 'https://x/sub\n', 'https://x./sub', 'https://x\\y/sub']:
            with self.assertRaises((ValueError, w.Refused)):
                w.endpoint(url)
        _, observed = self.fetch([Response()])
        self.assertEqual(observed[0]['ip'], '1.1.1.1')
        self.assertEqual(observed[0]['host'], 'source.example')

    def test_redirects_need_exact_registration_and_drop_credentials(self):
        with self.assertRaises(w.Refused):
            self.fetch([Response(302, Location='https://other.example/sub')])
        for target in ['http://source.example/sub', 'https://127.0.0.1/sub']:
            with self.assertRaises(w.Refused):
                self.fetch([Response(302, Location=target)])
        spec = {'url': 'https://source.example/sub?token=dummy',
                'user-agent': 'PrivateAgent/1',
                'headers': {'Authorization': 'Bearer fixture', 'Cookie': 'fixture'},
                'redirects': ['https://other.example/sub', 'https://source.example/sub?token=dummy']}
        _, requests = self.fetch([Response(302, Location='https://other.example/sub'),
                                  Response(302, Location=spec['url']), Response()], spec)
        self.assertIn('Authorization', requests[0]['headers'])
        self.assertNotIn('Authorization', requests[1]['headers'])
        self.assertNotIn('Cookie', requests[2]['headers'])
        self.assertEqual(requests[0]['headers']['User-Agent'], 'PrivateAgent/1')
        self.assertEqual(requests[1]['headers']['User-Agent'], 'clash.meta')
        self.assertEqual(requests[2]['headers']['User-Agent'], 'clash.meta')

    def test_rebinding_private_resolution_never_dials(self):
        def private(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]
        with self.assertRaises(w.Refused):
            self.fetch([Response()], resolver=private)

    def test_limits_empty_compression_duplicate_and_alias(self):
        for response in [Response(body=b''), Response(**{'Content-Length': str(w.LIMIT + 1)}), Response(**{'Content-Encoding': 'gzip'})]:
            with self.assertRaises(w.Refused):
                self.fetch([response])
        for data in [b'a: 1\na: 2', b'a: &a [*a]', b'a: ' + b'[' * 55 + b'0' + b']' * 55]:
            with self.assertRaises((w.Refused, ValueError)):
                w.load(data)

    def test_renderer_ignores_gui_and_subscription_control_fields(self):
        doc = dict(DOC, **{'mixed-port': 9999, 'listeners': [{'type': 'mixed'}], 'external-controller': ':9999'})
        out = w.load(w.render(doc, {}, {'mixed-port': 7897, 'listeners': []}, ['health.example']))
        self.assertEqual(out['mixed-port'], 7897)
        self.assertEqual(out['listeners'], [])
        self.assertNotIn('external-controller', out)
        self.assertEqual(out['rules'][0], 'IN-NAME,lf-strict,lf-remote')
        self.assertEqual(out['proxy-groups'][-1]['proxies'], ['n'])

    def test_probe_domains_use_the_catch_all_group(self):
        out = w.load(w.render(dict(DOC, rules=['MATCH,P']), {}, {}, [], ['ipinfo.io', 'ipip.net']))
        self.assertEqual(out['rules'][1], 'DOMAIN-SUFFIX,ipinfo.io,P')
        self.assertEqual(out['rules'][2], 'DOMAIN-SUFFIX,ipip.net,P')
        self.assertEqual(out['rules'][-1], 'MATCH,P')
        direct = w.load(w.render(dict(DOC, rules=['MATCH,DIRECT']), {}, {}, [], ['ipinfo.io']))
        self.assertFalse([rule for rule in direct['rules'] if 'ipinfo.io' in rule])

    def test_provider_payloads_are_inlined_not_cached_or_fetched_by_core(self):
        doc = {'proxy-providers': {'p': {'type': 'http', 'url': 'https://p.example/list'}},
               'proxy-groups': [{'name': 'P', 'type': 'select', 'use': ['p']}], 'rules': ['MATCH,P']}
        out = w.load(w.render(doc, {'p': json.dumps({'proxies': [NODE]}).encode()}, {}))
        self.assertNotIn('proxy-providers', out)
        self.assertNotIn('use', out['proxy-groups'][0])
        self.assertEqual(out['proxy-groups'][0]['proxies'], ['n'])
        with self.assertRaises(w.Refused):
            w.render(doc, {}, {})

    def test_registration_binds_provider_endpoint_and_origin_digest(self):
        request = {'registry': {'id': 'fixture', 'subscription': {'url': 'https://s.example/sub'}, 'providers': {}}, 'policy': {}}
        result = w.prepare(request, lambda *_: json.dumps(DOC).encode(), public)
        node = w.load(result['yaml'])['proxies'][0]
        self.assertEqual(node['server'], '1.1.1.1')
        self.assertEqual(node['lf-source-server'], 'proxy.example')
        self.assertEqual(len(result['responses']['subscription']), 64)
        doc = dict(DOC, **{'proxy-providers': {'p': {'type': 'http', 'url': 'https://evil.example/sub'}}})
        request['registry']['providers'] = {'p': {'url': 'https://approved.example/sub'}}
        with self.assertRaises(w.Refused):
            w.prepare(request, lambda *_: json.dumps(doc).encode(), public)

    def test_unresolvable_nodes_are_dropped_and_counted(self):
        dead = dict(NODE, name='dead', server='dead.example')
        doc = {'proxies': [NODE, dead],
               'proxy-groups': [{'name': 'P', 'type': 'select', 'proxies': ['n', 'dead']}],
               'rules': ['MATCH,P']}

        def resolver(host, port, **kwargs):
            if host == 'dead.example':
                raise socket.gaierror(-2, 'Name or service not known')
            return public()

        request = {'registry': {'id': 'fixture', 'subscription': {'url': 'https://s.example/sub'}, 'providers': {}}, 'policy': {}}
        result = w.prepare(request, lambda *_: json.dumps(doc).encode(), resolver)
        out = w.load(result['yaml'])
        self.assertEqual([node['name'] for node in out['proxies']], ['n'])
        self.assertEqual(out['proxy-groups'][0]['proxies'], ['n'])
        self.assertEqual(out['proxy-groups'][-1]['proxies'], ['n'])
        self.assertEqual(result['dropped'], 1)

    def test_node_resolution_loss_is_bounded(self):
        nodes = [dict(NODE, name=f'n{i}', server=f'n{i}.example') for i in range(6)]
        dead = {f'n{i}.example' for i in (0, 1)}

        def resolver(host, port, **kwargs):
            if host in dead:
                raise socket.gaierror(-2, 'Name or service not known')
            return public()

        doc = {'proxies': nodes,
               'proxy-groups': [{'name': 'P', 'type': 'select', 'proxies': [n['name'] for n in nodes]}],
               'rules': ['MATCH,P']}
        request = {'registry': {'id': 'fixture', 'subscription': {'url': 'https://s.example/sub'}, 'providers': {}}, 'policy': {}}
        with self.assertRaises(w.Refused):
            w.prepare(request, lambda *_: json.dumps(doc).encode(), resolver)

    def test_group_emptied_by_a_drop_is_never_rerouted(self):
        dead = dict(NODE, name='dead', server='dead.example')
        doc = {'proxies': [NODE, dead],
               'proxy-groups': [{'name': 'P', 'type': 'select', 'proxies': ['n']},
                                {'name': 'Q', 'type': 'select', 'proxies': ['dead']}],
               'rules': ['MATCH,Q']}

        def resolver(host, port, **kwargs):
            if host == 'dead.example':
                raise socket.gaierror(-2, 'Name or service not known')
            return public()

        request = {'registry': {'id': 'fixture', 'subscription': {'url': 'https://s.example/sub'}, 'providers': {}}, 'policy': {}}
        with self.assertRaises(w.Refused):
            w.prepare(request, lambda *_: json.dumps(doc).encode(), resolver)

    def test_private_resolution_still_refuses_the_candidate(self):
        def private(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))]
        with self.assertRaises(w.Refused):
            w.pin_servers(json.dumps(DOC).encode(), private)


if __name__ == '__main__':
    unittest.main()
