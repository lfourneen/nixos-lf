package lfguard

import (
	"context"
	"errors"
	"net/netip"
	"os"
	"path/filepath"
	"testing"

	C "github.com/metacubex/mihomo/constant"
)

func TestSharedDomainFilter(t *testing.T) {
	file := filepath.Join(t.TempDir(), "domains")
	if err := os.WriteFile(file, []byte("ads.example\nmetrics.home.arpa\n_under.example\n"), 0600); err != nil {
		t.Fatal(err)
	}
	ConfigureT(t, map[string]any{
		"prefixes": []string{"10.0.0.0/8"}, "allowed-domains": []string{"home.arpa"},
		"blocked-domains-file": file, "blocked-domains": []string{"again.ok.ads.example", "192.168.1.1"},
		"blocked-domain-exceptions": []string{"ok.ads.example"},
	})
	for _, host := range []string{"ads.example", "Pixel.ADS.example.", "again.ok.ads.example", "metrics.home.arpa", "_under.example"} {
		if CheckDomain(host) == nil {
			t.Fatal("blocked name allowed", host)
		}
		for _, network := range []C.NetWork{C.TCP, C.UDP} {
			metadata := &C.Metadata{Host: host, Type: C.SOCKS5, NetWork: network}
			if _, err := Protect(context.Background(), metadata, metadata, nil); err == nil {
				t.Fatal("proxy dial bypassed domain filter", host, network)
			}
		}
	}
	for _, host := range []string{"ok.ads.example", "a.ok.ads.example", "notads.example", "ads.example.org"} {
		if err := CheckDomain(host); err != nil {
			t.Fatal(host, err)
		}
	}
	literal := &C.Metadata{Host: "192.168.1.1", Type: C.SOCKS5}
	if result, err := Protect(context.Background(), literal, literal, nil); err != nil || result.DstIP.String() != "192.168.1.1" || result.Host != "" {
		t.Fatal("suffix filter changed intentional literal-IP access", result, err)
	}
	for _, invalid := range []string{"/nonexistent/lf-blocklist", file} {
		if invalid == file {
			_ = os.WriteFile(file, []byte("bad/domain\n"), 0600)
		}
		if Configure(map[string]any{"prefixes": []string{"10.0.0.0/8"}, "blocked-domains-file": invalid}) == nil {
			t.Fatal("invalid blocklist accepted")
		}
		if CheckDomain("ads.example") == nil {
			t.Fatal("invalid update removed active filter")
		}
	}
}

func ConfigureT(t *testing.T, value map[string]any) {
	t.Helper()
	if err := Configure(value); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = Configure(nil) })
}

