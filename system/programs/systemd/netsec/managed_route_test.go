package route

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/metacubex/http"
	"github.com/metacubex/http/httptest"
	"github.com/metacubex/mihomo/listener"
)

func TestLFManagedTunPatchRouting(t *testing.T) {
	path := os.Getenv("LF_MANAGED_TEST_POLICY")
	if path == "" {
		t.Skip("Nix integration policy not supplied")
	}
	t.Setenv("LF_MANAGED_PROXY_POLICY", path)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	policy := map[string]any{}
	if err = json.Unmarshal(data, &policy); err != nil {
		t.Fatal(err)
	}
	for key, value := range map[string]any{
		"iproute2-rule-index":                        40000,
		"auto-redirect-input-mark":                   6666,
		"auto-redirect-output-mark":                  6666,
		"auto-redirect-iproute2-fallback-rule-index": 52,
		"file-descriptor":                            123,
	} {
		expected, pinned := policy["tun."+key]
		if !pinned {
			continue
		}
		for _, candidate := range []any{expected, value} {
			body, err := json.Marshal(map[string]any{"tun": map[string]any{"enable": policy["tun.enable"], key: candidate}})
			if err != nil {
				t.Fatal(err)
			}
			recorder := httptest.NewRecorder()
			_, ok := lfDecodeManagedPatch(recorder, httptest.NewRequest(http.MethodPatch, "/", strings.NewReader(string(body))))
			if ok != reflect.DeepEqual(candidate, expected) {
				t.Fatalf("wrong PATCH decision for %s=%v", key, candidate)
			}
		}
	}
}

func TestLFManagedPatchHandler(t *testing.T) {
	path := filepath.Join(t.TempDir(), "policy.json")
	if err := os.WriteFile(path, []byte(`{"port":0,"mixed-port":7897,"allow-lan":false,"tun.enable":true,"tun.dns-mode":"hijack","tun.include-uid":[],"tuic-server.enable":false}`), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("LF_MANAGED_PROXY_POLICY", path)
	for _, body := range []string{
		`{"port":18080}`, `{"PORT":18080}`, `{"PORT":0}`,
		`{"mixed-port":7897,"Mixed-Port":18080}`,
		`{"Tun":{"enable":false}}`, `{"tun":{"Enable":true}}`,
		`{"tun":{"include-uid":[1000]}}`, `{"tun":{}}`,
		`{"tun":{"enable":true,"dns-mode":"native"}}`,
		`{"tun":{"enable":true,"dns-mode":"disabled"}}`,
		`{"tun":{"enable":true,"dns-mode":""}}`,
		`{"tun":{"enable":true,"DNS-MODE":"hijack"}}`,
		`{"tun":{"enable":true,"include-uid":[1000]},"tun":{"enable":true}}`,
		`{"tuic-server":{"enable":true,"listen":"127.0.0.1:18080"},"tuic-server":{"listen":"127.0.0.1:18080"}}`,
		`{"port":0,"port":18080}`, `{"port":18080,"port":0}`,
		`null`, `[]`, `{} {}`, `{"tun":`,
	} {
		t.Run(body, func(t *testing.T) {
			before := listener.GetPorts()
			recorder := httptest.NewRecorder()
			request := httptest.NewRequest(http.MethodPatch, "/", strings.NewReader(body))
			configRouter().ServeHTTP(recorder, request)
			if recorder.Code != http.StatusBadRequest {
				t.Fatalf("got status %d", recorder.Code)
			}
			if !reflect.DeepEqual(before, listener.GetPorts()) {
				t.Fatal("rejected request changed listener ports")
			}
		})
	}
	for _, body := range []string{`{}`, `{"mode":"rule","port":null,"tun":null}`, `{"port":0,"allow-lan":false}`, `{"tun":{"enable":true,"dns-mode":"hijack"}}`} {
		recorder := httptest.NewRecorder()
		configRouter().ServeHTTP(recorder, httptest.NewRequest(http.MethodPatch, "/", strings.NewReader(body)))
		if recorder.Code != http.StatusNoContent {
			t.Fatalf("valid PATCH %s: status %d", body, recorder.Code)
		}
	}
}
