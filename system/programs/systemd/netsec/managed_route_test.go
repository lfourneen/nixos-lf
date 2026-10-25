package route

import (
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/metacubex/http"
	"github.com/metacubex/http/httptest"
	"github.com/metacubex/mihomo/listener"
)

func TestLFManagedPatchHandler(t *testing.T) {
	path := filepath.Join(t.TempDir(), "policy.json")
	if err := os.WriteFile(path, []byte(`{"port":0,"mixed-port":7897,"allow-lan":false,"tun.enable":true,"tun.include-uid":[],"tuic-server.enable":false}`), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("LF_MANAGED_PROXY_POLICY", path)
	for _, body := range []string{
		`{"port":18080}`, `{"PORT":18080}`, `{"PORT":0}`,
		`{"mixed-port":7897,"Mixed-Port":18080}`,
		`{"Tun":{"enable":false}}`, `{"tun":{"Enable":true}}`,
		`{"tun":{"include-uid":[1000]}}`, `{"tun":{}}`,
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
	for _, body := range []string{`{}`, `{"mode":"rule","port":null,"tun":null}`, `{"port":0,"allow-lan":false}`} {
		recorder := httptest.NewRecorder()
		configRouter().ServeHTTP(recorder, httptest.NewRequest(http.MethodPatch, "/", strings.NewReader(body)))
		if recorder.Code != http.StatusNoContent {
			t.Fatalf("valid PATCH %s: status %d", body, recorder.Code)
		}
	}
}
