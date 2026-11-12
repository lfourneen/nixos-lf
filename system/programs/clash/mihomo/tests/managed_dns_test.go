package tunnel_test

import (
	"context"
	"net"
	"strings"
	"testing"

	"github.com/metacubex/mihomo/adapter"
	"github.com/metacubex/mihomo/adapter/outbound"
	"github.com/metacubex/mihomo/component/lfguard"
	C "github.com/metacubex/mihomo/constant"
	RC "github.com/metacubex/mihomo/rules/common"
	"github.com/metacubex/mihomo/tunnel"
)

func TestLFManagedRoutedDNSCannotSelectDirect(t *testing.T) {
	if err := lfguard.Configure(map[string]any{"prefixes": []string{"10.0.0.0/8"}, "proxy-only": true}); err != nil {
		t.Fatal(err)
	}
	oldMode, oldProxies, oldRules := tunnel.Mode(), tunnel.Proxies(), tunnel.Rules()
	oldProviders, oldRuleProviders := tunnel.Providers(), tunnel.RuleProviders()
	defer func() {
		tunnel.SetMode(oldMode)
		tunnel.UpdateProxies(oldProxies, oldProviders)
		tunnel.UpdateRules(oldRules, nil, oldRuleProviders)
		_ = lfguard.Configure(nil)
	}()
	rule, err := RC.NewIPCIDR("1.1.1.1/32", "DIRECT")
	if err != nil {
		t.Fatal(err)
	}
	tunnel.SetMode(tunnel.Rule)
	tunnel.UpdateProxies(map[string]C.Proxy{"DIRECT": adapter.NewProxy(outbound.NewDirect())}, nil)
	tunnel.UpdateRules([]C.Rule{rule}, nil, nil)
	d := tunnel.NewDNSDialer(nil, nil, tunnel.DnsRespectRules)
	ctx := context.Background()
	for _, network := range []string{"tcp", "udp"} {
		conn, err := d.DialContext(ctx, network, "1.1.1.1:53")
		if conn != nil {
			conn.Close()
		}
		if err == nil || !strings.Contains(err.Error(), "rule-routed DNS DIRECT") {
			t.Fatal(network, "DNS rule bypass", err)
		}
	}
	pc, err := d.ListenPacket(ctx, "udp", "1.1.1.1:53")
	if pc != nil {
		pc.Close()
	}
	if err == nil || !strings.Contains(err.Error(), "rule-routed DNS DIRECT") {
		t.Fatal("DNS packet dial bypass", err)
	}
	// Explicit internal bootstrap/MagicDNS remains separate from rule routing.
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	bootstrap := tunnel.NewDNSDialer(nil, nil, "DIRECT")
	conn, err := bootstrap.DialContext(ctx, "tcp", listener.Addr().String())
	if err != nil {
		t.Fatal("explicit bootstrap was blocked", err)
	}
	conn.Close()
}
