package route

import (
	"bytes"
	"strings"
	"testing"

	"github.com/metacubex/http"
)

type lfPolicyRecorder struct {
	code int
	body bytes.Buffer
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

func TestLFManagedPolicyAllowsRuleGlobalCases(t *testing.T) {
	for _, body := range []string{
		`{"mode":"rule"}`,
		`{"mode":"global"}`,
		`{"mode": "rule"}`,
		``,
	} {
		code, called := lfPolicyRun(t, http.MethodPatch, "/configs", body)
		if code != http.StatusOK || !called {
			t.Fatalf("patch %q: code=%d called=%v, want allowed", body, code, called)
		}
	}
}

func TestLFManagedPolicyRejectsControlPlaneMutation(t *testing.T) {
	for _, tc := range []struct{ method, path, body string }{
		{http.MethodPut, "/configs", `{"path":"/tmp/x","payload":"mode: direct"}`},
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
		{http.MethodPatch, "/configs", `{"mode":"direct"}`},
		{http.MethodPatch, "/configs", `{"tun":{"enable":false}}`},
		{http.MethodPatch, "/configs", `{"mode":"rule","sniffer":{"enable":false}}`},
		{http.MethodPatch, "/configs", `not-json`},
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
	for _, path := range []string{"/rules/disable", "/rules/disable/"} {
		req, err := http.NewRequest(http.MethodPatch, path, strings.NewReader(`{"0":true}`))
		if err != nil { t.Fatal(err) }
		rec := &lfPolicyRecorder{}
		router.ServeHTTP(rec, req)
		if rec.code != http.StatusForbidden {
			t.Fatalf("actual router %s: code=%d, want forbidden", path, rec.code)
		}
	}
}
