"""Support nftables exclusion ranges ending at the maximum IPv4/IPv6 address.

Omit the invalid end marker so the final interval extends to the maximum.
Upstream: https://github.com/SagerNet/sing-tun/commit/7954dd6e20105bd48659dbb8bfc145c0a2e86a6d
Exact source matching requires review when the interval builder changes.
"""
from pathlib import Path
import stat
import sys

TARGET = Path("vendor/github.com/metacubex/sing-tun/redirect_nftables_exprs.go")

BUGGY = (
    "\t\tendAddr := rr.To().Next()\n"
    "\t\tif !endAddr.IsValid() {\n"
    "\t\t\tendAddr = rr.From()\n"
    "\t\t}\n"
    "\t\tsetElements = append(setElements, nftables.SetElement{\n"
    "\t\t\tKey: rr.From().AsSlice(),\n"
    "\t\t})\n"
    "\t\tsetElements = append(setElements, nftables.SetElement{\n"
    "\t\t\tKey:         endAddr.AsSlice(),\n"
    "\t\t\tIntervalEnd: true,\n"
    "\t\t})\n"
)

FIXED = (
    "\t\tendAddr := rr.To().Next()\n"
    "\t\tsetElements = append(setElements, nftables.SetElement{\n"
    "\t\t\tKey: rr.From().AsSlice(),\n"
    "\t\t})\n"
    "\t\tif endAddr.IsValid() {\n"
    "\t\t\tsetElements = append(setElements, nftables.SetElement{\n"
    "\t\t\t\tKey:         endAddr.AsSlice(),\n"
    "\t\t\t\tIntervalEnd: true,\n"
    "\t\t\t})\n"
    "\t\t}\n"
)


def patch(root):
    path = root / TARGET
    source = path.read_text()
    if source.count(BUGGY) != 1:
        raise SystemExit(
            "vendored sing-tun anchor changed or fix already upstreamed: %s" % path
        )
    path.chmod(path.stat().st_mode | stat.S_IWUSR)
    path.write_text(source.replace(BUGGY, FIXED, 1))


if __name__ == "__main__":
    patch(Path(sys.argv[1]))
