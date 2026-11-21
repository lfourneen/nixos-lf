"""Bounded subscription downloader and deterministic renderer; runs without root.

Input is supplied only by the root broker on stdin. Never import desktop scripts,
proxy environment, GUI indexes or cached provider files. Errors contain no URLs.
"""
import hashlib
import errno
import http.client
import ipaddress
import json
import re
import resource
import socket
import ssl
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import yaml

LIMIT = 8 * 1024 * 1024
TOTAL = 32 * 1024 * 1024


class Refused(ValueError):
    def __init__(self, message, reason=None, http_status=None):
        super().__init__(message)
        self.reason, self.http_status = reason, http_status


FAILURE_REASONS = frozenset(('dns lookup failed', 'certificate rejected', 'tls failed',
    'connection timeout', 'connection refused', 'connection reset', 'network unreachable',
    'http rejected', 'encoding unsupported', 'redirect refused', 'destination refused',
    'response limit', 'yaml invalid', 'configuration unsupported', 'node resolution failed',
    'selected egress unavailable', 'worker failure'))


def failure_reason(error, phase):
    if isinstance(error, Refused) and error.reason in FAILURE_REASONS:
        return error.reason
    for kind, reason in ((socket.gaierror, 'dns lookup failed'),
                         (ssl.SSLCertVerificationError, 'certificate rejected'),
                         (ssl.SSLError, 'tls failed'), (TimeoutError, 'connection timeout'),
                         (ConnectionRefusedError, 'connection refused'),
                         (ConnectionResetError, 'connection reset')):
        if isinstance(error, kind):
            return reason
    if isinstance(error, OSError) and error.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
        return 'network unreachable'
    if isinstance(error, Refused):
        reason = {'redirect': 'redirect refused', 'redirect count': 'redirect refused',
                  'destination': 'destination refused', 'response size': 'response limit',
                  'deadline': 'connection timeout', 'selected egress unavailable': 'selected egress unavailable'}.get(str(error))
        if reason:
            return reason
    return {'parse': 'yaml invalid', 'render': 'configuration unsupported',
            'pin-addresses': 'node resolution failed'}.get(phase, 'worker failure')


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


SCHEMES = {'https': 443}

# A group test URL is only a latency target; the system's own probes decide
# readiness. HTTP is accepted there on its default port because common
# subscription templates use the plaintext generate_204 endpoint.
TEST_SCHEMES = {'https': 443, 'http': 80}


def endpoint(url, schemes=SCHEMES):
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) >= 127 for c in url):
        raise Refused('url encoding')
    p = urlsplit(url)
    if p.scheme not in schemes or not p.hostname or p.username or p.password or p.fragment:
        raise Refused('url scheme')
    if p.port not in (None, schemes[p.scheme]):
        raise Refused('url port')
    if '%' in p.netloc or '\\' in url:
        raise Refused('url authority')
    host = p.hostname.lower().rstrip('.')
    if host != p.hostname or '..' in host:
        raise Refused('url host')
    return host, p.port or schemes[p.scheme], p.path or '/', p.query


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


def real_resolver(settings):
    """Use the filtered real-address gateway, never the B fake-IP resolver."""
    import dns.exception
    import dns.resolver

    address, port = settings.get('address'), settings.get('port')
    if address not in ('127.0.0.1', '::1') or type(port) is not int or not 1 <= port <= 65535:
        raise Refused('resolver settings')
    backend = dns.resolver.Resolver(configure=False)
    backend.nameservers, backend.port = [address], port
    backend.timeout = 1
    backend.cache = dns.resolver.Cache()
    blocked = {name.lower() for name in settings.get('blocked', ())}
    if settings.get('blocked_file'):
        blocked.update(Path(settings['blocked_file']).read_text().splitlines())
    exceptions = {name.lower() for name in settings.get('exceptions', ())}

    def lookup(host, port, *, type=socket.SOCK_STREAM):
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            suffix = host.lower().rstrip('.')
            while suffix and suffix not in exceptions:
                if suffix in blocked:
                    raise socket.gaierror(socket.EAI_NONAME, 'filtered DNS name')
                _, _, suffix = suffix.partition('.')
            addresses = []
            deadline = time.monotonic() + 4
            for kind in ('A', 'AAAA'):
                try:
                    answer = backend.resolve(host, kind, search=False,
                                             lifetime=max(0.001, deadline - time.monotonic()))
                except dns.resolver.NoAnswer:
                    continue
                except dns.exception.DNSException:
                    # A validated answer for one RR type remains usable when
                    # the other family is unavailable. Never switch resolvers.
                    continue
                addresses.extend(ipaddress.ip_address(item.address) for item in answer)
        if not addresses:
            raise socket.gaierror(socket.EAI_NONAME, 'real-address DNS has no address')
        return [(socket.AF_INET if ip.version == 4 else socket.AF_INET6, type, socket.IPPROTO_TCP,
                 '', (str(ip), port) if ip.version == 4 else (str(ip), port, 0, 0))
                for ip in addresses]
    return lookup


