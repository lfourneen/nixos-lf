"""Collect local performance evidence. No probes, sudo, or configuration writes."""

import json
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
    settings = read_json(sys.argv[1])
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
                       "verification": "/run/netsec/state.json"}.items():
        result[name] = read_json(path)
    result["services"] = command([settings["systemctl"], "show", "clash-verge.service",
                                  "gost-relay.service", "proxy-mode.service", "-p", "Id",
                                  "-p", "CPUUsageNSec", "-p", "MemoryCurrent", "-p", "NRestarts"])
    for name in ("softirqs", "net/softnet_stat"):
        try:
            result[name] = Path("/proc", name).read_text()
        except OSError:
            result[name] = None
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
