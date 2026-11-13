"""Bounded subscription downloader and deterministic renderer; runs without root.

Input is supplied only by the root broker on stdin. Never import desktop scripts,
proxy environment, GUI indexes or cached provider files. Errors contain no URLs.
"""
import hashlib
import http.client
import ipaddress
import json
import re
import resource
import socket
import ssl
import sys
import time
from urllib.parse import urljoin, urlsplit

import yaml

LIMIT = 8 * 1024 * 1024
TOTAL = 32 * 1024 * 1024


class Refused(ValueError):
    pass


class Loader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise Refused('yaml alias')
        self.depth = getattr(self, 'depth', 0) + 1
        if self.depth > 48:
            raise Refused('yaml depth')
        try:
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1

    def construct_mapping(self, node, deep=False):
        result = {}
        for key, value in node.value:
            key = self.construct_object(key, deep=deep)
            if not isinstance(key, str) or key in result:
                raise Refused('yaml key')
            result[key] = self.construct_object(value, deep=deep)
        return result


def load(data):
    if not data or len(data) > LIMIT:
        raise Refused('response size')
    value = yaml.load(data, Loader=Loader)
    if not isinstance(value, dict):
        raise Refused('yaml document')
    # Reject YAML-specific scalar types, NaN and non-string mapping keys.
    return json.loads(json.dumps(value, allow_nan=False))


def endpoint(url):
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) >= 127 for c in url):
        raise Refused('url encoding')
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment:
        raise Refused('url scheme')
    if p.port not in (None, 443):
        raise Refused('url port')
    if '%' in p.netloc or '\\' in url:
        raise Refused('url authority')
    host = p.hostname.lower().rstrip('.')
    if host != p.hostname or '..' in host:
        raise Refused('url host')
    return host, p.port or 443, p.path or '/', p.query