def public_addresses(resolver, host, port):
    values = {item[4][0] for item in resolver(host, port, type=socket.SOCK_STREAM)}
    if not values or not all(public_address(ip) for ip in values):
        raise Refused('destination')
    # Prefer IPv4 when both are present; keep IPv6-only sources supported.
    # The old lexical ordering could select an unreachable IPv6 address first.
    return sorted(values, key=lambda value: (ipaddress.ip_address(value).version,
                                           int(ipaddress.ip_address(value))))


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
        addresses = public_addresses(resolver, host, port)
        response = conn = None
        failure = None
        for index, address in enumerate(addresses):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Refused('deadline')
            # Reserve time for other validated addresses, under one deadline.
            conn = connection(host, address, min(10, remaining / (len(addresses) - index)), context)
            try:
                request_headers = dict(headers)
                if (host, port) == credentials_origin:
                    request_headers.update(extra)
                conn.request('GET', path + ('?' + query if query else ''), headers=request_headers)
                response = conn.getresponse()
                break
            except (OSError, http.client.HTTPException) as error:
                failure = error
                conn.close()
        if response is None:
            raise failure or Refused('http connection')
        try:
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
            if response.status != 200:
                raise Refused('http response', 'http rejected', response.status)
            if response.getheader('Content-Encoding', 'identity') != 'identity':
                raise Refused('http response', 'encoding unsupported', response.status)
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


def node_identity(node):
    return hashlib.sha256(json.dumps(node, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':')).encode()).hexdigest()


def egress_nodes(document, tunnel=None, require_protected=False):
    """Return ordered, fixed leaves reachable from the subscription catch-all."""
    nodes = {node['name']: node for node in document['proxies']}
    groups = {group['name']: group for group in document['proxy-groups']}
    result, visited = [], set()
    def visit(name, parents):
        if name in parents:
            raise Refused('catchall graph')
        if name in visited:
            return
        visited.add(name)
        if name in nodes:
            node = nodes[name]
            if node.get('type') in ('ss', 'vmess', 'vless', 'trojan', 'hysteria2', 'tuic', 'http', 'socks5') \
                    and not ech_lookup(node) and (not require_protected or tunnel or protected_transport(node)):
                result.append(node)
        elif name in groups:
            for child in groups[name]['proxies']:
                visit(child, parents | {name})
        elif name not in ('DIRECT', 'COMPATIBLE', 'REJECT', 'REJECT-DROP', 'PASS', 'GLOBAL'):
            raise Refused('catchall graph')
    visit(document['rules'][-1].split(',')[1], set())
    if not result:
        raise Refused('empty catchall')
    if tunnel:
        relay = {'address': tunnel['relayAddress'], 'port': tunnel['relayPort']}
        result = [node for node in result if wireguard_relay(node, relay)]
    return result


def selected_egress(document):
    targets = document.get('lf-private-targets', {})
    if targets.get('single-egress') is not True:
        return None
    return node_identity(document['proxies'][0])


def ech_lookup(node):
    """True when dialing the node needs a DNS lookup for its ECH configuration."""
    options = node.get('ech-opts')
    return isinstance(options, dict) and not options.get('config')


