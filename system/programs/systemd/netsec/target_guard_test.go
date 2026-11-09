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
	if result, err := Protect(context.Background(), literal, literal, nil); err != nil || result != literal {
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
	for _, host := range []string{"home.arpa", "printer.home.arpa", "192.168.1.1", ""} {
		metadata.Host = host
		if _, err := Protect(context.Background(), metadata, metadata, failed); err != nil {
			t.Fatal(host, err)
		}
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