class Connection(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout, context):
        super().__init__(host, 443, timeout=timeout, context=context)
        self.address = address

    def connect(self):
        # Resolve exactly once; TLS still authenticates the registered DNS name.
        raw = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def public_address(value):
    ip = ipaddress.ip_address(value.split('%')[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    # Fail closed on translation/tunnelling prefixes, including embedded IPv4.
    return ip.is_global and not ip.is_multicast and not any(ip in net for net in (
        ipaddress.ip_network('64:ff9b::/96'), ipaddress.ip_network('64:ff9b:1::/48'),
        ipaddress.ip_network('2002::/16'), ipaddress.ip_network('2001::/32'))
        if ip.version == net.version)


def fetch(spec, deadline, budget, resolver=socket.getaddrinfo, connection=Connection):
    url = spec['url']
    approved = {spec['url'], *spec.get('redirects', [])}
    agent = spec.get('user-agent', 'clash.meta')
    if not isinstance(agent, str) or not 1 <= len(agent) <= 256 or any(not 32 <= ord(c) < 127 for c in agent):
        raise Refused('user agent')
    headers = {'Accept-Encoding': 'identity', 'User-Agent': agent}
    extra = spec.get('headers', {})
    if not isinstance(extra, dict) or any(k.lower() not in ('authorization', 'cookie') or
            not isinstance(v, str) or any(ord(c) < 32 or ord(c) == 127 for c in v)
            for k, v in extra.items()):
        raise Refused('headers')
    credentials_origin = endpoint(url)[:2]
    context = ssl.create_default_context()
    for _ in range(5):
        host, port, path, query = endpoint(url)
        addresses = sorted({item[4][0] for item in resolver(host, port, type=socket.SOCK_STREAM)})
        if not addresses or not all(public_address(ip) for ip in addresses):
            raise Refused('destination')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Refused('deadline')
        conn = connection(host, addresses[0], min(remaining, 10), context)
        try:
            request_headers = dict(headers)
            if (host, port) == credentials_origin:
                request_headers.update(extra)
            conn.request('GET', path + ('?' + query if query else ''), headers=request_headers)
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                target = urljoin(url, response.getheader('Location', ''))
                endpoint(target)
                if target not in approved:
                    raise Refused('redirect')
                # Never restore credentials after leaving the original origin.
                if endpoint(target)[:2] != credentials_origin:
                    extra = {}
                    headers['User-Agent'] = 'clash.meta'
                url = target
                continue
            if response.status != 200 or response.getheader('Content-Encoding', 'identity') != 'identity':
                raise Refused('http response')
            size = response.getheader('Content-Length')
            if size is not None and (not size.isdecimal() or int(size) > min(LIMIT, budget[0])):
                raise Refused('response size')
            chunks, count = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Refused('deadline')
                if conn.sock is not None:
                    conn.sock.settimeout(min(remaining, 5))
                chunk = response.read1(min(65536, LIMIT - count + 1))
                if not chunk:
                    break
                count += len(chunk)
                budget[0] -= len(chunk)
                if count > LIMIT or budget[0] < 0:
                    raise Refused('response size')
                chunks.append(chunk)
            data = b''.join(chunks)
            if not data:
                raise Refused('empty response')
            return data
        finally:
            conn.close()
    raise Refused('redirect count')


def render(document, payloads, policy, forced_domains=(), probe_domains=(), remote_only_catchall=False):
    document = json.loads(json.dumps(document, allow_nan=False))
    # Only subscription routing data survives. Managed listeners/DNS/TUN come
    # exclusively from the immutable Nix policy, never from GUI merge scripts.
    permitted = {'proxies', 'proxy-groups', 'rules', 'hosts', 'proxy-providers'}
    if document.get('rule-providers'):
        raise Refused('unsupported rule provider')
    result = {k: v for k, v in document.items() if k in permitted}
    providers = result.pop('proxy-providers', {}) or {}
    if not isinstance(providers, dict) or set(providers) != set(payloads):
        raise Refused('provider registration')
    nodes = result.setdefault('proxies', [])
    groups = result.setdefault('proxy-groups', [])
    if not isinstance(nodes, list) or not isinstance(groups, list):
        raise Refused('node structure')
    names = {n['name'] for n in nodes}
    expanded = {}
    for name, spec in providers.items():
        if spec.get('type') != 'http' or spec.get('override') or spec.get('filter') or spec.get('exclude-filter'):
            raise Refused('unsupported provider')
        entries = load(payloads[name]).get('proxies')
        if not isinstance(entries, list) or not entries:
            raise Refused('provider payload')
        expanded[name] = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get('name'), str) or entry['name'] in names:
                raise Refused('node collision')
            names.add(entry['name'])
            expanded[name].append(entry['name'])
            nodes.append(entry)
    for group in groups:
        if not isinstance(group, dict) or group.get('type') not in ('select', 'url-test', 'fallback'):
            raise Refused('group type')
        common = {'name', 'type', 'proxies', 'use', 'hidden', 'icon', 'udp', 'disable-udp'}
        testing = {'url', 'interval', 'tolerance', 'lazy', 'timeout', 'max-failed-times', 'expected-status'}
        if set(group) - common - testing:
            raise Refused('unsupported group field')
        if not isinstance(group.get('name'), str) or not group['name']:
            raise Refused('group name')
        for key in ('proxies', 'use'):
            if key in group and (not isinstance(group[key], list) or
                                any(not isinstance(v, str) or not v for v in group[key])):
                raise Refused('group references')
        for key in ('hidden', 'udp', 'disable-udp', 'lazy'):
            if key in group and type(group[key]) is not bool:
                raise Refused('group boolean')
        if 'icon' in group and (not isinstance(group['icon'], str) or len(group['icon']) > 2048):
            raise Refused('group icon')
        if 'url' in group:
            endpoint(group['url'])
        elif group['type'] != 'select':
            raise Refused('group test url')
        for key, low, high in (('interval', 1, 86400), ('tolerance', 0, 60000),
                               ('timeout', 100, 60000), ('max-failed-times', 1, 100)):
            if key in group and (type(group[key]) is not int or not low <= group[key] <= high):
                raise Refused('group test bounds')
        if 'expected-status' in group and (not isinstance(group['expected-status'], str) or
                not re.fullmatch(r'[1-5][0-9]{2}(?:-[1-5][0-9]{2})?(?:/[1-5][0-9]{2}(?:-[1-5][0-9]{2})?)*', group['expected-status'])):
            raise Refused('group test status')
        refs = group.setdefault('proxies', [])
        for provider in group.pop('use', []) or []:
            if provider not in expanded:
                raise Refused('provider reference')
            refs.extend(expanded[provider])
    rules = result.get('rules')
    if not isinstance(rules, list) or not rules or not isinstance(rules[-1], str):
        raise Refused('rules')
    catchall = rules[-1].split(',')
    if len(catchall) != 2 or catchall[0].upper() not in ('MATCH', 'FINAL'):
        raise Refused('catchall')
    if remote_only_catchall:
        by_name = {group['name']: group for group in groups}
        cache = {}
        def prune(name, seen):
            if name in names:
                return True
            if name in ('DIRECT', 'COMPATIBLE', 'REJECT', 'REJECT-DROP', 'PASS', 'GLOBAL'):
                return False
            if name in cache:
                return cache[name]
            if name in seen or name not in by_name:
                raise Refused('catchall graph')
            group = by_name[name]
            group['proxies'] = [v for v in group['proxies'] if prune(v, seen | {name})]
            cache[name] = bool(group['proxies'])
            return cache[name]
        if not prune(catchall[1], set()):
            raise Refused('empty catchall')
    # The strict inlet has its own remote-only group; ordinary Rule routing is
    # preserved. Use all explicitly validated remote nodes, never DIRECT.
    if 'lf-remote' in names or any(g.get('name') == 'lf-remote' for g in groups):
        raise Refused('reserved group')
    remote = [n['name'] for n in nodes if n.get('type') not in ('direct', 'compatible', 'reject', 'dns')]
    if not remote:
        raise Refused('empty nodes')
    groups.append({'name': 'lf-remote', 'type': 'select', 'proxies': remote})
    # Split-routing and IP-quality checks must not see a direct connection when
    # the subscription sends GEOIP,CN traffic DIRECT. Prepend the registered
    # probe suffixes with the same group the subscription's catch-all selects;
    # no probe rule is added when that catch-all already returns DIRECT/REJECT.
    target = catchall[1]
    injected = [] if target in ('DIRECT', 'COMPATIBLE', 'REJECT', 'REJECT-DROP') else [
        'DOMAIN-SUFFIX,' + domain + ',' + target for domain in probe_domains
        if 'DOMAIN-SUFFIX,' + domain + ',' + target not in rules]
    result['rules'] = ['IN-NAME,lf-strict,lf-remote'] + [
        'DOMAIN,' + domain + ',lf-remote' for domain in forced_domains] + injected + rules
    for key, value in policy.items():
        target = result
        parts = key.split('.')
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    result.update({'mode': 'rule', 'log-level': 'info', 'external-controller-unix': '/run/clash-verge-rev/core/mihomo.sock'})
    return yaml.safe_dump(result, allow_unicode=True, sort_keys=True).encode()


def prune_groups(document, dropped):
    """Detach dropped node names; refuse instead of silently rerouting a rule."""
    groups = document.get('proxy-groups') or []
    for group in groups:
        group['proxies'] = [ref for ref in group.get('proxies') or [] if ref not in dropped]
    while True:
        empty = {group['name'] for group in groups if not group.get('proxies')}
        if not empty:
            return
        for rule in document.get('rules') or []:
            if any(part.strip() in empty for part in rule.split(',')):
                raise Refused('empty group')
        groups[:] = [group for group in groups if group['name'] not in empty]
        for group in groups:
            group['proxies'] = [ref for ref in group['proxies'] if ref not in empty]


def pin_servers(config, resolver=socket.getaddrinfo):
    document = load(config)
    nodes, dropped = [], []
    for node in document['proxies']:
        if node.get('type') not in ('ss', 'vmess', 'vless', 'trojan', 'hysteria2', 'tuic', 'http', 'socks5'):
            raise Refused('unsupported node protocol')
        if node.get('plugin') or node.get('dialer-proxy') or node.get('interface-name') or node.get('routing-mark'):
            raise Refused('unsupported node transport')
        server, port = node.get('server'), node.get('port')
        if not isinstance(server, str) or type(port) is not int or not 1 <= port <= 65535:
            raise Refused('node endpoint')
        try:
            addresses = sorted({item[4][0] for item in resolver(server, port, type=socket.SOCK_STREAM)})
        except OSError:
            # Never leave an unpinned name for the core to resolve later; an
            # unavailable endpoint is dropped and counted, not silently dialed.
            dropped.append(node.get('name'))
            continue
        if not addresses or not all(public_address(ip) for ip in addresses):
            raise Refused('node destination')
        node['lf-source-server'] = server
        node['server'] = addresses[0]
        if node.get('tls') or node.get('type') in ('trojan', 'hysteria2', 'tuic'):
            node.setdefault('servername', node.get('sni') or server)
            node.setdefault('sni', node.get('servername') or server)
        ws = node.get('ws-opts')
        if isinstance(ws, dict):
            ws.setdefault('headers', {}).setdefault('Host', server)
        nodes.append(node)
    if not nodes:
        raise Refused('empty nodes')
    # A failing resolver must not silently shrink the source to a handful of
    # endpoints; bounded loss keeps the candidate representative.
    if len(dropped) > max(1, len(document['proxies']) // 5):
        raise Refused('node resolution')
    document['proxies'] = nodes
    prune_groups(document, set(dropped))
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=True).encode(), len(dropped)


def step(phase, function, *args):
    try:
        return function(*args)
    except Exception:
        raise Refused(phase) from None


def prepare(request, download=fetch, resolver=socket.getaddrinfo):
    registry, policy = request['registry'], request['policy']
    deadline, budget = time.monotonic() + 40, [TOTAL]
    data = step('fetch', download, registry['subscription'], deadline, budget)
    document = step('parse', load, data)
    registered = registry.get('providers', {})
    providers = document.get('proxy-providers', {}) or {}
    if not isinstance(providers, dict) or set(providers) != set(registered):
        raise Refused('render')
    payloads, hashes = {}, {'subscription': hashlib.sha256(data).hexdigest()}
    for name, spec in providers.items():
        if spec.get('url') != registered[name]['url']:
            raise Refused('render')
        payloads[name] = step('fetch', download, registered[name], deadline, budget)
        hashes['provider:' + name] = hashlib.sha256(payloads[name]).hexdigest()
    rendered = step('render', render, document, payloads, policy,
                    request.get('forced_domains', []), request.get('probe_domains', []),
                    request.get('remote_only_catchall', False))
    config, dropped = step('pin-addresses', pin_servers, rendered, resolver)
    return {'yaml': config.decode(), 'responses': hashes, 'dropped': dropped}


def main():
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    request = json.loads(sys.stdin.buffer.read(1024 * 1024 + 1))
    print(json.dumps(prepare(request), ensure_ascii=True))


if __name__ == '__main__':
    try:
        main()
    except Refused as error:
        phase = str(error) if str(error) in ('fetch', 'parse', 'render', 'pin-addresses') else 'worker'
        print(json.dumps({'error': phase}))
        raise SystemExit(1)
    except Exception:
        print('subscription fetch refused', file=sys.stderr)
        raise SystemExit(1)
