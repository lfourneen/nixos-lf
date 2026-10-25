"""Collect local performance evidence. No probes, sudo, or configuration writes."""

import json
import argparse
from pathlib import Path
import subprocess
import sys
import time


def command(argv):
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=5, check=False)
        return {"available": result.returncode == 0, "output": result.stdout.strip()}
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "output": ""}


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("settings")
    parser.add_argument("--sample-seconds", type=int, choices=range(1, 61))
    args = parser.parse_args()
    settings = read_json(args.settings)
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
                       "dns_permissions": "/run/dns-policy/state.json"}.items():
        result[name] = read_json(path)
    if args.sample_seconds:
        time.sleep(max(0, args.sample_seconds - (time.monotonic() - started)))
    result["services"] = command(service_args)
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
    main()
