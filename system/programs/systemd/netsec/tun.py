"""Shared, bounded kernel observations for the supervisor and verifier."""

import json
import subprocess
import sys
import time


IPV6_PROBES = ("2606:4700:4700::1111", "2001:4860:4860::8888", "2400:3200::1")


def check(settings, run=subprocess.run):
    deadline = time.monotonic() + 2

    def observe(*args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("TUN observation deadline exhausted")
        output = run([settings["ip"], "-j", *args], capture_output=True, text=True,
                     timeout=remaining, check=True).stdout
        value = json.loads(output)
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ValueError("invalid kernel TUN observation")
        return value

    device, table = settings["device"], settings["table"]
    links = observe("link", "show", "dev", device)
    if len(links) != 1 or links[0].get("ifname") != device or "UP" not in links[0].get("flags", []):
        raise ValueError("TUN device is absent or down")
    for family in (4, 6) if settings["ipv6"] else (4,):
        if not any(str(rule.get("table")) == str(table) for rule in observe(f"-{family}", "rule", "show")):
            raise ValueError(f"IPv{family} TUN policy rule missing")
        if not any(route.get("dev") == device for route in observe(f"-{family}", "route", "show", "table", str(table))):
            raise ValueError(f"IPv{family} TUN route missing")
    if settings["ipv6"]:
        # Query unmarked root and desktop flows; a policy rule's existence does
        # not prove that it wins. No DNS or packet transmission is involved.
        for destination in IPV6_PROBES:
            for uid in settings["uids"]:
                routes = observe("-6", "route", "get", destination, "uid", str(uid), "mark", "0")
                if len(routes) != 1 or routes[0].get("dev") != device:
                    raise ValueError(f"IPv6 {destination} uid {uid} is not captured by {device}")


if __name__ == "__main__":
    try:
        with open(sys.argv[1]) as stream:
            check(json.load(stream))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print("tun: " + str(error), file=sys.stderr)
        sys.exit(1)