func TestProtectedTargets(t *testing.T) {
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8", "100.64.0.0/10", "192.168.0.0/16", "fc00::/7"},
		"allowed-domains": []string{"home.arpa"}})
	for _, address := range []string{"10.1.2.3", "100.100.100.100", "127.0.0.1", "169.254.169.254", "0.0.0.0", "::", "::1", "fe80::1", "fd12::1", "::ffff:192.168.1.1", "224.0.0.1"} {
		t.Run(address, func(t *testing.T) {
			metadata := &C.Metadata{Host: "public.example", Type: C.SOCKS5, NetWork: C.TCP}
			lookup := func(context.Context, string) ([]netip.Addr, error) {
				return []netip.Addr{netip.MustParseAddr("203.0.113.42"), netip.MustParseAddr(address)}, nil
			}
			if _, err := Protect(context.Background(), metadata, metadata, lookup); err == nil {
				t.Fatal("private/mixed answer accepted")
			}
		})
	}
	metadata := &C.Metadata{Host: "public.example", Type: C.TUN, DNSMode: C.DNSFakeIP}
	lookup := func(context.Context, string) ([]netip.Addr, error) {
		return []netip.Addr{netip.MustParseAddr("2001:db8::42")}, nil
	}
	result, err := Protect(context.Background(), metadata, metadata, lookup)
	if err != nil || result.Host != "" || result.DstIP.String() != "2001:db8::42" || metadata.Host == "" {
		t.Fatal("checked IP not pinned on a clone", result, err)
	}
	// Exhausted/empty lookups cannot authorize an unchecked second resolution.
	metadata = &C.Metadata{Host: "public.example", Type: C.TUN, DNSMode: C.DNSFakeIP}
	failed := func(context.Context, string) ([]netip.Addr, error) { return nil, errors.New("no DNS") }
	empty := func(context.Context, string) ([]netip.Addr, error) { return nil, nil }
	for name, resolve := range map[string]func(context.Context, string) ([]netip.Addr, error){"error": failed, "empty": empty} {
		result, err := Protect(context.Background(), metadata, metadata, resolve)
		if err == nil || result != nil {
			t.Fatal(name, "unresolved dial must fail closed", result, err)
		}
	}
	// Local suffixes still require a local, validated and pinned resolution.
	metadata.Host = "printer.home.arpa"
	if _, err := Protect(context.Background(), metadata, metadata, failed); err == nil {
		t.Fatal("unresolved local name accepted")
	}
	metadata.Host = "nothome.arpa"
	private := func(context.Context, string) ([]netip.Addr, error) {
		return []netip.Addr{netip.MustParseAddr("192.168.1.1")}, nil
	}
	if _, err := Protect(context.Background(), metadata, metadata, private); err == nil {
		t.Fatal("suffix boundary bypass")
	}
	metadata.Host = "public.example"
	metadata.DstIP = netip.MustParseAddr("10.1.2.3")
	if _, err := Protect(context.Background(), metadata, metadata, nil); err == nil {
		t.Fatal("pre-resolved private address accepted")
	}
	metadata.Type = C.INNER
	if _, err := Protect(context.Background(), metadata, metadata, nil); err != nil {
		t.Fatal("recursive DNS infrastructure intercepted")
	}
}

// A transient error gets one retry; persistent failure never grants a dial.
func TestUnresolvedDialFailsClosed(t *testing.T) {
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8", "100.64.0.0/10", "192.168.0.0/16"}})
	metadata := &C.Metadata{Host: "peer.example", Type: C.SOCKS5, NetWork: C.TCP}
	calls := 0
	lookup := func(context.Context, string) ([]netip.Addr, error) {
		calls++
		return nil, errors.New("no DNS")
	}
	result, err := Protect(context.Background(), metadata, metadata, lookup)
	if err == nil || result != nil || calls != 2 {
		t.Fatal("unresolved dial must fail after one retry", result, err, calls)
	}
	calls = 0
	recovered := func(context.Context, string) ([]netip.Addr, error) {
		calls++
		if calls == 1 {
			return nil, errors.New("temporary failure")
		}
		return []netip.Addr{netip.MustParseAddr("203.0.113.42")}, nil
	}
	result, err = Protect(context.Background(), metadata, metadata, recovered)
	if err != nil || result.Host != "" || result.DstIP.String() != "203.0.113.42" || calls != 2 {
		t.Fatal("validated retry did not pin target", result, err, calls)
	}
	resolve := func(context.Context, string) ([]netip.Addr, error) {
		return []netip.Addr{netip.MustParseAddr("100.100.100.100")}, nil
	}
	if _, err := Protect(context.Background(), metadata, metadata, resolve); err == nil {
		t.Fatal("carrier-grade NAT answer accepted")
	}
}

