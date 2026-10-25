"""Coalesce owned nft ruleset events; no per-packet logging or traffic probes."""

import re
import os
import selectors
import subprocess
import sys
import time


OWNED = re.compile(r"\b(?:inet|ip|ip6)\s+(?:lf_[A-Za-z0-9_]+|mihomo)\b")
LEASE_ELEMENT = re.compile(
    r"^\s*(?:add|delete) element inet lf_filter "
    r"(?:dns_plaintext4|dns_split4|dns_split6)\s+\{")


def needs_wake(line):
    # Broker renewals are data, not policy drift. Definitions and every other
    # rule/object change still wake supervision; rules.py validates lease bounds.
    return bool(OWNED.search(line) and not LEASE_ELEMENT.match(line))


def main():
    selector = selectors.DefaultSelector()
    selector.register(sys.stdin.buffer, selectors.EVENT_READ)
    pending, last, buffered = False, 0.0, b""
    while True:
        for key, _ in selector.select(timeout=0.25):
            data = os.read(key.fd, 65536)
            if not data:
                return 1
            buffered += data
            lines = buffered.split(b"\n")
            buffered = lines.pop()
            for line in lines:
                if needs_wake(line.decode(errors="replace")):
                    pending = True
        now = time.monotonic()
        if pending and now - last >= 1.0:
            try:
                subprocess.run([sys.argv[1], "start", "--no-block", "proxy-net-wake.service"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=2, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
            pending, last = False, now


if __name__ == "__main__":
    sys.exit(main())
