"""Human-readable view of the shared, read-only network readiness contract."""
import argparse
import importlib.util
import json
import os
import shutil
import sys
import time
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
APPROVAL_CATEGORIES = frozenset(('node_identity', 'node_authentication', 'node_transport', 'sensitive_route'))


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


def stamp(at):
    if not at:
        return 'unknown'
    moment, today = time.localtime(at), time.localtime()
    if (moment.tm_year, moment.tm_yday) == (today.tm_year, today.tm_yday):
        return time.strftime('%H:%M', moment)
    return time.strftime('%m-%d %H:%M', moment)


def auto_update_text(value):
    state = value.get('state')
    if state == 'controlled':
        return 'root · on startup and every 30m · GUI copy is display-only'
    if state == 'ok':
        return (f"every {value['interval']}m · last fetch {stamp(value['updated'])}"
                f" · next ~{stamp(value['next_due'])}")
    if state == 'timer-off':
        return 'disabled · ' + value.get('reason', 'no schedule')
    if state == 'gui-closed':
        return (f"GUI not running · last fetch {stamp(value['updated'])}"
                f" · next ~{stamp(value['next_due'])}")
    if state == 'overdue':
        return (f"overdue · last fetch {stamp(value['updated'])}"
                f" · expected {stamp(value['next_due'])}")
    return 'unknown'


def update_result_text(value):
    if not value:
        return 'unknown'
    reason = {'node': 'node health failed', 'tun': 'tun health failed',
              'dns': 'dns health failed', 'obs': 'probe observation failed'}.get(value['reason'], value['reason'])
    label = {'accepted': 'health accepted', 'verifying': 'waiting for health', 'restored': 'restored accepted snapshot', 'fallback': 'fallback',
             'rolled_back': 'rolled back', 'refused': 'refused'}[value['result']]
    if value['result'] in ('accepted', 'verifying', 'restored'):
        return f"{label} · {stamp(value['at'])}"
    return f"{label} · {reason} · {stamp(value['at'])}"


def runtime_text(value):
    state = value.get('state')
    return {'display-only': 'GUI is display-only · controlled source is authoritative',
            'in sync': 'in sync',
            'differs': 'differs · GUI and authorized snapshot contain different YAML'}.get(
                state, 'unknown')


def refresh_text(value):
    stamp = lambda at: time.strftime('%H:%M', time.localtime(at)) if at else 'unknown'
    policy, result = value['policy'], value['result']
    if policy == 'strict' and result == 'refused' and value['reason'] == 'operator authorization required':
        return 'strict · refused · run clash-on'
    reason = {'node': 'node health failed', 'tun': 'tun health failed', 'dns': 'dns health failed',
              'obs': 'probe observation failed'}.get(value['reason'], value['reason'])
    if result == 'accepted':
        return f"{policy} · accepted {stamp(value['at'])} · snapshot from {stamp(value['snapshot_at'])}"
    if result == 'restored':
        return f"{policy} · restored accepted snapshot from {stamp(value['snapshot_at'])}"
    if result == 'verifying':
        return f"{policy} · verifying · snapshot from {stamp(value['snapshot_at'])}"
    label = {'fallback': 'fallback', 'rolled_back': 'rolled back', 'refused': 'refused'}[result]
    return f"{policy} · {label} · {reason} · running snapshot from {stamp(value['snapshot_at'])}"


