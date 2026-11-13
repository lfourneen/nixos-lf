"""Controlled-source attack fixtures; no personal subscriptions or credentials."""
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import time
import unittest

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
            for key, value in (('url', 'http://probe.example/204'), ('interval', True), ('tolerance', -1),
                               ('lazy', 'true'), ('unknown', 1), ('filter', '.*'), ('proxies', 'n')):
                with self.assertRaises((w.Refused, ValueError)):
                    w.render(dict(DOC, **{'proxy-groups': [dict(group, **{key: value})]}), {}, {})
        with self.assertRaises(w.Refused):
            w.render(dict(DOC, **{'proxy-groups': [dict(group, type='load-balance')]}), {}, {})
        with self.assertRaises(w.Refused):
            w.render(dict(DOC, **{'rule-providers': {'remote': {'url': 'https://rules.example/'}}}), {}, {})

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
