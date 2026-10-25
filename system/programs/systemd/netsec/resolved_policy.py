"""Keep the managed TUN out of resolved's default resolver selection."""

import json
import subprocess
import sys


def reconcile(settings, check=False, run=subprocess.run):
    def command(*args):
        return run(args, text=True, capture_output=True, timeout=3, check=True).stdout.strip()
    try:
        command(settings["ip"], "link", "show", "dev", settings["tun"])
    except subprocess.CalledProcessError:
        return
    dns = command(settings["resolvectl"], "dns", settings["tun"])
    suffixes = command(settings["resolvectl"], "domain", settings["tun"])
    # Both commands print "Link N (name):" even when the value is empty.
    dirty = bool(dns.partition(":")[2].strip() or suffixes.partition(":")[2].strip())
    if check:
        if dirty:
            raise ValueError("TUN has independent resolved DNS configuration")
    else:
        if dirty:
            command(settings["resolvectl"], "revert", settings["tun"])
        command(settings["resolvectl"], "default-route", settings["tun"], "no")


if __name__ == "__main__":
    try:
        with open(sys.argv[1]) as stream:
            reconcile(json.load(stream), check="--check" in sys.argv[2:])
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("resolved-policy: " + type(error).__name__, file=sys.stderr)
        sys.exit(1)
