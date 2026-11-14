"""Shared, bounded kernel observations for the supervisor and verifier."""

import json
import importlib.util
from concurrent.futures import ThreadPoolExecutor
import shlex
import subprocess
import sys
import time


IPV6_PROBES = ("2606:4700:4700::1111", "2001:4860:4860::8888", "2400:3200::1")


def check(settings, run=subprocess.run):
    deadline = time.monotonic() + 2
    if settings.get('observer'):
        # Both kernel views are required, but neither depends on the other.
        # Serial netns + host observations left the final host route queries
        # almost no time within the shared two-second budget.
        with ThreadPoolExecutor(max_workers=1) as worker:
            remote = worker.submit(namespace_check, settings, deadline)
            local_check(settings, run, deadline)
            remote.result(timeout=max(0, deadline - time.monotonic()))
    else:
        local_check(settings, run, deadline)


def namespace_check(settings, deadline):
    spec = importlib.util.spec_from_file_location('core_network', settings['observer_module'])
    observer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(observer)
    observer.request(settings['observer'], 'tun', deadline=deadline)


def local_check(settings, run, deadline):
    def observe(family, commands):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("TUN observation deadline exhausted")
        # ip emits one JSON array per batch command. Keep every result and
        # reject incomplete output; batching only removes process startup cost.
        output = run([settings["ip"], "-j", f"-{family}", "-batch", "-"],
                     input="\n".join(shlex.join(command) for command in commands) + "\n",
                     capture_output=True, text=True, timeout=remaining, check=True).stdout
        decoder, values = json.JSONDecoder(), []
        while output.strip():
            value, end = decoder.raw_decode(output.lstrip())
            if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
                raise ValueError("invalid kernel TUN observation")
            values.append(value)
            output = output.lstrip()[end:]
        if len(values) != len(commands):
            raise ValueError("incomplete kernel TUN observation")
        return values

    device, table = settings["device"], str(settings["table"])
    for family in (4, 6) if settings["ipv6"] else (4,):
        commands = [["link", "show", "dev", device]] if family == 4 else []
        commands += [["rule", "show"], ["route", "show", "table", table]]
        probes = [(destination, str(uid)) for destination in IPV6_PROBES for uid in settings["uids"]]
        if family == 6:
            # Query actual unmarked root/desktop selection, not just table
            # existence. These lookups send no packets and perform no DNS.
            commands += [["route", "get", destination, "uid", uid, "mark", "0"]
                         for destination, uid in probes]
        values = observe(family, commands)
        if family == 4:
            links = values.pop(0)
            if len(links) != 1 or links[0].get("ifname") != device or "UP" not in links[0].get("flags", []):
                raise ValueError("TUN device is absent or down")
        rules, routes, *selections = values
        if not any(str(rule.get("table")) == table for rule in rules):
            raise ValueError(f"IPv{family} TUN policy rule missing")
        if not any(route.get("dev") == device for route in routes):
            raise ValueError(f"IPv{family} TUN route missing")
        for (destination, uid), selection in zip(probes, selections):
            if len(selection) != 1 or selection[0].get("dev") != device:
                raise ValueError(f"IPv6 {destination} uid {uid} is not captured by {device}")


if __name__ == "__main__":
    try:
        with open(sys.argv[1]) as stream:
            check(json.load(stream))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print("tun: " + str(error), file=sys.stderr)
        sys.exit(1)
