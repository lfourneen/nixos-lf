package sing_tun

import (
	"net/netip"
	"testing"
	"time"

	"github.com/metacubex/mihomo/component/lfguard"
	M "github.com/metacubex/sing/common/metadata"
	N "github.com/metacubex/sing/common/network"
)

func TestLFManagedSingleEgressRefusesRealAndSyntheticEcho(t *testing.T) {
	if err := lfguard.Configure(map[string]any{"prefixes": []string{"10.0.0.0/8"}, "proxy-only": true, "single-egress": true}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = lfguard.Configure(nil) })
	for _, ip := range []string{"1.1.1.1", "2606:4700::1111", "198.18.0.1", "fdfe:dcba:9876::1"} {
		for _, disabled := range []bool{true, false} {
			handler := &ListenerHandler{DisableICMPForwarding: disabled}
			_, err := handler.PrepareConnection(N.NetworkICMP, M.Socksaddr{}, M.Socksaddr{Addr: netip.MustParseAddr(ip)}, nil, time.Second)
			if err == nil {
				t.Fatalf("accepted ICMP echo to %s (disable-forwarding=%v)", ip, disabled)
			}
		}
	}
}
