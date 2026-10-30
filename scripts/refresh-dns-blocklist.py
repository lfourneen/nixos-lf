#!/usr/bin/env python3
"""Refresh local-data/dns-blocklist.txt.xz from the upstream lists below.

The merged list is a plain, one-per-line, sorted, de-duplicated set of names.
`system/config/dns-blocklist.nix` turns it into `address=/<name>/` lines for
dnsmasq, so every name here answers NXDOMAIN for itself and all its subdomains.

Run it from the repository root when you want to pick up upstream changes:

    ./scripts/refresh-dns-blocklist.py            # fetch, merge, write
    ./scripts/refresh-dns-blocklist.py --check    # fetch and report drift only

Only the compressed data file is rewritten; the comment header of the Nix file
(upstreams, snapshot date, per-source counts) is regenerated in place between
the `BEGIN GENERATED` / `END GENERATED` markers. The data file lives under
local-data/, which is git-ignored: `scripts/push-to-dir.sh` copies it to
/etc/nixos so the flake can be evaluated there (see local-data/README.md).

No dependencies beyond the Python standard library. Formats understood:

  hosts    `<ip> <name>` pairs (0.0.0.0 / 127.0.0.1 / ::), as published by
           most hosts-list projects
  domain   one name per line, optionally prefixed with `*.`
  dnsmasq  `address=/<name>/` or `server=/<name>/` lines, i.e. the list is already
           in dnsmasq form
"""

from __future__ import annotations

import argparse
import datetime as dt
import lzma
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "local-data", "dns-blocklist.txt.xz")
NIX = os.path.join(ROOT, "system", "config", "dns-blocklist.nix")

# name, url, format, note
SOURCES = [
    ("stevenblack", "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
     "hosts", "adware + malware + tracking"),
    ("peterlowe", "https://pgl.yoyo.org/adservers/serverlist.php"
     "?hostformat=hosts&showintro=0&mimetype=plaintext",
     "hosts", "ad servers"),
    ("adaway", "https://adaway.org/hosts.txt", "hosts", "ad servers"),
    ("oisd_big", "https://big.oisd.nl/dnsmasq", "dnsmasq", "ads, tracking, malware"),
    ("1hosts_lite", "https://raw.githubusercontent.com/badmojr/1Hosts/master/Lite/hosts.txt",
     "hosts", "balanced ad/tracking"),
    ("notracking", "https://raw.githubusercontent.com/notracking/hosts-blocklists/master"
     "/hostnames.txt",
     "hosts", "trackers, telemetry"),
]

HOSTS_PREFIX = re.compile(r"^(?:0\.0\.0\.0|127\.0\.0\.1|::1?|fe80::1)$")
DNSMASQ_LINE = re.compile(r"^(?:address|server)=/(.+)/$")
NAME = re.compile(r"^[a-z0-9_-]+(?:\.[a-z0-9_-]+)*\.[a-z]{2,}$")
IGNORED = {
    "localhost", "localhost.localdomain", "broadcasthost", "local",
    "ip6-localhost", "ip6-loopback", "0.0.0.0", "127.0.0.1", "::1",
}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "lf-dns-blocklist/1"})
    with urllib.request.urlopen(req, timeout=120) as response:
        return response.read().decode("utf-8", "replace")


def normalize(raw: str) -> str | None:
    name = raw.strip().strip(".").lower()
    if name.startswith("*."):
        name = name[2:]
    if not name or name in IGNORED or not NAME.match(name):
        return None
    return name


def parse(text: str, fmt: str) -> set[str]:
    out: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] in "#!":
            continue
        if fmt == "hosts":
            parts = line.split()
            if len(parts) < 2 or not HOSTS_PREFIX.match(parts[0]):
                continue
            candidate = parts[1]
        elif fmt == "dnsmasq":
            match = DNSMASQ_LINE.match(line)
            if not match:
                continue
            candidate = match.group(1)
        else:  # domain
            candidate = line.split()[0]
        name = normalize(candidate)
        if name:
            out.add(name)
    return out


def write_data(names: set[str]) -> None:
    payload = ("\n".join(sorted(names)) + "\n").encode()
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    with lzma.open(DATA, "wb", preset=9 | lzma.PRESET_EXTREME) as handle:
        handle.write(payload)


def read_data() -> set[str]:
    with lzma.open(DATA, "rt") as handle:
        return {line for line in handle.read().split() if line}


def update_header(counts: list[tuple[str, str, int]], total: int, when: str) -> None:
    with open(NIX) as handle:
        text = handle.read()
    begin, end = "# BEGIN GENERATED", "# END GENERATED"
    head, _, rest = text.partition(begin)
    _, _, tail = rest.partition(end)
    block = [begin]
    for name, url, count in counts:
        block.append(f"#   {name:12s} {count:7d}  {url}")
    block.append(f"#   {'total':12s} {total:7d}  merged and de-duplicated")
    block.append(f"# Snapshot: {when}")
    block.append(end)
    with open(NIX, "w") as handle:
        handle.write(head + "\n".join(block) + tail)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="report drift against the committed list, write nothing")
    args = parser.parse_args()

    merged: set[str] = set()
    counts: list[tuple[str, str, int]] = []
    for name, url, fmt, _note in SOURCES:
        try:
            names = parse(fetch(url), fmt)
        except Exception as exc:  # network trouble should not destroy the list
            print(f"!! {name}: {exc}", file=sys.stderr)
            return 1
        counts.append((name, url, len(names)))
        merged |= names
        print(f"   {name:12s} {len(names):7d}")

    print(f"   {'merged':12s} {len(merged):7d}")

    if args.check:
        old = read_data() if os.path.exists(DATA) else set()
        added, removed = merged - old, old - merged
        print(f"\ncommitted: {len(old)}  added: {len(added)}  removed: {len(removed)}")
        if added:
            print("  + " + "\n  + ".join(sorted(added)[:20])
                  + (" ..." if len(added) > 20 else ""))
        if removed:
            print("  - " + "\n  - ".join(sorted(removed)[:20])
                  + (" ..." if len(removed) > 20 else ""))
        return 0

    write_data(merged)
    update_header(counts, len(merged), dt.date.today().isoformat())
    print(f"\nwrote {os.path.relpath(DATA, ROOT)} "
          f"({os.path.getsize(DATA) / 1024 / 1024:.1f} MiB compressed)")
    print(f"updated {os.path.relpath(NIX, ROOT)} header")
    return 0


if __name__ == "__main__":
    sys.exit(main())