def audit_rows(findings, controlled):
    # These categories enter warn only when an exact semantic approval moves
    # them out of deny. Other warnings still need their original visibility.
    warnings = dict(findings.get('warn') or {})
    automatic = findings.get('auto_approval')
    approved = sum(warnings.pop(key, 0) for key in APPROVAL_CATEGORIES) if controlled else 0
    counts = []
    for values, label in ((findings.get('deny') or {}, 'denied'), (warnings, 'warnings'),
                          (findings.get('unknown') or {}, 'unresolved')):
        count = sum(values.values())
        if count:
            if label == 'warnings' and count == 1:
                label = 'warning'
            counts.append(f'{count} {label}')
    if counts:
        result = ' · '.join(counts)
    elif approved and findings.get('verdict') in ('pass', 'warn'):
        result = 'Auto-approved' if automatic else 'Approved'
    else:
        result = {'pass': 'Passed', 'warn': 'Warnings recorded', 'deny': 'Denied',
                  'unknown': 'Unresolved'}.get(findings.get('verdict'), 'Unknown')
    yield 'Content audit', result
    if approved and not automatic:
        yield 'Approved changes', f'{approved} · explicit approval for this update'
    for field, label in (('deny', 'Audit denied'), ('warn', 'Audit warnings'), ('unknown', 'Audit unresolved')):
        values = findings.get(field) or {}
        if values:
            yield label, ', '.join(f'{key}={count}' for key, count in sorted(values.items()))


