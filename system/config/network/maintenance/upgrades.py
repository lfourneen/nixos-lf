"""Compare reviewed upstream inputs and select the required upgrade regressions.

This is a review gate, not proof of upstream semantics. Updating the baseline
requires source/packaging review and the listed behavioral checks. No command
silently accepts changed inputs or rewrites the review file.
"""
import argparse
import json
from pathlib import Path
import re
import sys


# Minimum behavioral obligations cannot be removed by shortening a JSON list.
REQUIRED = {
    'controlled-source': {'proxy-source', 'proxy-source-config', 'proxy-isolation-vm', 'proxy-control', 'proxy-refresh', 'proxy-core'},
    'browser-isolation': {'proxy-source', 'proxy-source-config', 'proxy-isolation-vm', 'browser-privacy', 'browser-sandbox-vm', 'proxy-core'},
    'mihomo': {'proxy-core', 'proxy-profile', 'proxy-control-vm', 'proxy-uniform-vm',
               'proxy-target-vm', 'proxy-guest-vm', 'proxy-dns-strict-vm', 'proxy-network-vm',
               'browser-ech-vm', 'browser-ech-uniform-vm'},
    'sing-tun': {'proxy-core', 'proxy-rules', 'proxy-guest-rules', 'proxy-control-vm',
                 'proxy-uniform-vm', 'proxy-guest-vm', 'proxy-network-vm'},
    'socks': {'proxy-core', 'proxy-target-vm', 'proxy-uniform-vm', 'proxy-network-vm'},
    'gost': {'proxy-gost', 'proxy-control-vm', 'proxy-uniform-vm', 'browser-ech-vm'},
    'clash': {'proxy-control', 'proxy-control-config', 'proxy-gui', 'proxy-control-vm'},
    'dns': {'proxy-dns-config', 'proxy-dns-strict-rules', 'proxy-dns-strict-vm', 'proxy-network-vm'},
    'firefox': {'browser-privacy', 'browser-sandbox-vm', 'browser-ech-vm', 'browser-ech-uniform-vm'},
    'tailscale': {'tailscale-transport', 'proxy-tailscale-vm', 'proxy-egress-vm'},
    'firewall': {'proxy-rules', 'proxy-egress-rules', 'proxy-firewall-vm', 'proxy-network-vm'},
    'link-identity': {'network-identity-config', 'network-identity-vm', 'proxy-control-vm', 'proxy-network-vm'},
}
SOURCE_GROUPS = {
    'controlled-source': {'mihomo', 'helper'},
    'browser-isolation': {'firefox', 'bubblewrap', 'systemd', 'mihomo', 'dbusProxy', 'seccomp'},
    'mihomo': {'mihomo'}, 'sing-tun': {'mihomo'}, 'socks': {'mihomo'},
    'gost': {'gost'}, 'clash': {'helper', 'gui'},
    'dns': {'dnsmasq', 'unbound', 'chrony'},
    'firefox': {'firefox', 'bubblewrap', 'dbusProxy', 'seccomp'},
    'tailscale': {'tailscale'}, 'firewall': {'nftables', 'networkmanager', 'systemd'},
    'link-identity': {'networkmanager', 'systemd', 'dhcpcd'},
}
FIELDS = {'version', 'source', 'dependencies', 'recipe'}


def validate(review, selected):
    if review.get('schema') != 1 or set(review.get('components', {})) != set(REQUIRED):
        raise ValueError('invalid or incomplete component review schema')
    known = set().union(*SOURCE_GROUPS.values())
    if set(review.get('sources', {})) != known or set(selected) != known:
        raise ValueError('selected/reviewed source inventory differs from the component contract')
    for inventory in (review['sources'], selected):
        for name, source in inventory.items():
            if set(source) != FIELDS or not all(isinstance(source[k], str) and source[k]
                                                for k in ('version', 'source', 'recipe')):
                raise ValueError(f'invalid source fingerprint: {name}')
            if source['dependencies'] is not None and (not isinstance(source['dependencies'], str) or not source['dependencies']):
                raise ValueError(f'invalid dependency fingerprint: {name}')
            if not re.fullmatch('[0-9a-f]{64}', source['recipe']):
                raise ValueError(f'invalid package recipe fingerprint: {name}')
    for name, component in review['components'].items():
        if set(component) != {'sources', 'checks', 'entrypoints', 'invariants'} or not all(
                isinstance(value, list) and value and all(isinstance(x, str) and x.strip() for x in value)
                for value in component.values()):
            raise ValueError(f'{name}: invalid component review record')
        if set(component.get('sources', [])) != SOURCE_GROUPS[name]:
            raise ValueError(f'{name}: required upstream inputs were removed')
        checks = component.get('checks', [])
        if not REQUIRED[name] <= set(checks) or not all(re.fullmatch('[a-z0-9-]+', c) for c in checks):
            raise ValueError(f'{name}: required regression checks were removed or invalid')
        for field in ('entrypoints', 'invariants'):
            if not component.get(field) or not all(isinstance(x, str) and x.strip() for x in component[field]):
                raise ValueError(f'{name}: missing {field}')


def changed_components(review, selected):
    validate(review, selected)
    changed = {name for name in selected if selected[name] != review['sources'][name]}
    return sorted(name for name, component in review['components'].items()
                  if changed.intersection(component['sources']))


def plan(review, selected, components=()):
    changed = changed_components(review, selected)
    names = sorted(set(components or changed))
    if set(names) - set(REQUIRED):
        raise ValueError('unknown upgrade component')
    checks = sorted({check for name in names for check in review['components'][name]['checks']})
    return {'changed': changed, 'components': {name: review['components'][name] for name in names},
            'checks': checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'plan'))
    parser.add_argument('review', type=Path)
    parser.add_argument('selected', type=Path)
    parser.add_argument('components', nargs='*', help='component names; default: changed components')
    args = parser.parse_args()
    try:
        review = json.loads(args.review.read_text())
        selected = json.loads(args.selected.read_text())
        result = plan(review, selected, args.components)
        if args.command == 'plan':
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if result['changed']:
            print('Network upstream inputs need source/packaging review: ' + ', '.join(result['changed']), file=sys.stderr)
            print('Inspect the entrypoints/invariants and run the plan checks before updating system/config/network/maintenance/sources.json.', file=sys.stderr)
            print(json.dumps(result, indent=2, ensure_ascii=False), file=sys.stderr)
            return 1
        print('Network upstream source, dependency and recipe baselines match')
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'network-upgrades: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
