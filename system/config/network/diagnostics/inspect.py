"""Collect local performance evidence. No probes, sudo, or configuration writes."""

import json
import argparse
import importlib.util
from pathlib import Path
import subprocess
import sys
import time


def command(argv):
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=5, check=False)
        error = None
        if result.returncode:
            detail = result.stderr[:4096].lower()
            if any(token in detail for token in ('permission denied', 'operation not permitted', 'access denied', 'not authorized')):
                error = 'permission-denied'
            elif any(token in detail for token in ('no such', 'does not exist', 'cannot find device')):
                error = 'not-found'
            elif any(token in detail for token in ('failed to connect', 'connection refused', 'not running')):
                error = 'service-unavailable'
            else:
                error = 'command-failed'
        return {"available": result.returncode == 0, "output": result.stdout.strip(),
                "returncode": result.returncode,
                # Diagnostics are fixed local commands. Avoid returning arbitrary
                # stderr which may contain unrelated service configuration.
                "error": error}
    except subprocess.TimeoutExpired:
        return {"available": False, "output": "", "returncode": None, "error": "timeout"}
    except OSError as error:
        return {"available": False, "output": "", "returncode": None,
                "error": "permission-denied" if isinstance(error, PermissionError) else
                         "command-missing" if isinstance(error, FileNotFoundError) else "os-error"}


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def drop_groups(settings):
    # These are policy categories, not per-flow attribution. Reuse anonymous
    # counters on existing drops; never depend on handles or include accepts.
    groups = [
        ('lf_lease_output', 'authorization-expired-output', 'host-output-and-guest-replies'),
        ('lf_lease_input', 'authorization-expired-input', 'host-input-and-tailnet'),
        ('lf_lease_forward', 'authorization-expired-forward', 'routed-guests'),
        ('lf_lease_ingress', 'authorization-expired-ingress', 'routed-guests'),
        ('lf_blocked_guard', 'mode-c-output', 'host-output-and-guest-replies'),
        ('lf_tailnet_guard', 'mode-c-input', 'host-input-and-tailnet'),
        ('lf_strict_reply_guard', 'strict-reply-policy', 'strict-browser'),
        ('lf_guest_guard', 'guest-mode-policy', 'routed-guests'),
        ('proxymode_forward', 'guest-forward-policy', 'routed-guests'),
        ('proxymode_drops', 'host-uplink-policy', 'host-output'),
        ('proxymode_tail', 'host-interface-policy', 'host-output'),
        ('lf_host_direct', 'host-private-policy', 'host-output'),
        ('dns_egress', 'dns-lease-policy', 'dnsmasq'),
        ('lf_dns_guard', 'dns-plaintext-policy', 'core'),
        ('lf_dns_egress_guard', 'dns-encrypted-policy', 'host-output'),
        ('lf_local_dns_guard', 'dns-client-policy', 'host-applications'),
        ('lf_local_dns_guard', 'dns-listener-identity', 'local-dns-listeners'),
        ('lf_local_dns_guard', 'dns-local-policy', 'local-dns'),
        ('lf_local_proxy_guard', 'proxy-listener-identity', 'local-proxy-listeners'),
        ('lf_proxy_inlet_guard', 'proxy-inlet-access', 'host-applications'),
        ('lf_proxy_inlet_ingress', 'proxy-inlet-access', 'non-loopback-ingress'),
        ('lf_socket_guest_private', 'socket-guest-target-policy', 'socket-guests'),
        ('lf_socket_guest_dns_guard', 'socket-guest-dns-policy', 'socket-guests'),
        ('lf_socket_guest_dns_after_nat', 'socket-guest-dns-path', 'socket-guests'),
    ]
    for index, interface in enumerate(settings['policy']['guests']['interfaces']):
        groups.extend((f'lf_guest_{suffix}_{index}', reason, interface) for suffix, reason in (
            ('from', 'guest-source-policy'), ('from', 'guest-target-policy'),
            ('host', 'guest-host-service-policy'), ('return', 'guest-reply-target-policy'),
            ('to', 'guest-reply-identity'), ('host_reply', 'guest-host-reply-policy')))
    if settings['policy'].get('runtimeRouting'):
        groups.extend([('lf_routing_input', 'routing-input-policy', 'strict-host-replies'),
                       ('lf_routing_output', 'routing-output-policy', 'strict-host-and-forwarded-egress')])
    return groups