func TestProxyOnlyDirectScope(t *testing.T) {
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8"}, "proxy-only": true})
	for _, kind := range []C.Type{C.HTTP, C.HTTPS, C.SOCKS5, C.TUN, C.TPROXY} {
		for _, network := range []C.NetWork{C.TCP, C.UDP} {
			if CheckDirect(&C.Metadata{Type: kind, NetWork: network}) == nil {
				t.Fatal("application direct egress allowed", kind, network)
			}
		}
	}
	if err := CheckDirect(&C.Metadata{Type: C.INNER}); err != nil {
		t.Fatal(err)
	}
	for _, network := range []C.NetWork{C.TCP, C.UDP} {
		if CheckDirect(&C.Metadata{Type: C.INNER, NetWork: network, LFRuleDNS: true}) == nil {
			t.Fatal("rule-routed DNS DIRECT was treated as bootstrap", network)
		}
	}
	if err := Configure(map[string]any{"prefixes": []string{"10.0.0.0/8"}, "proxy-only": false}); err != nil {
		t.Fatal(err)
	}
	if err := CheckDirect(&C.Metadata{Type: C.HTTP}); err != nil {
		t.Fatal(err)
	}
	for _, network := range []C.NetWork{C.TCP, C.UDP} {
		if err := CheckDirect(&C.Metadata{Type: C.INNER, NetWork: network, LFRuleDNS: true}); err != nil {
			t.Fatal("split-mode DNS DIRECT must remain available", network, err)
		}
	}
}

func TestSpecialAndTranslatedDNSAnswers(t *testing.T) {
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8", "169.254.0.0/16", "192.168.0.0/16"}})
	metadata := &C.Metadata{Host: "external.example", Type: C.SOCKS5}
	for _, address := range []string{"240.0.0.1", "255.255.255.255", "198.18.0.1", "198.19.255.254", "192.0.0.8", "192.0.0.170", "::a9fe:a9fe", "64:ff9b::a9fe:a9fe", "64:ff9b::c0a8:101", "64:ff9b::f000:1", "64:ff9b:1::1", "2001::1", "2002:c0a8:101::1"} {
		lookup := func(context.Context, string) ([]netip.Addr, error) {
			return []netip.Addr{netip.MustParseAddr(address)}, nil
		}
		if _, err := Protect(context.Background(), metadata, metadata, lookup); err == nil {
			t.Fatal("special DNS target accepted", address)
		}
	}
	for _, address := range []string{"192.0.0.9", "192.0.0.10", "64:ff9b::808:808", "2001:4860:4860::8888"} {
		lookup := func(context.Context, string) ([]netip.Addr, error) {
			return []netip.Addr{netip.MustParseAddr(address)}, nil
		}
		if _, err := Protect(context.Background(), metadata, metadata, lookup); err != nil {
			t.Fatal("public anycast/NAT64 target rejected", address, err)
		}
	}
}

func TestInvalidConfigurationRetainsProtection(t *testing.T) {
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8"}})
	for _, bad := range []map[string]any{
		{}, {"prefixes": []string{"invalid"}},
		{"prefixes": []string{"10.0.0.0/8"}, "allowed-domains": []string{"*.example.com"}},
		{"prefixes": []string{"10.0.0.0/8"}, "magic-dns-suffix-file": "/tmp/domain"},
		{"prefixes": []string{"10.0.0.0/8"}, "unknown": true},
	} {
		if err := Configure(bad); err == nil {
			t.Fatal("invalid guard policy accepted", bad)
		}
		m := &C.Metadata{Host: "public.example", DstIP: netip.MustParseAddr("10.1.2.3")}
		if _, err := Protect(context.Background(), m, m, nil); err == nil {
			t.Fatal("invalid update disabled protection")
		}
	}
}

