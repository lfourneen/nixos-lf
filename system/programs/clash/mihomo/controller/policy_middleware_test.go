package route

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/metacubex/http"
	"github.com/metacubex/mihomo/component/lfguard"
)

type lfPolicyRecorder struct {
	code int
	body bytes.Buffer
}

func TestLFManagedSingleEgressKeepsSelectionRootOwned(t *testing.T) {
	if err := lfguard.Configure(map[string]any{"prefixes": []string{"10.0.0.0/8"}, "proxy-only": true, "single-egress": true}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = lfguard.Configure(nil) })
	for _, method := range []string{http.MethodPut, http.MethodDelete} {
		if status, called := lfPolicyRun(t, method, "/proxies/lf-remote", `{"name":"other"}`); status != http.StatusForbidden || called {
			t.Fatalf("selection escaped root control: %d, %v", status, called)
		}
	}
	if status, called := lfPolicyRun(t, http.MethodGet, "/proxies", ""); status != http.StatusOK || !called {
		t.Fatal("read rejected")
	}
}

func (r *lfPolicyRecorder) Header() http.Header         { return http.Header{} }
func (r *lfPolicyRecorder) Write(p []byte) (int, error) { return r.body.Write(p) }
func (r *lfPolicyRecorder) WriteHeader(code int)        { r.code = code }

func lfPolicyRun(t *testing.T, method, path, body string) (int, bool) {
	t.Helper()
	called := false
	next := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		w.WriteHeader(http.StatusOK)
	})
	req, err := http.NewRequest(method, path, strings.NewReader(body))
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	rec := &lfPolicyRecorder{}
	lfPolicyMiddleware(next).ServeHTTP(rec, req)
	code := rec.code
	if code == 0 {
		code = http.StatusOK
	}
	return code, called
}

func TestLFManagedPolicyAllowsReadsAndProxySelection(t *testing.T) {
	for _, tc := range []struct{ method, path, body string }{
		{http.MethodGet, "/version", ""},
		{http.MethodGet, "/configs", ""},
		{http.MethodGet, "/proxies", ""},
		{http.MethodPut, "/proxies/Node", `{"name":"Node"}`},
		{http.MethodDelete, "/connections", ""},
		{http.MethodDelete, "/connections/connection-id", ""},
		{http.MethodDelete, "/proxies/Automatic", ""},
		{http.MethodGet, "/providers/rules", ""},
	} {
		code, called := lfPolicyRun(t, tc.method, tc.path, tc.body)
		if code != http.StatusOK || !called {
			t.Fatalf("%s %s: code=%d called=%v, want allowed", tc.method, tc.path, code, called)
		}
	}
}

func TestLFManagedPolicyControlWritesAreSilentNoOps(t *testing.T) {
	directory := t.TempDir()
	for name, content := range map[string]string{
		"rules":      `{"lf-private-targets":{"proxy-only":false}}`,
		"proxy-only": `{"lf-private-targets":{"proxy-only":true}}`,
		"corrupt":    `{`,
		"null":       `null`,
	} {
		if err := os.WriteFile(filepath.Join(directory, name), []byte(content), 0600); err != nil {
			t.Fatal(err)
		}
	}
	for _, state := range []string{"rules", "proxy-only", "missing", "corrupt", "null", "unset"} {
		t.Run(state, func(t *testing.T) {
			policy := filepath.Join(directory, state)
			if state == "unset" {
				policy = ""
			}
			t.Setenv("LF_MANAGED_PROXY_POLICY", policy)
			for _, body := range []string{
				"", "{}",
				`{"mode":"rule"}`, `{"mode":"global"}`, `{"mode":"direct"}`, `{"mode":null}`,
				`{"tun":{"enable":true}}`, `{"tun":{"enable":false}}`,
				`{"mode":"direct","tun":{"enable":true}}`,
			} {
				code, called := lfPolicyRun(t, http.MethodPatch, "/configs", body)
				if code != http.StatusNoContent || called {
					t.Fatalf("patch %q: code=%d called=%v, want a discarded 204", body, code, called)
				}
			}
			code, called := lfPolicyRun(t, http.MethodPut, "/configs",
				`{"path":"/tmp/gui-profile.yaml"}`)
			if code != http.StatusNoContent || called {
				t.Fatalf("config reload: code=%d called=%v, want a discarded 204", code, called)
			}
		})
	}
}

func TestLFManagedPolicyRejectsUnmanagedConfigurationWrites(t *testing.T) {
	for _, body := range []string{
		`{"port":7898}`,
		`{"mode":"rule","port":7898}`,
		`{"tun":{"enable":true},"sniffer":{"enable":false}}`,
		`not-json`,
	} {
		code, called := lfPolicyRun(t, http.MethodPatch, "/configs", body)
		if code == http.StatusNoContent || code == http.StatusOK || called {
			t.Fatalf("patch %q: code=%d called=%v, want rejected", body, code, called)
		}
	}
}

func TestLFManagedPolicyRejectsControlPlaneMutation(t *testing.T) {
	for _, tc := range []struct{ method, path, body string }{
		{http.MethodPost, "/configs/geo", ""},
		{http.MethodPost, "/restart", ""},
		{http.MethodPost, "/upgrade", ""},
		{http.MethodPost, "/upgrade/geo", ""},
		{http.MethodPost, "/upgrade/ui", ""},
		{http.MethodPut, "/providers/rules/ads", ""},
		{http.MethodPut, "/providers/rules/ads/", ""},
		{http.MethodPatch, "/rules/disable", `{"0":true}`},
		{http.MethodPatch, "/rules/disable/", `{"0":true}`},
		{http.MethodPut, "/providers/proxies/Subscription", ""},
		{http.MethodPut, "/storage/Key", `{"value":true}`},
		{http.MethodPost, "/unknown-future-write", ""},
	} {
		code, called := lfPolicyRun(t, tc.method, tc.path, tc.body)
		if code == http.StatusOK || called {
			t.Fatalf("%s %s: code=%d called=%v, want rejected", tc.method, tc.path, code, called)
		}
	}
}

// Use the real upstream router too: a middleware-only mock cannot establish
// that a newly exposed mutation route actually crosses the installed gate.
func TestLFManagedPolicyInstalledOnRouter(t *testing.T) {
	t.Setenv("LF_MANAGED_PROXY_POLICY", "managed-fixture")
	router := router(false, "", "", Cors{})
	for _, tc := range []struct{ method, path, body string }{
		{http.MethodPatch, "/configs", `{"mode":"global"}`},
		{http.MethodPut, "/configs", `{"path":"/tmp/profile.yaml"}`},
	} {
		req, err := http.NewRequest(tc.method, tc.path, strings.NewReader(tc.body))
		if err != nil {
			t.Fatal(err)
		}
		rec := &lfPolicyRecorder{}
		router.ServeHTTP(rec, req)
		if rec.code != http.StatusNoContent {
			t.Fatalf("actual router %s %s: code=%d, want 204", tc.method, tc.path, rec.code)
		}
	}
	for _, path := range []string{"/rules/disable", "/rules/disable/"} {
		req, err := http.NewRequest(http.MethodPatch, path, strings.NewReader(`{"0":true}`))
		if err != nil {
			t.Fatal(err)
		}
		rec := &lfPolicyRecorder{}
		router.ServeHTTP(rec, req)
		if rec.code != http.StatusForbidden {
			t.Fatalf("actual router %s: code=%d, want forbidden", path, rec.code)
		}
	}
}