def summarize_drops(document, settings):
    entries = document['nftables']
    if not isinstance(entries, list) or not any(
        entry.get('table', {}).get('family') == 'inet' and entry['table'].get('name') == 'lf_filter'
        for entry in entries
    ):
        raise ValueError('invalid nft snapshot')
    groups = {(chain, reason): dict(chain=chain, reason=reason, role=role, installed=False,
              counter_rules=0, uncounted_rules=0, packets=0, bytes=0)
              for chain, reason, role in drop_groups(settings)}
    by_chain = {}
    for chain, reason in groups:
        by_chain.setdefault(chain, []).append(reason)
    for entry in entries:
        chain = entry.get('chain', {})
        rule = entry.get('rule', {})
        record = chain or rule
        name = record.get('name') if chain else record.get('chain')
        table = 'lf_routing' if name in ('lf_routing_input', 'lf_routing_output') else 'lf_filter'
        if record.get('family') != 'inet' or record.get('table') != table or name not in by_chain:
            continue
        for reason in by_chain[name]:
            groups[name, reason]['installed'] = True
        expressions = rule.get('expr', [])
        if not any('drop' in expression for expression in expressions):
            continue
        fields = {expression.get('match', {}).get('left', {}).get('payload', {}).get('field')
                  for expression in expressions if isinstance(expression.get('match', {}).get('left', {}), dict)}
        if name == 'lf_local_dns_guard':
            reason = ('dns-listener-identity' if 'sport' in fields else
                      'dns-client-policy' if 'dport' in fields else 'dns-local-policy')
        elif name.startswith('lf_guest_from_'):
            missing_source_family = any(expression.get('match', {}).get('left', {}).get('meta', {}).get('key') == 'nfproto'
                for expression in expressions if isinstance(expression.get('match', {}).get('left', {}), dict))
            reason = 'guest-source-policy' if 'saddr' in fields or missing_source_family else 'guest-target-policy'
        else:
            reason = by_chain[name][0]
        group = groups[name, reason]
        counters = [expression['counter'] for expression in expressions if 'counter' in expression]
        group['uncounted_rules'] += int(not counters)
        group['counter_rules'] += int(bool(counters))
        for counter in counters:
            for key in ('packets', 'bytes'):
                value = counter[key]
                if type(value) is not int or value < 0:
                    raise ValueError('invalid counter')
                group[key] += value
    for group in groups.values():
        if not group['installed']:
            group.update(packets=None, bytes=None)
    return list(groups.values())


