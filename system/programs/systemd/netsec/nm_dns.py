"""Apply DNS policy to the current managed Wi-Fi profile; never read its secrets."""

import json
import os
import subprocess
import sys


def apply(settings, interface, action, uuid, run=subprocess.run):
    if interface != settings["interface"] or not uuid or uuid in settings["exceptions"]:
        return
    if action not in {"pre-up", "up", "dhcp4-change", "dhcp6-change"}:
        return

    def command(argv, required=True):
        result = run(argv, text=True, capture_output=True, timeout=8, check=False)
        if required and result.returncode:
            raise RuntimeError("DNS policy command failed: " + argv[0].rsplit("/", 1)[-1])
        return result.stdout.strip()

    properties = ["ipv4.ignore-auto-dns", "ipv6.ignore-auto-dns", "ipv4.dns", "ipv6.dns",
                  "ipv4.dns-search", "ipv6.dns-search"]
    current = command([settings["nmcli"], "-g", ",".join(properties), "connection", "show", "uuid", uuid])
    values = current.split("\n")
    compliant = len(values) >= 2 and all(v in {"yes", "true"} for v in values[:2]) \
        and not any(v for v in values[2:])
    if not compliant:
        argv = [settings["nmcli"], "--wait", "5", "connection", "modify", "uuid", uuid]
        for key in properties:
            argv += [key, "yes" if key.endswith("ignore-auto-dns") else ""]
        command(argv)
    # pre-up can be too early for Device.Reapply; up always reapplies the now
    # compliant profile, including one modified at pre-up in this activation.
    if action != "pre-up":
        command([settings["nmcli"], "--wait", "5", "device", "reapply", interface])
    command([settings["resolvectl"], "dns", interface, settings["resolver"]])
    command([settings["resolvectl"], "domain", interface, "~."])
    command([settings["resolvectl"], "llmnr", interface, "no"])


def main():
    with open(sys.argv[1]) as stream:
        settings = json.load(stream)
    try:
        apply(settings, sys.argv[2], sys.argv[3], os.environ.get("CONNECTION_UUID", ""))
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"nm-dns-policy: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