func TestLiteralAndInternalEgressAuthorization(t *testing.T) {
	ConfigureT(t, map[string]any{
		"prefixes":           []string{"10.0.0.0/8", "100.64.0.0/10", "192.168.0.0/16", "fc00::/7"},
		"allowed-domains":    []string{"home.arpa"},
		"local-target-cidrs": []string{"192.168.0.0/16", "127.0.0.0/8", "fc00::/7"},
		"fake-ip-prefix":     "203.0.114.0/24",
		"nat64-prefixes":     []string{"2001:db8:64::/96", "2001:db8:aa00::/40"},
		"remote-target-grants": []map[string]any{{"proxy": "node", "cidr": "192.168.1.20/32",
			"inlets": []string{"SOCKS5"}, "network": "tcp", "ports": []uint16{443}}},
	})
	for _, network := range []C.NetWork{C.TCP, C.UDP} {
		for _, address := range []string{"192.168.1.1", "::ffff:192.168.1.1", "127.0.0.1", "fd12::1"} {
			for _, hostForm := range []bool{false, true} {
				m := &C.Metadata{Type: C.SOCKS5, NetWork: network, DstPort: 80}
				if hostForm {
					m.Host = address
				} else {
					m.DstIP = netip.MustParseAddr(address)
				}
				result, err := Protect(context.Background(), m, m, nil)
				if err != nil {
					t.Fatal(address, err)
				}
				if err = CheckEgress(result, true, "renamed-direct"); err != nil {
					t.Fatal(address, err)
				}
				if err = CheckEgress(result, false, "node"); err == nil {
					t.Fatal("remote internal literal allowed", address)
				}
				result.Type = C.TPROXY
				if err = CheckEgress(result, true, "DIRECT"); err == nil {
					t.Fatal("guest inherited host local grant")
				}
			}
		}
	}
	for _, address := range []string{"169.254.169.254", "::ffff:169.254.169.254", "169.254.170.2", "100.100.100.200", "fd00:ec2::254",
		"0.1.2.3", "0.0.0.0", "::", "224.0.0.1", "ff02::1", "198.18.0.5", "203.0.114.5", "240.0.0.1",
		"64:ff9b::c0a8:101", "2001:db8:64::7f00:1", "2001:db8:aac0:a801:1::", "fe80::1%eth0"} {
		m := &C.Metadata{Host: address, Type: C.HTTP}
		if _, err := Protect(context.Background(), m, m, nil); err == nil {
			t.Fatal("unsafe literal accepted", address)
		}
	}
	m := &C.Metadata{Host: "printer.home.arpa", Type: C.SOCKS5, NetWork: C.TCP, DstPort: 443}
	resolve := func(context.Context, string) ([]netip.Addr, error) {
		return []netip.Addr{netip.MustParseAddr("192.168.1.20")}, nil
	}
	pinned, err := Protect(context.Background(), m, m, resolve)
	if err != nil || pinned.Host != "" {
		t.Fatal("internal name not pinned", pinned, err)
	}
	if err = CheckEgress(pinned, false, "node"); err != nil {
		t.Fatal(err)
	}
	for _, change := range []func(*C.Metadata){
		func(m *C.Metadata) { m.DstPort = 80 }, func(m *C.Metadata) { m.Type = C.HTTP },
		func(m *C.Metadata) { m.NetWork = C.UDP }, func(m *C.Metadata) { m.DstIP = netip.MustParseAddr("192.168.1.21") },
	} {
		copy := pinned.Clone()
		change(copy)
		if CheckEgress(copy, false, "node") == nil {
			t.Fatal("grant scope widened", copy)
		}
	}
	if CheckEgress(pinned, false, "other-node") == nil {
		t.Fatal("wrong proxy allowed")
	}
	// A direct/local grant cannot turn a public name's rebinding answer into
	// an internal-target authorization.
	m.Host = "public.example"
	if _, err = Protect(context.Background(), m, m, resolve); err == nil {
		t.Fatal("public rebinding used remote grant")
	}
}

func TestTranslatedTargetsAndConfiguration(t *testing.T) {
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8"},
		"nat64-prefixes": []string{"2001:db8:64::/96", "2001:db8:aa00::/40"}})
	for _, address := range []string{"64:ff9b::808:808", "2001:db8:64::808:808", "2001:db8:aa08:808::"} {
		m := &C.Metadata{DstIP: netip.MustParseAddr(address), Type: C.TUN}
		result, err := Protect(context.Background(), m, m, nil)
		if err != nil {
			t.Fatal(address, err)
		}
		if err = CheckEgress(result, false, "node"); err != nil {
			t.Fatal(address, err)
		}
	}
	for _, bad := range []map[string]any{
		{"local-target-cidrs": []string{"192.168.1.1/24"}},
		{"local-target-cidrs": []string{"0.0.0.0/0"}},
		{"nat64-prefixes": []string{"2001:db8::/72"}},
		{"fake-ip-prefix": "198.18.0.1/16"},
		{"remote-target-grants": []map[string]any{{"proxy": "node", "cidr": "192.168.1.1/32", "network": "tcp", "ports": []int{0}, "inlets": []string{"INNER"}}}},
	} {
		bad["prefixes"] = []string{"10.0.0.0/8"}
		if Configure(bad) == nil {
			t.Fatal("invalid authorization accepted", bad)
		}
	}
}

