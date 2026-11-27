package route

import (
	"context"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/metacubex/mihomo/component/lfguard"
)

func TestLFManagedDesktopRequiresCurrentB(t *testing.T) {
	root := t.TempDir()
	write := func(path, value string) {
		t.Helper()
		path = filepath.Join(root, path)
		if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(value), 0644); err != nil {
			t.Fatal(err)
		}
	}
	write("proxy-mode/intent", "proxy\n")
	write("proxy-mode/boot-id", "boot\n")
	write("proxy-mode/source.json", `{"boot_id":"boot","snapshot":"current"}`)
	write("netsec/state.json", `{"boot_id":"boot","version":4,"result":"ok"}`)
	state := `{"mode":"proxy","policy_version":4,"authorization_lease_seconds":60,"routing_epoch":2,"routing_policy_digest":"policy","routing_requested":"rules","routing_applied":"rules","routing_phase":"ready","egress_epoch":7}`
	write("proxy-mode/state.json", state)
	write("proxy-mode/routing.json", `{"boot_id":"boot","routing_epoch":2,"routing_policy_digest":"policy","routing_requested":"rules","routing_applied":"rules","routing_phase":"ready"}`)
	lease := `{"boot_id":"boot","digest":"current","authorization_epoch":"0","policy_version":4,"routing_epoch":2,"routing_policy_digest":"policy","until":250}`
	write("proxy-mode/authorization.json", lease)
	if _, ok := desktopAuthority(root, "current", 249); !ok {
		t.Fatal("active Rules B was refused")
	}
	if _, ok := desktopAuthority(root, "current", 250); ok {
		t.Fatal("accepted a lease at its expiration")
	}
	if _, ok := desktopAuthority(root, "old", 249); ok {
		t.Fatal("previous core snapshot could make active requests")
	}
	write("proxy-mode/authorization-epoch", "1\n")
	if _, ok := desktopAuthority(root, "current", 249); ok {
		t.Fatal("accepted a revoked lease")
	}
	write("proxy-mode/authorization-epoch", "0\n")
	write("proxy-mode/state.json", `{"mode":"blocked","policy_version":4,"authorization_lease_seconds":60}`)
	if _, ok := desktopAuthority(root, "current", 249); ok {
		t.Fatal("accepted a live-looking lease in C")
	}
	write("proxy-mode/state.json", state)
	write("dns-policy/sleeping", "")
	if _, ok := desktopAuthority(root, "current", 249); ok {
		t.Fatal("accepted a lease during sleep")
	}
}

func TestLFManagedDesktopRequestSurvivesRenewalButCancelsOnRevoke(t *testing.T) {
	var version atomic.Int64
	version.Store(1)
	guard := desktopLease{read: func() (desktopGeneration, bool) {
		value := version.Load()
		return desktopGeneration{Version: value}, value != 0
	}}
	ctx, stop, ok := guard.begin(context.Background())
	if !ok {
		t.Fatal("valid generation was refused")
	}
	defer stop()
	second, finish, ok := guard.begin(context.Background())
	if !ok {
		t.Fatal("another request in the same generation was refused")
	}
	defer finish()
	if ctx.Err() != nil || second.Err() != nil {
		t.Fatal("same generation canceled a live request")
	}
	version.Store(0)
	select {
	case <-ctx.Done():
	case <-time.After(time.Second):
		t.Fatal("request remained active after revoke")
	}
	select {
	case <-second.Done():
	case <-time.After(time.Second):
		t.Fatal("shared guard did not cancel the second request")
	}
	if context.Cause(ctx) != lfguard.ErrDesktopRevoked || context.Cause(second) != lfguard.ErrDesktopRevoked {
		t.Fatal("lease revocation lost its cause")
	}
	if _, _, ok := guard.begin(context.Background()); ok {
		t.Fatal("revoked generation admitted a new request")
	}
}

func TestLFManagedRecoveryDNSUsesConfiguredTargets(t *testing.T) {
	t.Setenv("LF_CONTROLLER_DNS_TARGETS", `["https://probe.example:8443/path?fixed=1","tun.example"]`)
	if !lfRecoveryDNSAllowed("Probe.Example.", "A") || !lfRecoveryDNSAllowed("tun.example", "") ||
		lfRecoveryDNSAllowed("probe.example.other", "A") || lfRecoveryDNSAllowed("probe.example", "TXT") {
		t.Fatal("unexpected DNS recovery authority")
	}
}
