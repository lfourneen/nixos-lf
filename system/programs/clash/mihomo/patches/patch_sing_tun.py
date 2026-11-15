"""Preserve managed TUN-bound OUTPUT and guest TPROXY socket selection.

Exact source matching requires review when the redirect rules change.
"""
from pathlib import Path
import stat
import sys


def patch(root):
    # REDIRECT in OUTPUT targets loopback. SO_BINDTODEVICE(TUN) sockets retain
    # their output constraint during rerouting, sending 127/8 into the TUN and
    # hitting the kernel's martian protection. DNAT to the TUN's own local
    # address reaches the same wildcard redirect listener without that conflict.
    path = root / 'vendor/github.com/metacubex/sing-tun/redirect_nftables_rules.go'
    source = path.read_text()
    # The repository's explicit IPv4 guest inlet already selected a transparent
    # socket at prerouting/mangle. Never replace that socket with auto-redirect
    # in the later NAT lookup. The mark is assigned by owned B-only nft rules;
    # ordinary guests cannot set a host packet mark over the wire.
    inlet_anchor = "\tif chain.Hooknum == nftables.ChainHookPrerouting {\n\t\tnft.AddRule(&nftables.Rule{\n\t\t\tTable: table,"
    inlet_rule = "\tif chain.Hooknum == nftables.ChainHookPrerouting {\n" + """
        nft.AddRule(&nftables.Rule{Table: table, Chain: chain, Exprs: []expr.Any{
            &expr.Meta{Key: expr.MetaKeyMARK, Register: 1},
            &expr.Cmp{Op: expr.CmpOpEq, Register: 1, Data: binaryutil.NativeEndian.PutUint32(0x233)},
            &expr.Counter{},
            &expr.Verdict{Kind: expr.VerdictReturn},
        }})
""" + "\t\tnft.AddRule(&nftables.Rule{\n\t\t\tTable: table,"
    if source.count(inlet_anchor) != 1:
        raise SystemExit('sing-tun guest TPROXY exclusion anchor changed')
    source = source.replace(inlet_anchor, inlet_rule, 1)
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
    tests = Path(__file__).parent.parent / 'tests/sing_tun_interval_test.go'
    path.with_name('lf_interval_test.go').write_text(tests.read_text())


if __name__ == "__main__":
    patch(Path(sys.argv[1]))
