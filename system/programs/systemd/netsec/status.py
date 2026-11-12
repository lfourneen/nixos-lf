"""Human-readable view of the shared, read-only network readiness contract."""
import argparse
import importlib.util
import json
import os
import shutil
import sys
import unicodedata

MODES = {'direct': 'A · Direct', 'proxy': 'B · Proxy', 'blocked': 'C · Protected', 'unenforced': 'Unenforced'}
DNS = {'dot': 'Unbound · authenticated DoT', 'proxy': 'Mihomo · managed DNS', 'dot-priming': 'DoT · trust anchor initialization',
       'bootstrap-plaintext': 'Plaintext recovery · bootstrap', 'portal-plaintext': 'Plaintext recovery · portal',
       'degraded-plaintext': 'Plaintext recovery · encrypted upstream unavailable', 'degraded-dot-only': 'Encrypted upstream unavailable',
       'resolver-error': 'Resolver identity check failed', 'split-error': 'Split DNS configuration error'}
REASONS = {'node': 'Proxy request failed', 'dns': 'Probe name resolution failed', 'tun': 'Transparent path check failed',
           'no-tun': 'TUN device or routes unavailable', 'core-gone': 'Proxy core is not running', 'config': 'Core configuration or listener identity mismatch',
           'route': 'Probe did not use the authorized proxy path', 'obs': 'Connection observation unavailable', 'direct-mode': 'Core is in Direct mode',
           'request': 'Waiting for proxy startup', 'reload': 'Reloading policy', 'stopping': 'Stopping services',
           'unauthorized-core': 'Core configuration is not authorized', 'policy-mismatch': 'Kernel policy differs from configuration',
           'policy-repair': 'Repairing policy', 'policy-repair-failed': 'Policy repair failed',
           'policy-repair-backoff': 'Waiting to retry policy repair', 'interrupted': 'Probe cancelled by supervisor'}
PHASES = {'prepare-policy': 'Certificate or subscription policy rejected', 'prepare-profile': 'Desktop profile missing or invalid',
          'prepare-assets': 'Required assets missing', 'prepare-core': 'Core configuration validation failed', 'prepared': 'Configuration checks passed'}


def clean(value):
    return ''.join(c if not unicodedata.category(c).startswith('C') else ' ' for c in str(value))[:1000]


def width(value):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in value)


def wrap(value, maximum):
    lines, line = [], ''
    for word in clean(value).split():
        candidate = (line + ' ' + word) if line else word
        if width(candidate) <= maximum:
            line = candidate
            continue
        if line:
            lines.append(line)
        line = ''
        for char in word:
            if width(line + char) > maximum:
                lines.append(line)
                line = ''
            line += char
    return lines + [line]


def age(observation):
    seconds = observation.get('age_seconds')
    return 'Age unknown' if seconds is None else f'{seconds:g}s ago'


