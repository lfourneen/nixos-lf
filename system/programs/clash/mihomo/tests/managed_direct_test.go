package outbound

import (
	"context"
	"net/netip"
	"strings"
	"testing"

	"github.com/metacubex/mihomo/component/lfguard"
	C "github.com/metacubex/mihomo/constant"
)

func TestLFManagedDirectAdaptersRefuseApplicationEgress(t *testing.T) {
	if err := lfguard.Configure(map[string]any{"prefixes": []string{"10.0.0.0/8"}, "proxy-only": true}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = lfguard.Configure(nil) })
	for _, adapter := range []*Direct{NewDirect(), NewCompatible(), NewDirectWithOption(DirectOption{Name: "renamed"})} {
		for _, address := range []string{"127.0.0.1", "::1"} {
			metadata := &C.Metadata{Type: C.HTTP, DstIP: netip.MustParseAddr(address), DstPort: 9}
			if conn, err := adapter.DialContext(context.Background(), metadata); err == nil || !strings.Contains(err.Error(), "refuses application DIRECT") {
				if conn != nil {
					_ = conn.Close()
				}
				t.Fatal("TCP was not stopped by managed policy", err)
			}
			metadata.NetWork = C.UDP
			if conn, err := adapter.ListenPacketContext(context.Background(), metadata); err == nil || !strings.Contains(err.Error(), "refuses application DIRECT") {
				if conn != nil {
					_ = conn.Close()
				}
				t.Fatal("UDP was not stopped by managed policy", err)
			}
		}
	}
}