// RFC 6052's six placements, including the u octet at bits 64..71.
func TestNAT64Formats(t *testing.T) {
	for _, tc := range []struct{ prefix, public, private string }{
		{"2001:db8::/32", "2001:db8:808:808::", "2001:db8:c0a8:101::"},
		{"2001:db8:aa00::/40", "2001:db8:aa08:808:8::", "2001:db8:aac0:a801:1::"},
		{"2001:db8:aa::/48", "2001:db8:aa:808:8:800::", "2001:db8:aa:c0a8:1:100::"},
		{"2001:db8:aa:bb00::/56", "2001:db8:aa:bb08:8:808::", "2001:db8:aa:bbc0:a8:101::"},
		{"2001:db8:aa:bb::/64", "2001:db8:aa:bb:8:808:800:0", "2001:db8:aa:bb:c0:a801:100:0"},
		{"2001:db8:aa:bb::/96", "2001:db8:aa:bb::808:808", "2001:db8:aa:bb::c0a8:101"},
	} {
		t.Run(tc.prefix, func(t *testing.T) {
			ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8"}, "nat64-prefixes": []string{tc.prefix}})
			for _, entry := range []struct {
				address string
				allow   bool
			}{{tc.public, true}, {tc.private, false}} {
				m := &C.Metadata{DstIP: netip.MustParseAddr(entry.address), Type: C.TUN}
				result, err := Protect(context.Background(), m, m, nil)
				if (err == nil) != entry.allow {
					t.Fatal(entry, err)
				}
				if err == nil {
					if err = CheckEgress(result, false, "node"); err != nil {
						t.Fatal(err)
					}
				}
			}
		})
	}
}

func TestLocalAuthorizationCanBeRemoved(t *testing.T) {
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8"}})
	m := &C.Metadata{DstIP: netip.MustParseAddr("127.0.0.1"), Type: C.HTTP}
	result, err := Protect(context.Background(), m, m, nil)
	if err != nil {
		t.Fatal(err)
	}
	if CheckEgress(result, true, "DIRECT") == nil {
		t.Fatal("empty local CIDRs authorized a local target")
	}
}

type lfCandidateConn struct{ C.Conn }

func (*lfCandidateConn) Close() error { return nil }

func TestValidatedCandidatesFallbackAndIsolation(t *testing.T) {
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8"}})
	defer Configure(nil)
	for _, answers := range [][]string{
		{"203.0.113.11", "203.0.113.10"},
		{"203.0.113.11", "2001:db8::10"},
		{"2001:db8::11", "2001:db8::10"},
	} {
		ips := []netip.Addr{}
		for _, text := range answers {
			ips = append(ips, netip.MustParseAddr(text))
		}
		metadata := &C.Metadata{Host: "fallback.example", DstPort: 443, NetWork: C.TCP, Type: C.SOCKS5}
		protected, err := Protect(context.Background(), metadata, metadata,
			func(context.Context, string) ([]netip.Addr, error) { return ips, nil })
		if err != nil {
			t.Fatal(err)
		}
		attempts := 0
		conn, err := DialTCP(context.Background(), protected, func(ctx context.Context, target *C.Metadata) (C.Conn, error) {
			attempts++
			if target.Host != "" || len(target.LFCandidates) != 0 {
				t.Fatal("target was not pinned")
			}
			if err := CheckEgress(target, false, "actual-leaf"); err != nil {
				t.Fatal(err)
			}
			if target.DstIP != ips[len(ips)-1] {
				return nil, errors.New("first address unavailable")
			}
			return &lfCandidateConn{}, nil
		})
		if err != nil || conn == nil || attempts != 2 {
			t.Fatalf("fallback: %v, %d", err, attempts)
		}
		if protected.DstIP != ips[len(ips)-1] {
			t.Fatal("successful target was not retained")
		}
	}
	metadata := &C.Metadata{Host: "mixed.example", NetWork: C.TCP, Type: C.SOCKS5}
	if _, err := Protect(context.Background(), metadata, metadata,
		func(context.Context, string) ([]netip.Addr, error) {
			return []netip.Addr{netip.MustParseAddr("203.0.113.10"), netip.MustParseAddr("10.0.0.1")}, nil
		}); err == nil {
		t.Fatal("mixed public/private answer accepted")
	}
	metadata.NetWork = C.UDP
	result, err := Protect(context.Background(), metadata, metadata,
		func(context.Context, string) ([]netip.Addr, error) {
			return []netip.Addr{netip.MustParseAddr("203.0.113.10")}, nil
		})
	if err != nil || len(result.LFCandidates) != 0 {
		t.Fatal("UDP gained per-packet fallback")
	}
}

