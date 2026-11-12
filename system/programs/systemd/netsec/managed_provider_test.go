package provider

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/metacubex/mihomo/component/lfguard"
	"github.com/metacubex/mihomo/component/resource"
)

func lfProviderPayload(name string, insecure bool) []byte {
	return []byte(fmt.Sprintf("proxies:\n  - {name: %s, type: socks5, server: 127.0.0.1, port: 1080, skip-cert-verify: %t}\n", name, insecure))
}

func lfProviderFixture(t *testing.T, cached, remote []byte, fallback bool) (*ProxySetProvider, string, *atomic.Value) {
	t.Helper()
	if err := lfguard.Configure(map[string]any{"prefixes": []string{"10.0.0.0/8"}}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = lfguard.Configure(nil) })
	body := new(atomic.Value)
	body.Store(remote)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write(body.Load().([]byte))
	}))
	t.Cleanup(server.Close)
	path := filepath.Join(t.TempDir(), "provider.yaml")
	if err := os.WriteFile(path, cached, 0600); err != nil {
		t.Fatal(err)
	}
	parser, err := NewProxiesParser("fixture", nil, "", "", "", "", overrideSchema{}, "")
	if err != nil {
		t.Fatal(err)
	}
	var payload []map[string]any
	if fallback {
		payload = []map[string]any{{"name": "fallback", "type": "socks5", "server": "127.0.0.1", "port": 1080}}
	}
	vehicle := resource.NewHTTPVehicle(server.URL, path, "", nil, 2*time.Second, 1<<20)
	provider, err := NewProxySetProvider("fixture", 0, payload, parser, vehicle, NewHealthCheck(nil, "", 0, 0, true, nil))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = provider.Close() })
	return provider, path, body
}

func TestLFManagedProviderRefreshPreservesLastValidNodesAndCache(t *testing.T) {
	safe := lfProviderPayload("safe", false)
	provider, path, body := lfProviderFixture(t, safe, lfProviderPayload("unsafe", true), false)
	if err := provider.Initial(); err != nil {
		t.Fatal(err)
	}
	if err := provider.Update(); err == nil {
		t.Fatal("insecure refresh accepted")
	}
	cache, _ := os.ReadFile(path)
	if provider.Count() != 1 || provider.Proxies()[0].Name() != "safe" || string(cache) != string(safe) {
		t.Fatal("failed refresh replaced the last valid state")
	}
	body.Store(lfProviderPayload("recovered", false))
	if err := provider.Update(); err != nil {
		t.Fatal(err)
	}
	if provider.Proxies()[0].Name() != "recovered" {
		t.Fatal("corrected source did not recover")
	}
}

func TestLFManagedProviderInvalidCacheFallsBackToRemote(t *testing.T) {
	safe := lfProviderPayload("remote", false)
	provider, path, _ := lfProviderFixture(t, lfProviderPayload("old-unsafe", true), safe, false)
	if err := provider.Initial(); err != nil {
		t.Fatal(err)
	}
	cache, _ := os.ReadFile(path)
	if provider.Count() != 1 || provider.Proxies()[0].Name() != "remote" || string(cache) != string(safe) {
		t.Fatal("valid remote did not replace rejected cold cache")
	}
}

func TestLFManagedProviderFailedInitialRetainsConfiguredFallback(t *testing.T) {
	for _, fallback := range []bool{false, true} {
		t.Run(fmt.Sprint(fallback), func(t *testing.T) {
			bad := lfProviderPayload("unsafe", true)
			provider, _, body := lfProviderFixture(t, bad, bad, fallback)
			if err := provider.Initial(); err == nil {
				t.Fatal("insecure initial accepted")
			}
			want := 0
			if fallback {
				want = 1
			}
			if provider.Count() != want {
				t.Fatalf("count = %d, want %d", provider.Count(), want)
			}
			body.Store(lfProviderPayload("recovered", false))
			if err := provider.Update(); err != nil {
				t.Fatal(err)
			}
			if provider.Count() != 1 {
				t.Fatal("provider did not recover after source correction")
			}
		})
	}
}
