"""Bounded, offline content checks. Run only as the unprivileged core identity."""
import argparse
from collections import Counter
import ipaddress
import json
from pathlib import Path
import resource
import sys

import yaml

CATEGORIES = frozenset(('yaml_structure', 'private_server', 'certificate', 'empty_nodes',
    'group_reference', 'hosts_override', 'provider_change', 'rule_provider_change',
    'sensitive_route', 'protected_route', 'route_unknown', 'rule_volume', 'node_volume',
    'host_concentration', 'plaintext_credentials', 'broad_route', 'audit_error'))


class InvalidYaml(ValueError):
    pass


class Loader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in mapping:
                raise InvalidYaml('yaml_structure')
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def load(path):
    data = Path(path).read_bytes()
    if len(data) > 32 * 1024 * 1024:
        raise InvalidYaml('yaml_structure')
    value = yaml.load(data, Loader=Loader)
    if not isinstance(value, dict):
        raise InvalidYaml('yaml_structure')
    # Reject recursive/excessive alias expansion; the core and this worker must
    # not disagree about a cyclic or exponentially expanded YAML structure.
    count = 0
    def walk(item, ancestors=frozenset(), depth=0):
        nonlocal count
        count += 1
        if count > 500000 or depth > 50:
            raise InvalidYaml('yaml_structure')
        if isinstance(item, (dict, list)):
            if id(item) in ancestors:
                raise InvalidYaml('yaml_structure')
            children = item.values() if isinstance(item, dict) else item
            for child in children:
                walk(child, ancestors | {id(item)}, depth + 1)
    walk(value)
    return value


def enabled(value):
    return value is True or (isinstance(value, str) and value.lower() in ('true', 'yes', '1', 'on'))


def insecure(value):
    if isinstance(value, dict):
        return any((key.lower().replace('_', '-') == 'skip-cert-verify' and enabled(child))
                   or insecure(child) for key, child in value.items())
    return isinstance(value, list) and any(insecure(child) for child in value)


def graph(document):
    nodes, groups, providers = document.get('proxies') or [], document.get('proxy-groups') or [], document.get('proxy-providers') or {}
    if not isinstance(nodes, list) or not isinstance(groups, list) or not isinstance(providers, dict):
        raise InvalidYaml('yaml_structure')
    if not all(isinstance(n, dict) and isinstance(n.get('name'), str) for n in nodes + groups):
        raise InvalidYaml('yaml_structure')
    names = [n['name'] for n in nodes + groups]
    if len(names) != len(set(names)) or any(not n for n in names):
        raise InvalidYaml('yaml_structure')
    return {n['name']: n for n in nodes}, {g['name']: g for g in groups}, providers