def rows(state):
    ready, mode = state.get('ready', False), state.get('mode', 'unknown')
    yield 'Network status', ('Ready' if ready else 'Protected · waiting for recovery' if mode == 'blocked' else 'Not ready')
    yield 'Mode', MODES.get(mode, 'Unknown') + '; requested ' + MODES.get(state.get('target'), 'Unknown')
    if mode == 'proxy':
        yield 'Application egress', 'Uniform proxy routing' if state.get('proxy_only') else 'Rule routing · DIRECT or proxy'
    elif mode == 'direct':
        yield 'Application egress', 'Direct · local Gost provides passthrough'
    elif mode == 'blocked':
        yield 'Application egress', 'Blocked · core recovery and link maintenance retained'
    if state.get('reason'):
        yield 'Transition reason', REASONS.get(str(state['reason']).split(':')[0], 'Inspect coordinator state with proxy-inspect')
    yield 'System DNS', DNS.get(state.get('dns_mode'), 'Unknown')
    if state.get('mode') == 'direct':
        encryption = {'strict': 'Strict encryption', 'opportunistic': 'Bounded plaintext recovery allowed'}.get(state.get('dns_encryption'), 'Unknown policy')
        yield 'DNS policy', encryption
        verdicts = {'secure': 'Validated', 'reachable': 'Reachable', 'unavailable': 'Unavailable',
                    'validation-error': 'Validation failed', 'indeterminate': 'Indeterminate', 'insecure': 'Unsigned answer',
                    'not-probed': 'Not probed', 'unknown': 'No observation'}
        yield 'DNS checks', 'Local: ' + verdicts.get(state.get('dns_local'), 'No observation') + '; encrypted upstream: ' + verdicts.get(state.get('dns_transport'), 'No observation')
        if state.get('budget_remaining') is not None:
            yield 'Recovery budget', f"Remaining {state['budget_remaining']}s / total {state.get('budget_total') or '?'}s; active window {state.get('window_remaining', 0)}s"
        if state.get('plaintext_override'):
            yield 'Manual override', 'Plaintext recovery override enabled'
    observations = state.get('observations', {})
    for key, label, result in [('verification', 'Firewall check', {'ok': 'Passed'}.get(state.get('verification'), 'Not passed')),
                               ('relay', 'Local relay', MODES.get(state.get('relay_mode'), 'Unknown')),
                               ('dns_leases', 'DNS leases', 'Applied' if state.get('dns_lease_status') == 'applied' else 'Pending')]:
        observation = observations.get(key, {})
        flags = []
        if not observation.get('fresh'): flags.append('Observation stale or missing')
        if not observation.get('generation_matches'): flags.append('Policy generation differs')
        yield label, f"{result} · {age(observation)}" + ('; ' + ', '.join(flags) if flags else '')
    yield 'Enforcement alarm', 'Active · inspection required' if state.get('alarm') else 'None'
    health = state.get('last_proxy_health', {})
    if mode in ('proxy', 'blocked') and health:
        value = 'Passed' if health.get('ok') else REASONS.get(health.get('reason'), 'Incomplete')
        yield 'Last proxy probe', value + f" · duration {health.get('duration_ms', '?')} ms"
    preflight = state.get('last_preflight', {})
    if preflight:
        yield 'Last preparation', PHASES.get(preflight.get('phase'), 'Incomplete')
        subscription = preflight.get('subscription', {})
        if subscription:
            yield 'Profile nodes', f"Inline {subscription.get('inline_nodes', 0)}; providers {subscription.get('mutable_sources', 0)}"
            yield 'TLS exceptions', f"Allowed by name {subscription.get('certificate_exceptions_allowed', 0)}; rejected {subscription.get('certificate_exceptions_rejected', 0)}"
            if subscription.get('mutable_sources'):
                yield 'Provider validation', 'Core validates contents at load/refresh; preflight checks configuration'
                if subscription.get('named_certificate_exceptions'):
                    yield 'Provider trust', 'Named TLS exceptions follow same-named provider replacements'
    if not ready:
        for reason in state.get('reasons', [])[:10]:
            translated = {'policy is not proxy': 'Proxy mode', 'policy is not direct': 'Direct mode',
                          'DNS is not proxy': 'Proxy DNS path', 'direct DNS unavailable': 'Direct DNS availability',
                          'enforcement alarm active': 'Enforcement alarm clearance', 'rules not verified for this generation': 'Current policy verification',
                          'rules verification stale': 'Fresh rule verification', 'plaintext window expired': 'A valid plaintext recovery lease'}
            yield 'Waiting for', translated.get(reason, clean(reason))
        yield 'Diagnostics', 'proxy-control preflight; proxy-inspect'
        if mode == 'blocked':
            yield 'Helper logs', 'sudo journalctl -b -u clash-verge.service -n 60'
            yield 'Core logs', 'sudo tail -n 60 /var/lib/clash-verge-service/logs/service_latest.log'
    if preflight and not preflight.get('ready'):
        yield 'Preparation action', 'Regenerate the GUI profile and run proxy-control preflight; parser details: sudo cat /run/proxy-mode/preflight.log'


def render(state, columns=88, color=False, plain=False):
    content = list(rows(state))
    if plain:
        return '\n'.join(label + ': ' + clean(value) for label, value in content)
    columns = max(32, min(columns, 110))
    inner = columns - 4
    title = ' Network status '
    lines = ['╭' + title + '─' * (columns - width(title) - 2) + '╮']
    label_width = max(width(label) for label, _ in content) + 2
    for label, value in content:
        # Wrap values independently to keep continuation lines aligned.
        value_width = inner - label_width
        if value_width >= 22:
            wrapped = wrap(value, value_width)
            body = [label + ' ' * (label_width - width(label)) + wrapped[0]]
            body += [' ' * label_width + line for line in wrapped[1:]]
        else:
            body = [label] + ['  ' + line for line in wrap(value, inner - 2)]
        for line in body:
            lines.append('│ ' + line + ' ' * (inner - width(line)) + ' │')
    lines.append('╰' + '─' * (columns - 2) + '╯')
    text = '\n'.join(lines)
    if color:
        tone = '32' if state.get('ready') else '33' if state.get('mode') == 'blocked' else '31'
        lines[0] = f'\x1b[{tone}m' + lines[0] + '\x1b[0m'
        text = '\n'.join(lines)
    return text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('state_tool')
    parser.add_argument('--max-age', type=int, default=120)
    parser.add_argument('--verification-max-age', type=int, default=420)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--plain', action='store_true')
    parser.add_argument('--no-color', action='store_true')
    parser.add_argument('--proxy-only', action='store_true')
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('network_state', args.state_tool)
    source = importlib.util.module_from_spec(spec); spec.loader.exec_module(source)
    state = source.readiness(target='auto', maximum=args.max_age, verification_maximum=args.verification_max_age)
    state['proxy_only'] = args.proxy_only
    if args.json:
        print(json.dumps(state, ensure_ascii=False))
    else:
        print(render(state, shutil.get_terminal_size((88, 24)).columns,
                     sys.stdout.isatty() and not args.no_color and 'NO_COLOR' not in os.environ, args.plain))


if __name__ == '__main__':
    main()
