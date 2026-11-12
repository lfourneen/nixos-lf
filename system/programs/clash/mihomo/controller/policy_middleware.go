package route

// Control-plane policy for the managed core.
//
// The controller socket is reachable by the desktop user. The managed profile
// already pins the TUN, ports and routing mark, but a same-user process could
// still replace the configuration, switch transparent capture off or upgrade
// binaries. This middleware restricts the controller API to the operations the
// GUI needs at runtime:
//
//   - proxy selection (/proxies) and connection closing (/connections) stay;
//   - tunnel mode may switch between rule and global, never direct;
//   - configuration loads, geodata updates, restarts, core self-updates and
//     rule-provider content updates are denied. A new configuration is
//     authorized only by clash-on, which restarts the core through the launcher.

import (
	"bytes"
	"encoding/json"
	"io"
	"os"
	"strconv"
	"strings"

	"github.com/metacubex/chi"
	"github.com/metacubex/http"
)

// lfPolicyUse installs the policy only when the packaged core carries the
// managed policy. Development builds without LF_MANAGED_PROXY_POLICY keep the
// upstream controller API.
func lfPolicyUse(r chi.Router) {
	if os.Getenv("LF_MANAGED_PROXY_POLICY") == "" {
		return
	}
	r.Use(lfPolicyMiddleware)
}

func lfPolicyMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if status, message := lfPolicyViolation(r); status != 0 {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(status)
			_, _ = io.WriteString(w, `{"message":`+strconv.Quote(message)+`}`)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func lfPolicyViolation(r *http.Request) (int, string) {
	path := r.URL.Path
	if len(path) > 1 {
		path = strings.TrimSuffix(path, "/")
	}

	// Deny unknown writes as well as the pinned version's known mutation APIs.
	// Reads include the GUI's delay/health-check requests. Proxy selection and
	// connection termination are the only general runtime writes we authorize.
	switch {
	case r.Method == http.MethodGet || r.Method == http.MethodHead || r.Method == http.MethodOptions:
		return 0, ""
	case r.Method == http.MethodPatch && path == "/configs":
		return lfPolicyPatch(r)
	case (r.Method == http.MethodPut || r.Method == http.MethodDelete) && strings.HasPrefix(path, "/proxies/"):
		return 0, ""
	case r.Method == http.MethodDelete && (path == "/connections" || strings.HasPrefix(path, "/connections/")):
		return 0, ""
	}
	return http.StatusForbidden, "managed proxy policy rejects this runtime mutation; run clash-on to authorize configuration changes"
}

// lfPolicyPatch allows the GUI's rule/global switch and nothing else. In
// particular a mode=direct patch would route every flow DIRECT under the
// core's exempt mark while the TUN is still up, which is exactly the leak the
// mode gate exists to prevent.
func lfPolicyPatch(r *http.Request) (int, string) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		return http.StatusBadRequest, "invalid configuration patch"
	}
	r.Body = io.NopCloser(bytes.NewReader(body))
	if len(bytes.TrimSpace(body)) == 0 {
		return 0, ""
	}
	var patch map[string]json.RawMessage
	if err = json.Unmarshal(body, &patch); err != nil {
		return http.StatusBadRequest, "invalid configuration patch"
	}
	for key := range patch {
		if key != "mode" {
			return http.StatusForbidden, "managed proxy policy rejects patching " + key
		}
	}
	raw, ok := patch["mode"]
	if !ok {
		return 0, ""
	}
	var mode string
	if err = json.Unmarshal(raw, &mode); err != nil {
		return http.StatusBadRequest, "invalid tunnel mode"
	}
	switch mode {
	case "rule", "global":
		return 0, ""
	default:
		return http.StatusForbidden, "managed proxy policy rejects tunnel mode " + mode
	}
}
