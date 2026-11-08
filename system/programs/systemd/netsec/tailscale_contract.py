"""Reject an upstream upgrade that changes the reviewed Linux UDP mark path.

These source checks accompany the real CLI/socket checks in proxy-tailscale-vm;
they do not replace kernel tests or review of upstream networking changes.
"""

from pathlib import Path
import re
import sys


def check(source, expected):
    constants = (source / "tsconst/linuxfw.go").read_text()
    match = re.search(r"\bLinuxBypassMarkNum\s*=\s*(0x[0-9a-fA-F]+|[0-9]+)\s*$",
                      constants, re.MULTILINE)
    if not match or int(match.group(1), 0) != expected:
        raise ValueError("LinuxBypassMarkNum changed; review the firewall and rerun proxy-tailscale-vm")
    linux = (source / "net/netns/netns_linux.go").read_text()
    if not re.search(r"unix\.SetsockoptInt\([^\n]*unix\.SO_MARK,\s*tsconst\.LinuxBypassMarkNum\)", linux):
        raise ValueError("Tailscale no longer sets the reviewed SO_MARK")
    if not re.search(r"if UseSocketMark\(\)\s*\{\s*sockErr = setBypassMark\(fd\)", linux):
        raise ValueError("Tailscale socket control changed; review mark/fallback behavior")
    magicsock = (source / "wgengine/magicsock/magicsock.go").read_text()
    if "netns.Listener(c.logf, c.netMon)" not in magicsock:
        raise ValueError("magicsock no longer uses the reviewed Linux socket control")
    standalone = (source / "net/netcheck/standalone.go").read_text()
    for family in ("udp4", "udp6"):
        if not re.search(r'netns\.Listener\(c\.logf, c\.NetMon\)\)\.ListenPacket\(ctx, "'
                         + family + r'", bindAddr\)', standalone):
            raise ValueError(f"standalone {family} STUN socket control changed")


if __name__ == "__main__":
    try:
        check(Path(sys.argv[1]), int(sys.argv[2]))
    except (OSError, ValueError) as error:
        sys.exit(f"Tailscale transport contract: {error}")