func TestProviderNodesRequireCertificateExceptions(t *testing.T) {
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8"}})
	defer Configure(nil)
	if CheckProxy(map[string]any{"name": "node", "skip-cert-verify": true}) == nil {
		t.Fatal("insecure node accepted")
	}
	if CheckProxy(map[string]any{"name": "node", "tls": map[string]any{"skip-cert-verify": true}}) == nil {
		t.Fatal("nested insecure node accepted")
	}
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8"}, "insecure-proxy-exceptions": []string{"node"}})
	if err := CheckProxy(map[string]any{"name": "node", "skip-cert-verify": true}); err != nil {
		t.Fatal(err)
	}
	// A name exception intentionally follows a same-named node, including
	// a provider replacing its server; it is not a server identity pin.
	if err := CheckProxy(map[string]any{"name": "node", "server": "changed.example", "skip-cert-verify": true}); err != nil {
		t.Fatal("same-name exception did not follow its documented scope:", err)
	}
	if CheckProxy(map[string]any{"name": "replacement", "skip-cert-verify": true}) == nil {
		t.Fatal("exception crossed node names")
	}
}

func TestStrictInletRejectsDirectAndPrivate(t *testing.T) {
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8", "192.168.0.0/16"},
		"strict-inlets": []string{"lf-strict"}, "local-target-cidrs": []string{"192.168.0.0/16"}})
	for _, network := range []C.NetWork{C.TCP, C.UDP} {
		strict := &C.Metadata{Type: C.HTTP, NetWork: network, InName: "lf-strict", DstIP: netip.MustParseAddr("1.1.1.1"), DstPort: 443}
		if CheckDirect(strict) == nil || CheckEgress(strict, true, "renamed-direct") == nil {
			t.Fatal("strict DIRECT accepted")
		}
		if err := CheckEgress(strict, false, "remote"); err != nil {
			t.Fatal(err)
		}
		strict.DstIP, strict.LFLocalTarget = netip.MustParseAddr("192.168.1.1"), true
		if CheckEgress(strict, false, "remote") == nil {
			t.Fatal("strict private target accepted")
		}
		ordinary := &C.Metadata{Type: C.HTTP, NetWork: network, DstIP: netip.MustParseAddr("1.1.1.1")}
		if err := CheckDirect(ordinary); err != nil {
			t.Fatal("ordinary Rule routing changed", err)
		}
	}
}

func TestPlaintextCredentialsCannotUseCertificateException(t *testing.T) {
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8"},
		"reject-plaintext-credentials": true, "insecure-proxy-exceptions": []string{"test"}})
	for _, kind := range []string{"http", "socks5"} {
		node := map[string]any{"name": "test", "type": kind, "username": "fixture", "password": "dummy"}
		if CheckProxy(node) == nil {
			t.Fatal("plaintext authentication accepted")
		}
		node["tls"] = true
		if err := CheckProxy(node); err != nil {
			t.Fatal(err)
		}
	}
}