def rows(state):
    ready, mode = state.get('ready', False), state.get('mode', 'unknown')
    yield 'Network status', ('Ready' if ready else 'Protected · waiting for recovery' if mode == 'blocked' else 'Not ready')
    yield 'Mode', MODES.get(mode, 'Unknown') + '; requested ' + MODES.get(state.get('target'), 'Unknown')
    auto = state.get('auto_update') or {}
    if auto:
        yield 'Auto update', auto_update_text(auto)
    trust = state.get('source_trust', {})
    yield 'Source provenance', trust.get('provenance', 'unknown')
    if trust.get('source_id'):
        yield 'Active source', trust['source_id']
    if trust.get('dropped'):
        yield 'Dropped leaves', f"{trust['dropped']} · unresolved, loopback or unapproved TLS"
    yield 'Publisher signature', 'Not checked · verification is not implemented'
    fetch = state.get('source_fetch') or {}
    if fetch:
        label = {'fetching': 'fetching', 'downloaded': 'downloaded · import and health are separate',
                 'failed': 'failed', 'deferred': 'deferred', 'discarded': 'request changed'}[fetch['result']]
        if fetch.get('application') == 'pending':
            label = 'downloaded · waiting to apply'
        elif fetch.get('application') == 'discarded':
            label = 'downloaded · application discarded'
        detail = fetch.get('reason') or fetch.get('phase') or ''
        if fetch.get('http_status'):
            detail += f" · HTTP {fetch['http_status']}"
        yield 'Subscription download', label + (' · ' + detail if detail else '') + ' · ' + stamp(fetch['at'])
    if state.get('profile_refresh'):
        findings = state['profile_refresh'].get('audit')
        if findings:
            yield from audit_rows(findings, trust.get('provenance') == 'controlled-fetch')
        yield 'Profile refresh', refresh_text(state['profile_refresh'])
        yield 'Update result', update_result_text(state['profile_refresh'])
        automatic = state['profile_refresh'].get('last_auto_approval')
        if automatic:
            categories = ', '.join(f'{key}={count}' for key, count in sorted(automatic['categories'].items()))
            yield 'Last auto approval', f"{automatic['nodes']} nodes · {categories} · {stamp(automatic['at'])}"
    runtime = state.get('profile_vs_runtime') or {}
    if runtime:
        yield 'Profile vs runtime', runtime_text(runtime)
    subscription = state.get('subscription_health', {})
    if subscription.get('update_failed'):
        yield 'Subscription update', ('latest update failed · previous acceptance retained'
            if subscription.get('last_accepted') else 'latest update failed · no accepted update observed')
    if subscription.get('stale'):
        age_days = subscription.get('age_days')
        yield 'Subscription health', ('stale · last successful activity ' + str(age_days) + 'd ago'
                                      if age_days is not None else 'stale · no accepted update observed')
    if mode == 'proxy' and state.get('routing_schema'):
        yield 'Routing requested', state.get('routing_requested', 'unknown')
        yield 'Routing applied', state.get('routing_applied') or 'Pending'
        yield 'Routing transaction', str(state.get('routing_phase', 'unknown')) + ' · epoch ' + str(state.get('routing_epoch', '?'))
        yield 'Core mode', state.get('core_mode', 'unknown')
        yield 'Sampled TCP leaf', state.get('sampled_egress_identity') or 'Not proven'
        capabilities = state.get('capabilities', {})
        udp = capabilities.get('udp', 'not-probed')
        if capabilities.get('udp_declared') is True:
            udp += ' (declared by node)'
        yield 'Protocol coverage', 'DNS: ' + capabilities.get('dns', 'unknown') + ' · UDP: ' + udp + ' · ICMP: ' + capabilities.get('icmp', 'not-probed')
    tunnel = state.get('ip_tunnel_health', {})
    if mode in ('proxy', 'blocked') and tunnel:
        yield 'WireGuard health', tunnel['stage'] + (' · handshake: ' + tunnel['handshake'] if 'handshake' in tunnel else '')
    if mode == 'proxy':
        yield 'Application egress', ('Uniform proxy routing' if state.get('routing_applied') == 'proxy-only'
            else 'Rule routing · DIRECT or proxy' if state.get('routing_applied') == 'rules'
            else 'Routing pending' if state.get('routing_schema')
            else 'Uniform proxy routing' if state.get('proxy_only') else 'Rule routing · DIRECT or proxy')
    elif mode == 'direct':
        yield 'Application egress', 'Direct · local Gost provides passthrough'
    elif mode == 'blocked':
        yield 'Application egress', 'Blocked · core recovery and link maintenance retained'
    if state.get('reason'):
        yield 'Transition reason', REASONS.get(str(state['reason']).split(':')[0], 'Inspect coordinator state with proxy-inspect')
    yield 'System DNS', DNS.get(state.get('dns_mode'), 'Unknown')
    if mode == 'proxy':
        yield 'DNS validation', ('Local DNSSEC · unsigned zones remain unsigned'
            if state.get('dns_validation') == 'local-dnssec' else 'Encrypted core resolvers')
        lease = state.get('authorization_lease', {})
        if lease.get('required'):
            yield 'Application authorization', (f"Kernel lease · {lease.get('remaining', 0)}s remaining"
                if lease.get('active') else 'Kernel lease expired or pending')
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
    for key, label, result in [('verification', 'Firewall check', {'ok': 'Passed', 'degraded': 'Passed · warnings'}.get(state.get('verification'), 'Not passed')),
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
        fields = [('data_plane_ok', 'Mixed HTTP'), ('route_ok', 'Route'),
                  ('dns_ok', 'DNS'), ('tun_ok', 'TUN')]
        if any(key in health for key, _ in fields):
            yield 'Probe layers', ' · '.join(label + ': ' +
                ('Passed' if health.get(key) is True else 'Failed' if health.get(key) is False else 'Not proven')
                for key, label in fields)
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
        yield 'Preparation action', 'Check controlled source registration (or legacy GUI profile), then run proxy-control preflight; parser details: sudo cat /run/proxy-mode/preflight.log'


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
    parser.add_argument('--controlled-source', action='store_true')
    parser.add_argument('--profiles', default=os.path.expanduser('~/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles.yaml'))
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('network_state', args.state_tool)
    source = importlib.util.module_from_spec(spec); spec.loader.exec_module(source)
    state = source.readiness(target='auto', maximum=args.max_age, verification_maximum=args.verification_max_age)
    state['proxy_only'] = state.get('routing_applied') == 'proxy-only' if state.get('routing_schema') else args.proxy_only
    state['subscription_health'] = source.subscription_health(
        '/nonexistent/controlled-source-profiles' if args.controlled_source else args.profiles, state.get('profile_refresh'),
        fetch=state.get('source_fetch'))
    state['auto_update'] = source.auto_update(args.profiles)
    state['profile_vs_runtime'] = source.profile_vs_runtime(args.profiles)
    if args.controlled_source:
        state['auto_update'] = {'state': 'controlled'}
        state['profile_vs_runtime'] = {'state': 'display-only'}
    if args.json:
        print(json.dumps(state, ensure_ascii=False))
    else:
        print(render(state, shutil.get_terminal_size((88, 24)).columns,
                     sys.stdout.isatty() and not args.no_color and 'NO_COLOR' not in os.environ, args.plain))


if __name__ == '__main__':
    main()
