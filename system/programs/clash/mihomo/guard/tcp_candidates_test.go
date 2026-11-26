package lfguard

import (
	"context"
	"net"
	"net/netip"
	"testing"
	"time"

	C "github.com/metacubex/mihomo/constant"
)

func TestRaceTCPDoesNotWaitForUnreachableFirstFamily(t *testing.T) {
	first, second := netip.MustParseAddr("8.8.8.8"), netip.MustParseAddr("2606:4700:4700::1111")
	done := make(chan struct{})
	ctx, cancel := context.WithTimeout(context.Background(), 1500*time.Millisecond)
	defer cancel()
	conn, winner, err := raceTCP(ctx, []netip.Addr{first, second}, func(ctx context.Context, ip netip.Addr) (net.Conn, error) {
		if ip == first {
			<-ctx.Done()
			close(done)
			return nil, ctx.Err()
		}
		client, peer := net.Pipe()
		_ = peer.Close()
		return client, nil
	})
	if err != nil || winner != second {
		t.Fatalf("expected alternate to connect within caller's budget: winner=%v err=%v", winner, err)
	}
	_ = conn.Close()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("losing dial was not cancelled")
	}
}

type recordingNodeDialer struct {
	C.Dialer
	address string
}

func (d *recordingNodeDialer) DialContext(_ context.Context, _ string, address string) (net.Conn, error) {
	d.address = address
	return nil, nil
}

func TestNodeAlternatesPreserveFamilyRestrictionAndOnlyReplacePrimaryTCP(t *testing.T) {
	if err := Configure(map[string]any{
		"prefixes": []string{"100.64.0.0/10"}, "require-protected-node-transport": true,
		"nat64-prefixes": []string{"2600:1234:4321::/96"},
	}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = Configure(nil) })
	node := map[string]any{"server": "8.8.8.8", "type": "trojan",
		"lf-node-alternates": []any{"2606:4700:4700::1111"}}
	ips, err := ParseNodeAlternates(node)
	if err != nil {
		t.Fatal(err)
	}
	underlying := &recordingNodeDialer{}
	d := WithNodeAlternates(underlying, ips, 443, C.IPv6Only)
	for _, check := range []struct{ network, address, want string }{
		{"tcp", "8.8.8.8:443", "[2606:4700:4700::1111]:443"},
		{"tcp", "8.8.8.8:444", "8.8.8.8:444"},
		{"udp", "8.8.8.8:443", "8.8.8.8:443"},
	} {
		if _, err := d.DialContext(context.Background(), check.network, check.address); err != nil ||
			underlying.address != check.want {
			t.Fatalf("%+v: got %q, %v", check, underlying.address, err)
		}
	}
	for _, address := range []string{"8.8.8.8", "127.0.0.1", "169.254.169.254", "100.64.0.1", "2600:1234:4321::808:808"} {
		node["lf-node-alternates"] = []any{address}
		if _, err := ParseNodeAlternates(node); err == nil {
			t.Fatalf("invalid alternate %q accepted", address)
		}
	}
}
