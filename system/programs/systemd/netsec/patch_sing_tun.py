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

    # REDIRECT in OUTPUT targets loopback. SO_BINDTODEVICE(TUN) sockets retain
    # their output constraint during rerouting, sending 127/8 into the TUN and
    # hitting the kernel's martian protection. DNAT to the TUN's own local
    # address reaches the same wildcard redirect listener without that conflict.
    path = root / 'vendor/github.com/metacubex/sing-tun/redirect_nftables_rules.go'
    source = path.read_text()
    anchor = ') error {\n\texprsRedirect := []expr.Any{'
    replacement = ''') error {
	if chain.Hooknum == nftables.ChainHookOutput && !r.tunOptions.AutoRedirectMarkMode &&
		len(r.tunOptions.Inet4LoopbackAddress) == 0 && len(r.tunOptions.Inet6LoopbackAddress) == 0 {
		var prefixes []netip.Prefix
		if r.enableIPv4 {
			prefixes = append(prefixes, r.tunOptions.Inet4Address...)
		}
		if r.enableIPv6 {
			prefixes = append(prefixes, r.tunOptions.Inet6Address...)
		}
		for _, prefix := range prefixes {
			address := prefix.Addr()
			family, nfproto := uint32(unix.AF_INET), byte(nftables.TableFamilyIPv4)
			if address.Is6() {
				family, nfproto = unix.AF_INET6, byte(nftables.TableFamilyIPv6)
			}
			rule := append([]expr.Any{}, exprs...)
			rule = append(rule,
				&expr.Meta{Key: expr.MetaKeyNFPROTO, Register: 1},
				&expr.Cmp{Op: expr.CmpOpEq, Register: 1, Data: []byte{nfproto}},
				&expr.Meta{Key: expr.MetaKeyL4PROTO, Register: 1},
				&expr.Cmp{Op: expr.CmpOpEq, Register: 1, Data: []byte{unix.IPPROTO_TCP}},
				&expr.Counter{},
				&expr.Immediate{Register: 1, Data: address.AsSlice()},
				&expr.Immediate{Register: 2, Data: binaryutil.BigEndian.PutUint16(r.redirectPort())},
				&expr.NAT{Type: expr.NATTypeDestNAT, Family: family, RegAddrMin: 1,
					RegProtoMin: 2, Specified: true},
			)
			nft.AddRule(&nftables.Rule{Table: table, Chain: chain, Exprs: rule})
		}
		return nil
	}
	exprsRedirect := []expr.Any{'''
    if source.count(anchor) != 1:
        raise SystemExit('sing-tun output redirect anchor changed; review TUN-bound socket fix')
    path.chmod(path.stat().st_mode | stat.S_IWUSR)
    path.write_text(source.replace(anchor, replacement, 1))


if __name__ == "__main__":
    patch(Path(sys.argv[1]))
