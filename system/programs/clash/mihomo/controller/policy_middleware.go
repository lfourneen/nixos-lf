package route

// The packaged controller exposes reads, proxy selection and connection closing.
// Core mode and TUN belong to the system controller. The GUI may still send its
// normal control requests: accepting them as silent no-ops keeps its local
// preference path from showing an error or falling back to a core restart.
// Configuration replacement is discarded rather than applied; unknown writes
// stay forbidden.

import (
	"bytes"
	"encoding/json"
	"io"
	"strconv"
	"strings"

	"github.com/metacubex/chi"
	"github.com/metacubex/http"
)

// Managed PATCH fields are local/subsystem preferences, never core state.
var lfManagedPatchFields = map[string]struct{}{
	"mode": {},
	"tun":  {},
}

func lfPolicyUse(r chi.Router) {
	r.Use(lfPolicyMiddleware)
}

func lfPolicyMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		status, message, discard := lfPolicyDisposition(r)
		if discard {
			w.WriteHeader(status)
			return
		}
		if status != 0 {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(status)
			_, _ = io.WriteString(w, `{"message":`+strconv.Quote(message)+`}`)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func lfPolicyDisposition(r *http.Request) (int, string, bool) {
	path := r.URL.Path
	if len(path) > 1 {
		path = strings.TrimSuffix(path, "/")
	}

	// Reads include the GUI's delay/health-check requests. Proxy selection and
	// connection termination are the only general runtime writes we authorize.
	switch {
	case r.Method == http.MethodGet || r.Method == http.MethodHead || r.Method == http.MethodOptions:
		return 0, "", false
	case r.Method == http.MethodPatch && path == "/configs":
		return lfPolicyPatch(r)
	case r.Method == http.MethodPut && path == "/configs":
		// The GUI reloads its generated profile with PUT /configs. Applying it
		// would let a local preference replace the system-rendered profile and
		// a rejected reload triggers the GUI's restart fallback. Discard the
		// request and report success; the root controller owns real reloads.
		return http.StatusNoContent, "", true
	case (r.Method == http.MethodPut || r.Method == http.MethodDelete) && strings.HasPrefix(path, "/proxies/"):
		return 0, "", false
	case r.Method == http.MethodDelete && (path == "/connections" || strings.HasPrefix(path, "/connections/")):
		return 0, "", false
	}
	return http.StatusForbidden, "managed proxy policy rejects this runtime mutation; run clash-on to authorize configuration changes", false
}

// PATCH /configs is the GUI's mode and TUN toggle path. A mixed patch with any
// other field is not a control write and stays forbidden.
func lfPolicyPatch(r *http.Request) (int, string, bool) {
	body, err := io.ReadAll(io.LimitReader(r.Body, (1<<20)+1))
	if err != nil || len(body) > 1<<20 {
		return http.StatusBadRequest, "invalid configuration patch", false
	}
	r.Body = io.NopCloser(bytes.NewReader(body))
	if len(bytes.TrimSpace(body)) == 0 {
		return http.StatusNoContent, "", true
	}
	var patch map[string]json.RawMessage
	if err = json.Unmarshal(body, &patch); err != nil || patch == nil {
		return http.StatusBadRequest, "invalid configuration patch", false
	}
	for key := range patch {
		if _, ok := lfManagedPatchFields[key]; !ok {
			return http.StatusForbidden, "configuration is managed by the system controller; GUI configuration writes are forbidden", false
		}
	}
	return http.StatusNoContent, "", true
}