def route(document, host, suffix=False):
    """Conservative proof for one exact domain, not a general Mihomo evaluator."""
    nodes, groups, providers = graph(document)
    def leaf(name, seen=frozenset()):
        if (name in nodes or name in groups) and name in ('DIRECT', 'COMPATIBLE', 'REJECT', 'REJECT-DROP', 'PASS', 'GLOBAL'):
            return 'unknown'
        if name in ('DIRECT', 'COMPATIBLE'):
            return 'direct'
        if name in ('REJECT', 'REJECT-DROP'):
            return 'reject'
        if name in seen:
            return 'unknown'
        if name in nodes:
            kind = nodes[name].get('type')
            return ('direct' if kind in ('direct', 'compatible') else 'reject' if kind == 'reject'
                    else 'unknown' if kind in ('pass', 'dns') else 'proxy')
        group = groups.get(name)
        if not group or any(group.get(key) for key in ('use', 'include-all', 'include-all-proxies',
                                                       'include-all-providers', 'filter', 'exclude-filter')):
            return 'unknown'
        values = {leaf(n, seen | {name}) for n in group.get('proxies', [])}
        return values.pop() if len(values) == 1 else 'unknown'
    mode = document.get('mode', 'rule')
    if mode == 'direct':
        return 'direct'
    if mode != 'rule':
        return 'unknown'
    outcomes = set()
    def proven(policy):
        outcomes.add(leaf(policy))
        return next(iter(outcomes)) if len(outcomes) == 1 else 'unknown'
    for rule in document.get('rules') or []:
        if not isinstance(rule, str):
            return 'unknown'
        parts = [s.strip() for s in rule.split(',')]
        kind = parts[0].upper()
        if kind in ('MATCH', 'FINAL') and len(parts) == 2:
            return proven(parts[1])
        if kind not in ('DOMAIN', 'DOMAIN-SUFFIX', 'DOMAIN-KEYWORD') or len(parts) != 3:
            return 'unknown'
        pattern = parts[1].lower().rstrip('.')
        if suffix:
            # Overapproximate every subdomain, rather than inferring a suffix
            # guarantee from its apex and a few sample names. Mixed outcomes
            # conservatively remain unknown, including intersecting keywords.
            if kind == 'DOMAIN':
                overlaps = pattern == host or pattern.endswith('.' + host)
                covers = False
            elif kind == 'DOMAIN-SUFFIX':
                covers = host == pattern or host.endswith('.' + pattern)
                overlaps = covers or pattern.endswith('.' + host)
            else:
                overlaps, covers = True, pattern in host
            if overlaps:
                outcomes.add(leaf(parts[2]))
            if covers:
                return next(iter(outcomes)) if len(outcomes) == 1 else 'unknown'
            continue
        match = (host == pattern if kind == 'DOMAIN' else
                 host == pattern or host.endswith('.' + pattern) if kind == 'DOMAIN-SUFFIX' else pattern in host)
        if match:
            return leaf(parts[2])
    return 'unknown'


