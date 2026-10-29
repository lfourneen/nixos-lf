package lfguard

import (
	"context"
	"errors"
	"net/netip"
	"testing"

	C "github.com/metacubex/mihomo/constant"
)

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
	// A resolver hiccup is not a target verdict: refusing the dial turned every
	// DNS glitch into a host-wide outage. The dial proceeds unresolved.
	metadata = &C.Metadata{Host: "public.example", Type: C.TUN, DNSMode: C.DNSFakeIP}
	failed := func(context.Context, string) ([]netip.Addr, error) { return nil, errors.New("no DNS") }
	empty := func(context.Context, string) ([]netip.Addr, error) { return nil, nil }
	for name, resolve := range map[string]func(context.Context, string) ([]netip.Addr, error){"error": failed, "empty": empty} {
		result, err := Protect(context.Background(), metadata, metadata, resolve)
		if err != nil {
			t.Fatal(name, "lookup failure refused a dial", err)
		}
		if result.Host != "public.example" || result != metadata {
			t.Fatal(name, "unresolved dial must pass through unchanged", result)
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

// A retry means one flaky lookup is not a verdict; a persistent failure lets
// the dial resolve on its own path instead of failing as a dead node.
func TestUnresolvedDialPassesThrough(t *testing.T) {
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8", "100.64.0.0/10", "192.168.0.0/16"}})
	metadata := &C.Metadata{Host: "peer.example", Type: C.SOCKS5, NetWork: C.TCP}
	calls := 0
	lookup := func(context.Context, string) ([]netip.Addr, error) {
		calls++
		return nil, errors.New("no DNS")
	}
	result, err := Protect(context.Background(), metadata, metadata, lookup)
	if err != nil || result != metadata || calls != 2 {
		t.Fatal("unresolved dial must pass through after one retry", result, err, calls)
	}
	resolve := func(context.Context, string) ([]netip.Addr, error) {
		return []netip.Addr{netip.MustParseAddr("100.100.100.100")}, nil
	}
	if _, err := Protect(context.Background(), metadata, metadata, resolve); err == nil {
		t.Fatal("carrier-grade NAT answer accepted")
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