def transport_fields(node):
    fields = {}
    for key, value in node.items():
        key = key.lower().replace('_', '-')
        if key in fields:
            raise Refused('ambiguous node transport')
        fields[key] = value
    return fields


def skips_certificate(value):
    if isinstance(value, dict):
        return any((key.lower().replace('_', '-') == 'skip-cert-verify' and child is not False)
                   or skips_certificate(child) for key, child in value.items())
    if isinstance(value, list):
        return any(skips_certificate(child) for child in value)
    return False


def allowed_transport_fields(node, fields):
    return not any(fields.get(key) for key in (
        'dialer-proxy', 'interface-name', 'routing-mark', 'plugin',
        'shadow-tls-opts', 'restls-opts', 'jls-opts', 'tlsmirror-opts',
    )) and all(key not in fields or type(fields[key]) is bool for key in ('tls', 'udp')) \
        and not skips_certificate(node)


def wireguard_relay(node, relay):
    if not relay:
        return False
    fields = transport_fields(node)
    return (fields.get('type') == 'socks5' and fields.get('server') == relay['address']
            and type(fields.get('port')) is int and fields['port'] == relay['port']
            and fields.get('udp') is True and fields.get('network', '') == ''
            and (fields.get('tls') is True or not (fields.get('username') or fields.get('password')))
            and allowed_transport_fields(node, fields))


def vmess_aead(fields):
    identity = fields.get('uuid')
    if (type(fields.get('alterid')) is not int or fields['alterid'] != 0
            or fields.get('cipher') not in ('auto', 'aes-128-gcm', 'chacha20-poly1305')
            or not isinstance(identity, str)):
        return False
    try:
        parsed = uuid.UUID(identity)
    except ValueError:
        return False
    return parsed.int != 0 and str(parsed) == identity.lower()


def protected_transport(node):
    fields = transport_fields(node)
    if not allowed_transport_fields(node, fields):
        return False
    kind = fields.get('type')
    network = fields.get('network', '')
    tls = fields.get('tls') is True
    if fields.get('reality-opts') and (kind not in ('vmess', 'vless', 'trojan') or network == 'ws'):
        return False
    if kind == 'tuic' and (fields.get('disable-sni', False) is not False or fields.get('ip')):
        return False
    if kind == 'hysteria2' and fields.get('realm-opts'):
        return False
    if kind in ('http', 'socks5'):
        return network == '' and tls and fields.get('udp') is not True
    if kind == 'ss':
        password = fields.get('password')
        return isinstance(password, str) and bool(password.strip()) and network == '' and fields.get('cipher') in (
            'aes-128-gcm', 'aes-192-gcm', 'aes-256-gcm', 'chacha20-ietf-poly1305',
            'xchacha20-ietf-poly1305', '2022-blake3-aes-128-gcm',
            '2022-blake3-aes-256-gcm', '2022-blake3-chacha20-poly1305',
        )
    if kind in ('vmess', 'vless'):
        return network in ('', 'tcp', 'ws', 'http', 'h2', 'grpc') and (tls or kind == 'vmess' and vmess_aead(fields))
    if kind == 'trojan':
        return network in ('', 'tcp', 'ws', 'grpc')
    return kind in ('hysteria2', 'tuic', 'anytls') and network == ''


def prune_nodes(document, dropped):
    if not dropped:
        return
    for rule in document['rules']:
        if not isinstance(rule, str) or any(part.strip() in dropped for part in rule.split(',')[1:]):
            raise Refused('rule requires a rejected node')
    document['proxies'] = [node for node in document['proxies'] if node['name'] not in dropped]
    prune_groups(document, dropped)


