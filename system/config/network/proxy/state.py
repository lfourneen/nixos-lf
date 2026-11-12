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


REFRESH_RESULTS = ('accepted', 'fallback', 'rolled_back', 'refused', 'verifying')
REFRESH_REASONS = ('configuration invalid', 'input unreadable', 'validation timeout',
                   'snapshot mismatch', 'blocked by audit', 'audit indeterminate',
                   'unknown', 'node', 'tun', 'dns')
AUDIT_CATEGORIES = frozenset((
    'yaml_structure', 'private_server', 'certificate', 'empty_nodes', 'group_reference',
    'hosts_override', 'provider_change', 'rule_provider_change', 'sensitive_route',
    'protected_route', 'route_unknown', 'rule_volume', 'node_volume', 'host_concentration',
    'plaintext_credentials', 'broad_route', 'audit_error'))


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
    except OSError:
        pass
    return ''


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


def subscription_health(profiles, refresh=None, now=None):
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
    return dict(stale=failed or (reference is not None and now - reference > 3 * 86400),
                last_update=last_update, last_accepted=accepted or None,
                age_days=int((now - reference) // 86400) if reference is not None else None)


def readiness(root=Path("/run"), target="proxy", maximum=120, now=None,
              verification_maximum=420, boot_id=None):
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
    for name, value in [("heartbeat", heartbeat), ("DNS", dns), ("relay", relay),
                        ("DNS leases", permissions)]:
        stamp = value.get("monotonic_seconds", -1)
        if not fresh(stamp, now, maximum):
            reasons.append(name + " observation stale")
        if value.get("policy_version", -2) != version:
            reasons.append(name + " policy generation differs")
    if policy.get("mode") != target:
        reasons.append("policy is not " + target)
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
    if verification.get("result") != "ok" or verification.get("version") != version:
        reasons.append("rules not verified for this generation")
    if not fresh(verification.get("monotonic_seconds"), now, verification_maximum):
        reasons.append("rules verification stale")
    if not boot_id or verification.get("boot_id") != boot_id:
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
    return {"ready": not reasons, "target": target, "policy_version": version,
            "mode": policy.get("mode", "unknown"), "reasons": reasons,
            # A failed verdict is reported, not treated as an outage: Mode A keeps
            # a bounded plaintext window while the encrypted path is retried.
            "dns_mode": dns.get("mode", "unknown"),
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
            "dns_local": dns.get('dnssec_probe', {}).get('local', {}).get('status', 'unknown'),
            "dns_transport": dns.get('dnssec_probe', {}).get('transport', {}).get('status', 'unknown'),
            "budget_total": dns.get('budget_total'),
            "plaintext_override": dns.get('plaintext_override', False),
            "window_remaining": max(0, round(dns.get('window_until', 0) - now)),
            "last_preflight": preflight,
            "last_proxy_health": read(root / 'proxy-mode/health.json'),
            "profile_refresh": profile_refresh(root, boot=boot_id),
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
    args = parser.parse_args()
    result = readiness(target=args.target, maximum=args.max_age,
                       verification_maximum=args.verification_max_age)
    print(json.dumps(result, separators=(",", ":")))
    raise SystemExit(0 if result["ready"] else 1)
