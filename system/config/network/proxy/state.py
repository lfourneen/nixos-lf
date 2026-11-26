"""One readiness contract for CLI, desktop status and automated consumers."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time


def read(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def fresh(stamp, now, maximum):
    if type(stamp) not in (int, float):
        return False
    try:
        return math.isfinite(stamp) and stamp >= 0 and 0 <= now - stamp <= maximum
    except OverflowError:
        return False


REFRESH_RESULTS = ('accepted', 'fallback', 'rolled_back', 'refused', 'verifying', 'restored')
REFRESH_REASONS = ('configuration invalid', 'input unreadable', 'validation timeout',
                   'snapshot mismatch', 'blocked by audit', 'audit indeterminate',
                   'operator authorization required', 'recent health failure', 'unknown', 'node', 'tun', 'dns', 'obs', 'application')
FETCH_REASONS = frozenset(('dns lookup failed', 'certificate rejected', 'tls failed',
    'connection timeout', 'connection refused', 'connection reset', 'network unreachable',
    'http rejected', 'encoding unsupported', 'redirect refused', 'destination refused',
    'response limit', 'yaml invalid', 'configuration unsupported', 'node resolution failed', 'worker failure',
    'selected egress unavailable', 'network protection active', 'waiting for healthy proxy', 'waiting for stable proxy'))
CACHE_RESULTS = ('eligible', 'no-download', 'already-accepted', 'registration-changed', 'policy-mismatch', 'damaged-cache')
AUDIT_CATEGORIES = frozenset((
    'yaml_structure', 'private_server', 'certificate', 'empty_nodes', 'group_reference',
    'hosts_override', 'provider_change', 'rule_provider_change', 'sensitive_route',
    'protected_route', 'route_unknown', 'rule_volume', 'node_volume', 'host_concentration',
    'plaintext_credentials', 'node_identity', 'node_authentication', 'node_transport', 'broad_route', 'audit_error'))


def boot_identity(root=Path('/run')):
    try:
        fd = os.open(Path(root) / 'proxy-mode/boot-id', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            owner = 0 if Path(root) == Path('/run') else os.geteuid()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
                return ''
            value = stream.read(80).strip()
        if re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value):
            return value
    except (OSError, ValueError):
        pass
    return ''


def portal_authority(root=Path('/run'), maximum=120, verification_maximum=420):
    """Return one verified explicit A grant without depending on public DNS."""
    root = Path(root)
    now = time.clock_gettime(time.CLOCK_BOOTTIME)
    boot = boot_identity(root)
    directory = root / 'proxy-mode'
    policy = read(directory / 'state.json')
    heartbeat = read(directory / 'heartbeat.json')
    verification = read(root / 'netsec/state.json')
    version = policy.get('policy_version', policy.get('version'))
    if (not boot or type(version) is not int or version < 1 or policy.get('mode') != 'direct'
            or heartbeat.get('mode') != 'direct' or heartbeat.get('policy_version') != version
            or not fresh(heartbeat.get('monotonic_seconds'), now, maximum)
            or verification.get('boot_id') != boot or verification.get('version') != version
            or verification.get('result') not in ('ok', 'degraded')
            or not fresh(verification.get('monotonic_seconds'), now, verification_maximum)
            or (root / 'netsec/failed').exists()):
        return None
    try:
        if (directory / 'intent').read_text().strip() == 'direct':
            return boot, version
    except (OSError, ValueError):
        pass
    return None


def clean_audit(value):
    if not isinstance(value, dict) or value.get('verdict') not in (
            'pass', 'warn', 'deny', 'unknown'):
        return None
    result = {'verdict': value['verdict']}
    for field in ('deny', 'warn', 'unknown'):
        counters = value.get(field)
        if not isinstance(counters, dict):
            return None
        kept = {}
        for key, count in counters.items():
            if key not in AUDIT_CATEGORIES or type(count) is not int or not 0 <= count <= 10000:
                return None
            kept[str(key)] = count
        result[field] = kept
    automatic = clean_auto_approval(value.get('auto_approval'))
    if automatic:
        result['auto_approval'] = automatic
    return result


def clean_auto_approval(value, now=None):
    if not isinstance(value, dict):
        return None
    categories, nodes = value.get('categories'), value.get('nodes')
    if (not isinstance(categories, dict) or not categories or not set(categories) <= {
            'node_identity', 'node_authentication', 'node_transport'} or any(
            type(count) is not int or not 1 <= count <= 10000 for count in categories.values()) or
            type(nodes) is not int or not max(categories.values()) <= nodes <= min(10000, sum(categories.values())) or
            not fresh(value.get('at'), time.time() if now is None else now, 365 * 86400)):
        return None
    return dict(categories=categories, nodes=nodes, at=value['at'])


def source_trust(root, boot):
    value = read(Path(root) / 'proxy-mode/source.json')
    if value.get('boot_id') != boot or value.get('provenance') not in ('controlled-fetch', 'legacy-unverified'):
        return {'provenance': 'unknown', 'upstream_signature': 'not-verified'}
    revision = value.get('revision', '')
    source_id = value.get('source_id', '')
    dropped = value.get('dropped')
    counts = value.get('drop_counts', {})
    if (not isinstance(counts, dict) or set(counts) - {'unresolved', 'loopback', 'unapproved_tls', 'unapproved_transport'}
            or any(type(count) is not int or not 0 <= count <= 10000 for count in counts.values())
            or sum(counts.values()) != dropped):
        counts = {}
    return {'provenance': value['provenance'],
            'source_id': source_id if isinstance(source_id, str) and re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', source_id) else '',
            'revision': revision[:12] if isinstance(revision, str) and re.fullmatch('[0-9a-f]{64}', revision) else '',
            'dropped': dropped if type(dropped) is int and 0 <= dropped <= 10000 else 0,
            'drop_counts': counts,
            'upstream_signature': 'not-verified'}


def source_fetch(root=Path('/run'), boot=None, now=None):
    directory = Path(root) / 'proxy-mode'
    value = read(directory / 'source-fetch.json')
    boot = boot_identity(root) if boot is None else boot
    if (not boot or value.get('boot_id') != boot or
            value.get('result') not in ('fetching', 'downloaded', 'failed', 'deferred', 'discarded') or
            not fresh(value.get('at'), time.time() if now is None else now, 7 * 86400)):
        return None
    result = {key: value[key] for key in ('at', 'result')}
    if value.get('phase') in ('fetch', 'parse', 'render', 'pin-addresses', 'worker'):
        result['phase'] = value['phase']
    if value.get('reason') in FETCH_REASONS:
        result['reason'] = value['reason']
    status = value.get('http_status')
    if type(status) is int and 100 <= status <= 599:
        result['http_status'] = status
    if result['result'] == 'downloaded':
        application = read(directory / 'source-apply-status.json')
        if application.get('boot_id') == boot and application.get('at') == result['at']:
            if application.get('result') in ('pending', 'discarded'):
                result['application'] = application['result']
    return result


def clean_refresh(value, now, maximum, full_digests=False):
    if not isinstance(value, dict) or not fresh(value.get('at'), now, maximum):
        return None
    if value.get('policy') not in ('strict', 'auto') or value.get('result') not in REFRESH_RESULTS:
        return None
    if value.get('reason') is not None and value.get('reason') not in REFRESH_REASONS:
        return None
    result = {key: value[key] for key in ('at', 'result', 'policy', 'reason')}
    for key in ('digest', 'previous_digest'):
        digest = value.get(key, '')
        if not isinstance(digest, str) or not re.fullmatch(r'(?:[0-9a-f]{64})?', digest):
            return None
        result[key] = digest if full_digests else digest[:12]
    result['snapshot_at'] = value.get('snapshot_at') if fresh(
        value.get('snapshot_at'), now, 365 * 86400) else 0
    accepted = value.get('last_accepted_at')
    result['last_accepted_at'] = accepted if fresh(accepted, now, 365 * 86400) else 0
    audit = clean_audit(value.get('audit'))
    if audit is not None:
        result['audit'] = audit
    automatic = clean_auto_approval(value.get('last_auto_approval'), now)
    if automatic:
        result['last_auto_approval'] = automatic
    return result


def profile_refresh(root=Path('/run'), now=None, boot=None, maximum=7 * 86400, history=False,
                    full_digests=False):
    """Public allowlist: never forward arbitrary strings or private paths."""
    now = time.time() if now is None else now
    boot = boot_identity(root) if boot is None else boot
    if not boot:
        return None
    value = read(Path(root) / 'proxy-mode/profile-refresh.json')
    if value.get('boot_id') != boot:
        return None
    result = clean_refresh(value, now, maximum, full_digests)
    if result is None:
        return None
    if history:
        entries = value.get('history')
        cleaned = []
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and 'policy' not in entry:
                entry = dict(entry, policy=value.get('policy'))
            item = clean_refresh(entry, now, maximum)
            if item is not None:
                cleaned.append(item)
        result['history'] = cleaned[-20:]
    return result


def effective_digest(root=Path('/run')):
    value = read(Path(root) / 'proxy-mode/effective-config.json')
    boot = boot_identity(root)
    digest = value.get('yaml_digest')
    if boot and value.get('boot_id') == boot and isinstance(digest, str) \
            and re.fullmatch(r'[0-9a-f]{64}', digest):
        return digest
    return ''


def gui_config_digest(profiles):
    """Digest the GUI YAML with the same controller normalization as stage."""
    path = Path(profiles).parent / 'clash-verge.yaml'
    try:
        text = path.read_bytes().decode('utf-8')
    except (OSError, UnicodeError):
        return ''
    normalized, count = re.subn(
        r'^external-controller-unix:.*$',
        'external-controller-unix: /run/clash-verge-rev/core/mihomo.sock',
        text, flags=re.MULTILINE)
    if count != 1:
        return ''
    return hashlib.sha256(normalized.encode()).hexdigest()


def profile_vs_runtime(profiles, root=Path('/run')):
    gui = gui_config_digest(profiles)
    runtime = effective_digest(root)
    if not gui or not runtime:
        return {'state': 'unknown', 'gui': gui[:12], 'runtime': runtime[:12]}
    return {'state': 'in sync' if gui == runtime else 'differs',
            'gui': gui[:12], 'runtime': runtime[:12]}


def gui_running(uid=None):
    uid = os.getuid() if uid is None else uid
    try:
        entries = list(Path('/proc').iterdir())
    except OSError:
        return None
    seen = 0
    for entry in entries:
        if not entry.name.isdigit():
            continue
        seen += 1
        if seen > 8192:
            break
        try:
            status = (entry / 'status').read_text()
            match = re.search(r'^Uid:\s+(\d+)', status, re.MULTILINE)
            if match is None or int(match.group(1)) != uid:
                continue
            argv0 = os.fsdecode((entry / 'cmdline').read_bytes().split(b'\0', 1)[0])
        except OSError:
            continue
        if Path(argv0).name in ('clash-verge', '.clash-verge-wrapped', '.clash-verge-launcher'):
            return True
    return False


def profile_index(profiles):
    try:
        import yaml
    except ImportError:
        return None
    try:
        value = yaml.safe_load(Path(profiles).read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, yaml.YAMLError):
        return None


def auto_update(profiles, now=None, running=None):
    document = profile_index(profiles)
    if document is None:
        return {'state': 'unknown'}
    current = document.get('current')
    items = document.get('items')
    item = None
    for candidate in items if isinstance(items, list) else []:
        if isinstance(candidate, dict) and candidate.get('uid') == current \
                and candidate.get('type') == 'remote':
            item = candidate
            break
    if item is None:
        return {'state': 'unknown'}
    option = item.get('option') if isinstance(item.get('option'), dict) else {}
    interval = option.get('update_interval')
    allow = option.get('allow_auto_update')
    updated = item.get('updated')
    now = time.time() if now is None else now
    running = gui_running() if running is None else running
    result = {'interval': interval if type(interval) is int and interval > 0 else None,
              'allow_auto_update': allow is not False,
              'updated': updated if fresh(updated, now, 3650 * 86400) and updated > 0 else None,
              'gui_running': running}
    if not result['allow_auto_update']:
        result['state'] = 'timer-off'
        result['reason'] = 'allow_auto_update=false'
        return result
    if result['interval'] is None:
        result['state'] = 'timer-off'
        result['reason'] = 'no interval'
        return result
    if result['updated'] is None:
        result['state'] = 'unknown'
        return result
    result['next_due'] = int(result['updated'] + result['interval'] * 60)
    if now > result['next_due'] + 600:
        result['state'] = 'overdue'
    elif running is False:
        result['state'] = 'gui-closed'
    elif running is None:
        result['state'] = 'unknown'
    else:
        result['state'] = 'ok'
    return result


def subscription_health(profiles, refresh=None, now=None, fetch=None):
    """Read only local update timestamps; subscriptions are never fetched here."""
    now = time.time() if now is None else now
    document = profile_index(profiles)
    last_update = None
    if isinstance(document, dict):
        current = document.get('current')
        for item in document.get('items') if isinstance(document.get('items'), list) else []:
            if isinstance(item, dict) and item.get('uid') == current \
                    and item.get('type') == 'remote':
                stamp = item.get('updated')
                if fresh(stamp, now, 3650 * 86400):
                    last_update = stamp
                break
    accepted = refresh.get('last_accepted_at') if refresh else 0
    reference = max([value for value in (last_update, accepted) if value] or [0]) or None
    failed = bool(refresh and refresh['result'] in ('fallback', 'rolled_back', 'refused'))
    failed |= bool(fetch and fetch['result'] == 'failed' and fetch['at'] >= (refresh or {}).get('at', 0))
    return dict(stale=reference is not None and now - reference > 3 * 86400, update_failed=failed,
                last_update=last_update, last_accepted=accepted or None,
                age_days=int((now - reference) // 86400) if reference is not None else None)


def routing_view(root=Path('/run'), policy=None, boot_id=None):
    root = Path(root)
    policy = read(root / 'proxy-mode/state.json') if policy is None else policy
    path = root / 'proxy-mode/routing.json'
    required = 'routing_schema' in policy or path.exists()
    if not required:
        return {}
    result = dict(routing_schema=1, routing_requested='unknown', routing_applied='unknown',
                  routing_epoch=None, routing_policy_digest=None, routing_phase='unknown', routing_consistent=False)
    try:
        info = path.lstat()
        value = read(path)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 \
                or value.get('schema') != 1 or value.get('boot_id') != (boot_id or boot_identity(root)) \
                or value.get('routing_requested') not in ('rules', 'proxy-only') \
                or value.get('routing_applied') not in ('rules', 'proxy-only', None) \
                or value.get('routing_phase') not in ('preparing', 'verifying', 'ready', 'failed', 'inactive') \
                or type(value.get('routing_epoch')) is not int or value['routing_epoch'] < 1 \
                or not isinstance(value.get('routing_policy_digest'), str) \
                or not re.fullmatch('[a-f0-9]{64}', value['routing_policy_digest']):
            return result
        managed = (root / 'proxy-mode/routing-active/policy.json').read_bytes()
        if hashlib.sha256(managed).hexdigest() != value['routing_policy_digest']:
            return result
        tunnel = bool(json.loads(managed).get('lf-ip-tunnel'))
    except (OSError, ValueError, AttributeError):
        return result
    keys = ('routing_requested', 'routing_applied', 'routing_epoch', 'routing_policy_digest', 'routing_phase')
    if 'egress_epoch' in value:
        if (type(value['egress_epoch']) is not int or value['egress_epoch'] < 0
                or value.get('egress_requested') is not None and not re.fullmatch('[a-f0-9]{64}', str(value['egress_requested']))):
            return result
        keys += ('egress_requested', 'egress_epoch')
    result.update({key: value[key] for key in keys})
    if tunnel:
        result['routing_transport'] = 'wireguard'
    result['routing_consistent'] = all(policy.get(key) == value[key] for key in keys)
    return result


UDP_PROBE_RESULTS = ('verified', 'no-reply', 'error')
UDP_PROBE_SETTINGS = Path('/etc/proxy-udp-health.json')


def udp_probe_binding(root, boot, version, routing, source):
    if (not boot or not routing.get('routing_consistent') or routing.get('routing_applied') != 'proxy-only'
            or routing.get('routing_phase') != 'ready' or not routing.get('egress_requested')
            or source.get('boot_id') != boot or not source.get('snapshot')):
        return None
    try:
        epoch = (root / 'proxy-mode/authorization-epoch').read_text().strip()
    except FileNotFoundError:
        epoch = '0'
    except OSError:
        return None
    try:
        settings = hashlib.sha256(UDP_PROBE_SETTINGS.read_bytes()).hexdigest()
    except OSError:
        return None
    return dict(boot=boot, version=version, epoch=epoch, snapshot=source['snapshot'], settings=settings, **{
        key: routing[key] for key in ('routing_epoch', 'routing_policy_digest', 'egress_epoch', 'egress_requested')
    })


def readiness(root=Path("/run"), target="proxy", maximum=120, now=None,
              verification_maximum=420, boot_id=None, routing_target=None, routing_epoch=None):
    root = Path(root)
    now = time.clock_gettime(time.CLOCK_BOOTTIME) if now is None else now
    if boot_id is None:
        boot_id = boot_identity(root)
    policy = read(root / "proxy-mode/state.json")
    heartbeat = read(root / "proxy-mode/heartbeat.json")
    dns = read(root / "dns-upstream/state.json")
    permissions = read(root / "dns-policy/state.json")
    relay = read(root / "gost-relay/state.json")
    verification = read(root / "netsec/state.json")
    if target == "auto":
        try:
            target = (root / "proxy-mode/intent").read_text().strip()
        except OSError:
            target = "direct" if policy.get("mode") == "direct" else "proxy"
        if target not in {"proxy", "direct"}:
            target = "proxy"
    version = policy.get("policy_version", policy.get("version", -1))
    reasons = []
    routing = routing_view(root, policy, boot_id)
    if routing and target == 'proxy':
        if not routing['routing_consistent']:
            reasons.append('routing authority differs from applied policy')
        if routing['routing_phase'] != 'ready' or routing['routing_applied'] != routing['routing_requested']:
            reasons.append('routing transaction pending or failed')
        if routing_target is not None and routing['routing_applied'] != routing_target:
            reasons.append('another routing target is active')
        if routing_epoch is not None and routing['routing_epoch'] != routing_epoch:
            reasons.append('another routing epoch is active')
    for name, value in [("heartbeat", heartbeat), ("DNS", dns), ("relay", relay),
                        ("DNS leases", permissions)]:
        stamp = value.get("monotonic_seconds", -1)
        if not fresh(stamp, now, maximum):
            reasons.append(name + " observation stale")
        if value.get("policy_version", -2) != version:
            reasons.append(name + " policy generation differs")
    if policy.get("mode") != target:
        reasons.append("policy is not " + target)
    directory = root / 'proxy-mode'
    authorization = read(directory / 'authorization.json')
    source = read(directory / 'source.json')
    lease_required = target == 'proxy' and policy.get('authorization_lease_seconds') is not None
    until = authorization.get('until', 0)
    lease_active = (type(until) in (int, float) and math.isfinite(until) and until > now
                    and authorization.get('boot_id') == boot_id
                    and authorization.get('policy_version') == version)
    if lease_active and 'authorization_epoch' in authorization:
        try:
            epoch = (directory / 'authorization-epoch').read_text().strip()
        except FileNotFoundError:
            epoch = '0'
        except OSError:
            epoch = None
        lease_active = (authorization['authorization_epoch'] == epoch and not (root / 'dns-policy/sleeping').exists()
                        and (not source or (source.get('boot_id') == boot_id
                             and source.get('snapshot') == authorization.get('digest'))))
    if routing and target == 'proxy':
        lease_active = lease_active and all(authorization.get(key) == routing[key]
            for key in ('routing_epoch', 'routing_policy_digest'))
        if routing.get('routing_requested') == 'proxy-only' and 'egress_epoch' in routing:
            lease_active = lease_active and all(authorization.get(key) == routing[key]
                for key in ('egress_requested', 'egress_epoch'))
    if lease_required and not lease_active:
        reasons.append('B kernel authorization expired or pending')
    if heartbeat.get("mode") != target:
        reasons.append("heartbeat belongs to a different mode")
    if relay.get("mode") != target:
        reasons.append("relay is not " + target)
    if permissions.get("status") != "applied":
        reasons.append("DNS lease authorization is suspended or pending")
    if target == "proxy" and dns.get("mode") != "proxy":
        reasons.append("DNS is not proxy")
    if target == "direct" and dns.get("mode") not in {
        "dot", "dot-priming", "bootstrap-plaintext", "portal-plaintext", "degraded-plaintext"
    }:
        reasons.append("direct DNS unavailable")
    if target == "direct" and str(dns.get("mode", "")).endswith("plaintext") \
            and dns.get("window_until", 0) <= now and not dns.get("plaintext_override", False):
        reasons.append("plaintext window expired")
    # A degraded verdict is the verifier's warning channel: policy is installed
    # and every hard check passed, while an advisory observation did not. Only
    # a failed verdict raises the alarm. Missing/unknown verdicts, stale data,
    # generation/boot mismatches and active alarms still block readiness.
    verified_boot = verification.get('boot_id')
    if (verification.get("result") not in ("ok", "degraded") or verification.get("version") != version
            or not boot_id or not verified_boot):
        reasons.append("rules not verified for this generation")
    if not fresh(verification.get("monotonic_seconds"), now, verification_maximum):
        reasons.append("rules verification stale")
    if boot_id and verified_boot and verified_boot != boot_id:
        reasons.append("rules verification belongs to a different boot")
    if (root / "netsec/failed").exists():
        reasons.append("enforcement alarm active")
    observations = {}
    for name, value in [('heartbeat', heartbeat), ('dns', dns), ('relay', relay),
                        ('dns_leases', permissions), ('verification', verification)]:
        stamp = value.get('monotonic_seconds')
        observations[name] = {
            'fresh': fresh(stamp, now, verification_maximum if name == 'verification' else maximum),
            'age_seconds': round(now - stamp, 1) if fresh(stamp, now, 86400) else None,
            'generation_matches': value.get('version' if name == 'verification' else 'policy_version', -2) == version,
        }
    preflight = read(root / 'proxy-mode/preflight.json')
    if preflight.get('boot_id') != boot_id or not fresh(preflight.get('monotonic_seconds'), now, 86400):
        preflight = {}
    health = read(directory / 'health.json')
    health_current = health.get('boot_id') == boot_id and fresh(health.get('monotonic_seconds'), now, maximum)
    if source.get('boot_id') == boot_id and source.get('provenance') == 'controlled-fetch':
        health_current = health_current and health.get('digest') == source.get('snapshot')
    if routing:
        health_current = health_current and routing['routing_consistent'] and all(health.get(key) == routing[key]
            for key in ('routing_epoch', 'routing_policy_digest'))
        if routing.get('routing_requested') == 'proxy-only' and 'egress_epoch' in routing:
            health_current = health_current and all(health.get(key) == routing[key]
                for key in ('egress_requested', 'egress_epoch'))
    cache = read(directory / 'source-recovery-cache.json')
    cache_result = cache.get('result')
    if (not boot_id or cache.get('boot_id') != boot_id or not routing
            or not all(cache.get(key) == routing[key] for key in ('routing_epoch', 'routing_policy_digest'))
            or cache_result not in CACHE_RESULTS):
        cache_result = None
    restored = read(directory / 'profile-refresh.json')
    data_streak = health.get('startup_data_failure_streak')
    startup_recovery_needed = (target == 'proxy' and policy.get('mode') == 'blocked' and health_current
        and routing.get('routing_phase') == 'verifying' and not (root / 'netsec/failed').exists()
        and health.get('policy_version') == version and fresh(health.get('monotonic_seconds'), now, 35)
        and health.get('ok') is False and type(data_streak) is int and data_streak >= 2
        and source.get('boot_id') == restored.get('boot_id') == boot_id and restored.get('result') == 'restored'
        and restored.get('policy') == 'auto' and bool(health.get('digest'))
        and health['digest'] == restored.get('digest') == source.get('snapshot'))
    core_mode = health.get('core_mode') if health_current and health.get('core_mode') in ('rule', 'global', 'direct') else 'unknown'
    identity = (health.get('exit_identities') or {}).get('mixed') if health_current else None
    identity = identity[:12] if isinstance(identity, str) and re.fullmatch('[a-f0-9]{64}', identity) else None
    udp_declared = health.get('node_udp_supported') if health_current else None
    if type(udp_declared) is not bool:
        udp_declared = None
    udp = 'unsupported' if udp_declared is False else 'not-probed'
    if not reasons and target == 'proxy' and routing.get('routing_applied') == 'proxy-only':
        report = read(directory / 'udp-health.json')
        binding = udp_probe_binding(root, boot_id, version, routing, source)
        if (binding is not None and report.get('binding') == binding
                and report.get('result') in UDP_PROBE_RESULTS
                and fresh(report.get('monotonic_seconds'), now, 150)):
            udp = report['result']
    tunnel_health = health.get('ip_tunnel') if health_current and routing.get('routing_transport') == 'wireguard' else None
    return {**routing, "ready": not reasons, "target": target, "policy_version": version,
            'core_mode': core_mode, 'sampled_egress_identity': identity,
            **({'ip_tunnel_health': tunnel_health} if isinstance(tunnel_health, dict) else {}),
            'capabilities': {'dns': ('validated-via-proxy' if routing.get('routing_applied') == 'proxy-only'
                and health_current and health.get('real_dns_ok') is True else 'pending' if routing.get('routing_requested') == 'proxy-only'
                else 'policy-selected'), 'udp': udp,
                'udp_declared': udp_declared,
                'icmp': ('verified-dual-stack' if not reasons and health_current and health.get('ip_tunnel_ok') is True
                         else 'pending') if routing.get('routing_transport') == 'wireguard'
                    else 'blocked' if routing.get('routing_requested') == 'proxy-only' else 'not-probed',
                'egress': 'fixed-leaf' if routing.get('routing_requested') == 'proxy-only'
                    and routing.get('egress_requested') else 'policy-selected',
                'remote_nat': 'not-measured', 'ipv6_target': 'node-dependent'},
            "mode": policy.get("mode", "unknown"), "reasons": reasons,
            # A failed verdict is reported, not treated as an outage: Mode A keeps
            # a bounded plaintext window while the encrypted path is retried.
            "dns_mode": dns.get("mode", "unknown"),
            "authorization_lease": {'required': lease_required, 'active': lease_active,
                                    'remaining': max(0, round(until - now)) if lease_active else 0},
            "dns_verdict": dns.get("dnssec_status", "unknown"),
            "budget_remaining": dns.get("budget_remaining"),
            "window_until": dns.get("window_until", 0),
            "reason": policy.get("reason", "unknown"),
            "observations": observations,
            "verification": verification.get('result', 'unknown'),
            "alarm": (root / 'netsec/failed').exists(),
            "relay_mode": relay.get('mode', 'unknown'),
            "dns_lease_status": permissions.get('status', 'unknown'),
            "dns_encryption": dns.get('encryption_policy', 'unknown'),
            "dns_validation": ('local-dnssec' if dns.get('local_dnssec') is True else
                               'mode-a-local' if policy.get('mode') == 'direct' else 'encrypted-core'),
            "dns_local": dns.get('dnssec_probe', {}).get('local', {}).get('status', 'unknown'),
            "dns_transport": dns.get('dnssec_probe', {}).get('transport', {}).get('status', 'unknown'),
            "budget_total": dns.get('budget_total'),
            "plaintext_override": dns.get('plaintext_override', False),
            "window_remaining": max(0, round(dns.get('window_until', 0) - now)),
            "last_preflight": preflight,
            "last_proxy_health": health if health_current or not routing else {},
            "startup_recovery_needed": startup_recovery_needed,
            "source_recovery_cache": cache_result,
            "profile_refresh": profile_refresh(root, boot=boot_id),
            "source_fetch": source_fetch(root, boot=boot_id),
            "source_trust": source_trust(root, boot_id),
            "phase": "ready" if not reasons else
                     "unenforced" if policy.get("mode") == "unenforced" else
                     "protected" if policy.get("mode") == "blocked" else "pending",
            "next_action": "none" if not reasons else "proxy-control preflight" if target == "proxy"
                           else "inspect DNS and verification state"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=["proxy", "direct", "auto"])
    parser.add_argument("--max-age", type=int, default=120)
    parser.add_argument("--verification-max-age", type=int, default=420)
    parser.add_argument('--routing', choices=['rules', 'proxy-only'])
    parser.add_argument('--routing-epoch', type=int)
    args = parser.parse_args()
    result = readiness(target=args.target, maximum=args.max_age,
                       verification_maximum=args.verification_max_age,
                       routing_target=args.routing, routing_epoch=args.routing_epoch)
    print(json.dumps(result, separators=(",", ":")))
    raise SystemExit(0 if result["ready"] else 1)
