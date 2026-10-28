"""One readiness contract for CLI, desktop status and automated consumers."""
import argparse
import json
import math
from pathlib import Path
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


def readiness(root=Path("/run"), target="proxy", maximum=120, now=None,
              verification_maximum=420, boot_id=None):
    root = Path(root)
    now = time.clock_gettime(time.CLOCK_BOOTTIME) if now is None else now
    if boot_id is None:
        try:
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        except OSError:
            boot_id = ""
    policy = read(root / "proxy-mode/state.json")
    heartbeat = read(root / "proxy-mode/heartbeat.json")
    dns = read(root / "dns-upstream/state.json")
    permissions = read(root / "dns-policy/state.json")
    relay = read(root / "gost-relay/state.json")
    verification = read(root / "netsec/state.json")
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
        "dot", "bootstrap-plaintext", "portal-plaintext", "degraded-plaintext"
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
    return {"ready": not reasons, "target": target, "policy_version": version,
            "mode": policy.get("mode", "unknown"), "reasons": reasons}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=["proxy", "direct"])
    parser.add_argument("--max-age", type=int, default=120)
    parser.add_argument("--verification-max-age", type=int, default=420)
    args = parser.parse_args()
    result = readiness(target=args.target, maximum=args.max_age,
                       verification_maximum=args.verification_max_age)
    print(json.dumps(result, separators=(",", ":")))
    raise SystemExit(0 if result["ready"] else 1)