def collect_drops(settings):
    path = '/run/proxy-mode/state.json'
    before = read_json(path)
    snapshot = command([settings['nft'], '-j', 'list', 'table', 'inet', 'lf_filter'])
    routing_snapshot = command([settings['nft'], '-j', 'list', 'table', 'inet', 'lf_routing']) \
        if settings['policy'].get('runtimeRouting') else None
    after = read_json(path)
    fields = ('mode', 'reason', 'policy_version', 'revision', 'boot_id',
              'routing_epoch', 'routing_policy_digest', 'routing_phase')
    policy = {key: after.get(key) for key in fields} if isinstance(after, dict) else None
    result = dict(available=False, error=snapshot['error'], policy=policy,
                  state_consistent=isinstance(before, dict) and isinstance(after, dict)
                  and before.get('mode') in {'direct', 'proxy', 'blocked'}
                  and all(before.get(key) == after.get(key) for key in fields),
                  scope='aggregate-drop-counters', groups=[])
    if snapshot['available']:
        try:
            if len(snapshot['output']) > 2 * 1024 * 1024:
                raise ValueError('oversized nft snapshot')
            document = json.loads(snapshot['output'])
            if routing_snapshot is not None:
                if not routing_snapshot['available'] or len(routing_snapshot['output']) > 2 * 1024 * 1024:
                    raise ValueError('routing snapshot unavailable')
                document['nftables'].extend(json.loads(routing_snapshot['output'])['nftables'])
            result['groups'] = summarize_drops(document, settings)
            result.update(available=True, error=None)
        except (ValueError, KeyError, TypeError, AttributeError):
            result['error'] = 'invalid-snapshot'
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("settings")
    parser.add_argument("--sample-seconds", type=int, choices=range(1, 61))
    view = parser.add_mutually_exclusive_group()
    view.add_argument("--policy-only", action="store_true")
    view.add_argument("--drops", action="store_true")
    view.add_argument("--posture", action="store_true", help="show listeners and trust settings")
    parser.add_argument("--json", action="store_true", help="output the posture view as JSON")
    parser.add_argument("--privileged", action="store_true", help="include selected units' startup environment keys")
    parser.add_argument("--pid", type=int, action="append", default=[], help="inspect selected startup environment keys")
    parser.add_argument("--deep", action="store_true")
    args = parser.parse_args()
    settings = read_json(args.settings)
    if (args.json or args.privileged or args.pid) and not args.posture:
        parser.error('--json, --privileged and --pid require --posture')
    if args.posture:
        if args.sample_seconds or args.deep:
            parser.error('--posture cannot be combined with --sample-seconds or --deep')
        if len(args.pid) > 8 or any(pid <= 0 for pid in args.pid):
            parser.error('--pid accepts up to eight positive process IDs')
        spec = importlib.util.spec_from_file_location('network_posture', settings['posture']['script'])
        posture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(posture)
        report = posture.collect(settings, command, read_json, pids=args.pid, privileged=args.privileged)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print('\n'.join(posture.format_report(report)))
        return 0
    if args.drops:
        if args.sample_seconds or args.deep:
            parser.error('--drops cannot be combined with --sample-seconds or --deep')
        result = collect_drops(settings)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['available'] else 1
    spec = importlib.util.spec_from_file_location('network_state', settings['state_tool'])
    state = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(state)
    if args.policy_only:
        policy = dict(settings['policy'])
        policy['routing'] = state.routing_view()
        current = state.readiness(target='auto')
        policy['effective'] = dict(routing=current.get('routing_applied', 'unknown'),
            core_mode=current.get('core_mode', 'unknown'), capabilities=current.get('capabilities', {}),
            sampled_egress_identity=current.get('sampled_egress_identity'),
            native_egress='proxy-only-minimal' if current.get('routing_applied') == 'proxy-only'
                else 'configured-rules' if current.get('routing_applied') == 'rules' else 'unknown')
        policy['profile_refresh'] = state.profile_refresh(history=True)
        print(json.dumps(policy, ensure_ascii=False, indent=2))
        return
    service_args = [settings["systemctl"], "show", "clash-verge.service", "gost-relay.service",
                    "proxy-mode.service", "dns-upstream.service", "-p", "Id", "-p", "CPUUsageNSec",
                    "-p", "MemoryCurrent", "-p", "NRestarts"]
    first = command(service_args) if args.sample_seconds else None
    started = time.monotonic()
    result = {"collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "interfaces": {}}
    for interface in settings["interfaces"]:
        result["interfaces"][interface] = {
            "link": command([settings["ip"], "-j", "-s", "link", "show", "dev", interface]),
            "qdisc": command([settings["tc"], "-s", "-j", "qdisc", "show", "dev", interface]),
            "driver": command([settings["ethtool"], "-i", interface]),
            "offloads": command([settings["ethtool"], "-k", interface]),
            "queues": command([settings["ethtool"], "-l", interface]),
            "statistics": command([settings["ethtool"], "-S", interface]),
        }
    for name, path in {"policy": "/run/proxy-mode/state.json", "health": "/run/proxy-mode/health.json",
                       "heartbeat": "/run/proxy-mode/heartbeat.json", "dns": "/run/dns-upstream/state.json",
                       "verification": "/run/netsec/state.json", "relay": "/run/gost-relay/state.json",
                       "dns_permissions": "/run/dns-policy/state.json",
                       "health_failure": "/run/proxy-mode/health-failure.json"}.items():
        result[name] = read_json(path)
    if args.sample_seconds:
        time.sleep(max(0, args.sample_seconds - (time.monotonic() - started)))
    result["services"] = command(service_args)
    result["drops"] = collect_drops(settings)
    result['routing'] = state.routing_view()
    result["profile_refresh"] = state.profile_refresh(history=True)
    if args.deep:
        result['core_recovery'] = {
            'service': command([settings['systemctl'], 'show', 'clash-verge.service', '-p', 'NRestarts']),
            'logs': 'sudo journalctl -b -u clash-verge.service -u profile-refresh-verify.service -n 60'}
    if settings.get("profiles"):
        result["auto_update"] = state.auto_update(settings["profiles"])
        result["profile_vs_runtime"] = state.profile_vs_runtime(settings["profiles"])
    result["alerts"] = [read_json(path) for path in sorted(Path("/run/netsec/alerts").glob("*.json"))]
    if first and first["available"] and result["services"]["available"]:
        def samples(value):
            records = [dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
                       for block in value["output"].split("\n\n")]
            return {record["Id"]: record for record in records if "Id" in record}
        before, after = samples(first), samples(result["services"])
        elapsed = time.monotonic() - started
        result["sample"] = {"seconds": elapsed, "cpu_percent_of_one_logical_cpu": {
            unit: round((int(value["CPUUsageNSec"]) - int(before[unit]["CPUUsageNSec"])) / elapsed / 1e7, 3)
            for unit, value in after.items() if unit in before and value.get("CPUUsageNSec", "").isdigit()
            and before[unit].get("CPUUsageNSec", "").isdigit()
            and value.get("NRestarts") == before[unit].get("NRestarts")}}
    for name in ("softirqs", "net/softnet_stat"):
        try:
            result[name] = Path("/proc", name).read_text()
        except OSError:
            result[name] = None
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