def audit(candidate, baseline=None, settings=None):
    settings = settings or {}
    deny, warn, unknown = Counter(), Counter(), Counter()
    try:
        nodes, groups, providers = graph(candidate)
        old_nodes, _, _ = graph(baseline) if baseline is not None else ({}, {}, {})
        exceptions = set(settings.get('insecure_exceptions', []))
        allowed = [ipaddress.ip_network(n) for n in settings.get('allowed_server_cidrs', [])]
        for node in nodes.values():
            server = node.get('server')
            if isinstance(server, str):
                try:
                    ip = ipaddress.ip_address(server.strip('[]'))
                except ValueError:
                    ip = None
                if ip is not None and (ip.is_multicast or (
                        not ip.is_global and not any(ip in net for net in allowed))):
                    deny['private_server'] += 1
            if insecure(node) and node['name'] not in exceptions:
                deny['certificate'] += 1
        if not nodes and not providers:
            deny['empty_nodes'] += 1
        known = set(nodes) | set(groups) | {'DIRECT', 'COMPATIBLE', 'REJECT', 'REJECT-DROP', 'PASS', 'GLOBAL'}
        for group in groups.values():
            refs, uses = group.get('proxies') or [], group.get('use') or []
            if not isinstance(refs, list) or not isinstance(uses, list) or not all(isinstance(v, str) for v in refs + uses):
                raise InvalidYaml('yaml_structure')
            if not refs and not uses and not any(group.get(k) for k in ('include-all', 'include-all-proxies', 'include-all-providers')):
                deny['empty_nodes'] += 1
            if any(n not in known for n in refs) or any(n not in providers for n in uses):
                deny['group_reference'] += 1
        # Root's managed parser remains responsible for provider payloads and
        # paths. Source trust permits an initial provider; later changes require
        # the settled configuration to agree, rather than trusting the candidate.
        for field, category in (('hosts', 'hosts_override'), ('proxy-providers', 'provider_change'), ('rule-providers', 'rule_provider_change')):
            before = (baseline or {}).get(field) or {}
            after = candidate.get(field) or {}
            if not isinstance(after, dict):
                raise InvalidYaml('yaml_structure')
            if field == 'hosts':
                # A subscription, including a previously imported snapshot,
                # cannot grant itself permission to override local resolution.
                allowed = settings.get('allowed_hosts', {})
                if not isinstance(allowed, dict) or any(
                        key not in allowed or allowed[key] != value
                        for key, value in after.items()):
                    deny[category] += 1
            elif baseline is not None:
                if before != after:
                    deny[category] += 1
            elif field == 'rule-providers' or settings.get('subscription_trust', 'source') == 'fixed':
                if after:
                    deny[category] += 1
        sensitive = [domain.lower().rstrip('.') for domain in settings.get('sensitive_domains', [])]
        protected = {domain.lower().rstrip('.'): route for domain, route in settings.get('protected_routes', {}).items()}
        # Include explicitly mentioned subdomains, so an override below a
        # user-listed sensitive suffix cannot be hidden by testing only its apex.
        targets = set(sensitive) | set(protected)
        for document in (candidate, baseline or {}):
            for rule in document.get('rules') or []:
                parts = rule.split(',') if isinstance(rule, str) else []
                if len(parts) >= 3 and parts[0].upper() in ('DOMAIN', 'DOMAIN-SUFFIX'):
                    domain = parts[1].lower().rstrip('.')
                    if any(domain == d or domain.endswith('.' + d) for d in sensitive):
                        targets.add(domain)
                elif len(parts) >= 3 and parts[0].upper() == 'DOMAIN-KEYWORD':
                    keyword = parts[1].lower().strip('.')
                    for domain in sensitive:
                        if keyword and keyword not in domain:
                            targets.add(keyword + '.' + domain)
        uncertain = set()
        for domain in targets:
            after = route(candidate, domain)
            before = route(baseline, domain) if baseline is not None else None
            if after == 'unknown' or (baseline is not None and before == 'unknown'):
                unknown['route_unknown'] += 1
                uncertain.add(domain)
            elif domain in protected and after != protected[domain]:
                deny['protected_route'] += 1
            elif baseline is not None and before == 'direct' and after in ('proxy', 'reject'):
                deny['sensitive_route'] += 1
        for domain in sensitive:
            if domain not in uncertain and (route(candidate, domain, suffix=True) == 'unknown' or (
                    baseline is not None and route(baseline, domain, suffix=True) == 'unknown')):
                unknown['route_unknown'] += 1
        rules = candidate.get('rules') or []
        if not isinstance(rules, list) or not all(isinstance(v, str) for v in rules):
            raise InvalidYaml('yaml_structure')
        if len(nodes) > 1 and len({n.get('server') for n in nodes.values()}) == 1:
            warn['host_concentration'] += 1
        if baseline is not None:
            before_rules = baseline.get('rules') or []
            if len(before_rules) and abs(len(rules) - len(before_rules)) * 2 >= len(before_rules):
                warn['rule_volume'] += 1
            if len(old_nodes) and len(nodes) * 2 <= len(old_nodes):
                warn['node_volume'] += 1
            old_values = {json.dumps(n, sort_keys=True) for n in old_nodes.values()}
            for n in nodes.values():
                if n.get('type') in ('http', 'socks5') and not n.get('tls') and (n.get('username') or n.get('password')) and json.dumps(n, sort_keys=True) not in old_values:
                    warn['plaintext_credentials'] += 1
            old_rules = set(before_rules)
            for rule in rules:
                parts = rule.split(',')
                if rule not in old_rules and len(parts) >= 3 and parts[0].upper() in ('GEOSITE', 'GEOIP') and parts[2] not in ('DIRECT', 'REJECT', 'REJECT-DROP'):
                    warn['broad_route'] += 1
    except (ValueError, TypeError, KeyError, RecursionError):
        deny['yaml_structure'] += 1
    return dict(verdict='deny' if deny else 'unknown' if unknown else 'warn' if warn else 'pass',
                deny=dict(deny), warn=dict(warn), unknown=dict(unknown))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('settings')
    parser.add_argument('candidate')
    parser.add_argument('--baseline')
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    try:
        result = audit(load(args.candidate), load(args.baseline) if args.baseline else None,
                       json.loads(Path(args.settings).read_text()))
    except (OSError, ValueError, yaml.YAMLError, RecursionError, MemoryError):
        result = dict(verdict='deny', deny={'yaml_structure': 1}, warn={}, unknown={})
    print(json.dumps(result, separators=(',', ':')))


if __name__ == '__main__':
    main()