def normalize(document, payloads, delay_url=None):
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
        if 'url' in group and (not delay_url or group['url'] != delay_url):
            endpoint(group['url'], TEST_SCHEMES)
        for key, low, high in (('interval', 0, 86400), ('tolerance', 0, 60000),
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
    return result


def render(document, payloads, policy, forced_domains=(), probe_domains=(), remote_only_catchall=False, source=None,
           egress_id=None):
    targets = policy.get('lf-private-targets', {})
    delay_url = targets.get('delay-test-url')
    result = normalize(document, payloads, delay_url)
    proxy_only = targets.get('proxy-only') is True
    require_protected = targets.get('require-protected-node-transport') is True
    tunnel = policy.get('lf-ip-tunnel') if proxy_only and targets.get('single-egress') is True else None
    relay = targets.get('wireguard-relay')
    if require_protected:
        prune_nodes(result, {node['name'] for node in result['proxies']
                             if not protected_transport(node) and not wireguard_relay(node, relay)})
    if source is not None:
        source.update(json.loads(json.dumps(result)))
    if delay_url:
        for group in result['proxy-groups']:
            group['url'] = delay_url
            group['interval'] = 0 if group['type'] == 'select' else max(300, group.get('interval', 300))
            group['timeout'] = min(5000, group.get('timeout', 5000))
            group.pop('expected-status', None)
    if require_protected and not tunnel:
        prune_nodes(result, {node['name'] for node in result['proxies'] if wireguard_relay(node, relay)})
    nodes, groups, rules = (result[key] for key in ('proxies', 'proxy-groups', 'rules'))
    names = {node['name'] for node in nodes}
    catchall = rules[-1].split(',')
    if proxy_only and any(node.get('ports') or node.get('hop-interval') for node in nodes):
        raise Refused('proxy-only requires fixed node ports')
    if remote_only_catchall or proxy_only:
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
    # The managed transport DNS travels through this leaf, so a node that must
    # query its own ECH configuration cannot be the leaf: the lookup would loop
    # back through the node it is trying to dial. Such nodes stay available in
    # the subscription's own groups, where a non-ECH leaf resolves their ECH.
    reserved = {'lf-remote', 'lf-dns'}
    if (reserved & names or any(g['name'] in reserved or 'lf-dns' in g['proxies'] for g in groups)
            or any(isinstance(rule, str) and 'lf-dns' in rule.split(',') for rule in rules)):
        raise Refused('reserved group')
    remote = [n['name'] for n in nodes
              if n.get('type') not in ('direct', 'compatible', 'reject', 'dns') and not ech_lookup(n)]
    if not remote:
        raise Refused('empty nodes')
    groups.append({'name': 'lf-remote', 'type': 'select', 'proxies': [catchall[1]] if proxy_only else remote})
    transport = policy.get('lf-dns-transport') or {}
    if not proxy_only and any(url.endswith('#lf-dns') for url in transport.get('dns', {}).get('nameserver', [])):
        groups.append({'name': 'lf-dns', 'type': 'select', 'hidden': True,
                       'proxies': remote[1:3] + remote[:1]})
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
    if proxy_only:
        result['rules'] = ['IN-NAME,lf-strict,lf-remote', 'MATCH,' + catchall[1]]
    if policy.get('lf-private-targets', {}).get('single-egress') is True:
        if not proxy_only:
            raise Refused('single egress requires proxy-only')
        candidates = egress_nodes(result, tunnel, require_protected)
        selected = next((node for node in candidates if not egress_id or node_identity(node) == egress_id), None)
        if selected is None:
            raise Refused('selected egress unavailable')
        result['proxies'] = [selected]
        result['proxy-groups'] = [{'name': 'lf-remote', 'type': 'select', 'proxies': [selected['name']]}]
        result['rules'] = ['IN-NAME,lf-strict,lf-remote', 'MATCH,lf-remote']
    for key, value in policy.items():
        if key == 'lf-ip-tunnel':
            continue
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


def loopback_literal(server):
    try:
        address = ipaddress.ip_address(server)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback


def pin_servers(config, resolver=socket.getaddrinfo, sanitize_insecure=False):
    document = load(config)
    nodes, unavailable, excluded = [], [], []
    for node in document['proxies']:
        if node.get('type') not in ('ss', 'vmess', 'vless', 'trojan', 'hysteria2', 'tuic', 'http', 'socks5', 'anytls'):
            raise Refused('unsupported node protocol')
        if node.get('plugin') or node.get('dialer-proxy') or node.get('interface-name') or node.get('routing-mark'):
            raise Refused('unsupported node transport')
        # Authorized source sanitizing prunes unchecked TLS before DNS pinning.
        if sanitize_insecure and skips_certificate(node):
            excluded.append(node.get('name'))
            continue
        server, port = node.get('server'), node.get('port')
        if not isinstance(server, str) or type(port) is not int or not 1 <= port <= 65535:
            raise Refused('node endpoint')
        # Some subscription banners use literal loopback endpoints. Keep DNS
        # answers subject to the same public-address checks as other endpoints.
        if loopback_literal(server):
            unavailable.append(node.get('name'))
            continue
        try:
            addresses = public_addresses(resolver, server, port)
        except OSError:
            unavailable.append(node.get('name'))
            continue
        node['lf-source-server'] = server
        node['server'] = addresses[0]
        if node.get('tls') or node.get('type') in ('trojan', 'hysteria2', 'tuic', 'anytls'):
            node.setdefault('servername', node.get('sni') or server)
            node.setdefault('sni', node.get('servername') or server)
        if node.get('type') == 'socks5' and node.get('tls') is True:
            node.setdefault('name-cert-verify', server)
        ws = node.get('ws-opts')
        if isinstance(ws, dict):
            ws.setdefault('headers', {}).setdefault('Host', server)
        nodes.append(node)
    if not nodes:
        raise Refused('empty nodes')
    # The resolution limit applies to nodes eligible under the TLS policy.
    if len(unavailable) > max(1, (len(nodes) + len(unavailable)) // 5):
        raise Refused('node resolution')
    dropped = excluded + unavailable
    document['proxies'] = nodes
    prune_groups(document, set(dropped))
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=True).encode(), len(dropped)


def step(phase, function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except Exception as error:
        raise Refused(phase, failure_reason(error, phase), getattr(error, 'http_status', None)) from None


def prepare(request, download=None, resolver=socket.getaddrinfo):
    registry, policy = request['registry'], request['policy']
    if download is None:
        pinned = request.get('pinned_downloads')
        def download_resolver(host, port, *, type):
            return [(0, type, 0, '', (address, port)) for address in pinned.get(host, ())]

        def download(spec, deadline, budget):
            return fetch(spec, deadline, budget, resolver=resolver if pinned is None else download_resolver)
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
    source = step('render', normalize, document, payloads,
                  policy.get('lf-private-targets', {}).get('delay-test-url'))
    pinned_config, dropped = step('pin-addresses', pin_servers, yaml.safe_dump(source).encode(),
                                  resolver, request.get('sanitize_insecure') is True)
    pinned = load(pinned_config)['proxies']
    retained = {node['name'] for node in pinned}
    removed = {node['name'] for node in source['proxies']} - retained
    source['proxies'] = pinned
    prune_groups(source, removed)
    accepted_source = {}
    config = step('render', render, source, {}, policy, request.get('forced_domains', []),
                  request.get('probe_domains', []), request.get('remote_only_catchall', False),
                  source=accepted_source, egress_id=request.get('egress_id'))
    dropped += len(source['proxies']) - len(accepted_source['proxies'])
    return {'yaml': config.decode(), 'responses': hashes, 'dropped': dropped,
            'source_data': json.dumps(accepted_source, sort_keys=True, ensure_ascii=False),
            'egress_id': selected_egress(load(config))}


def main():
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    request = json.loads(sys.stdin.buffer.read(1024 * 1024 + 1))
    resolver = real_resolver(request['resolver']) if request.get('resolver') else socket.getaddrinfo
    print(json.dumps(prepare(request, resolver=resolver), ensure_ascii=True))


if __name__ == '__main__':
    try:
        main()
    except Refused as error:
        phase = str(error) if str(error) in ('fetch', 'parse', 'render', 'pin-addresses') else 'worker'
        result = dict(error=phase, reason=failure_reason(error, phase))
        if type(error.http_status) is int and 100 <= error.http_status <= 599:
            result['http_status'] = error.http_status
        print(json.dumps(result))
        raise SystemExit(1)
    except Exception:
        print('subscription fetch refused', file=sys.stderr)
        raise SystemExit(1)
