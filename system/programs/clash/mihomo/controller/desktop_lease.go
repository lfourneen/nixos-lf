package route

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/metacubex/http"
	"github.com/metacubex/mihomo/component/lfguard"
	"golang.org/x/sys/unix"
)

type desktopGeneration struct {
	Boot     string `json:"boot_id"`
	Digest   string `json:"digest"`
	Revoked  string `json:"authorization_epoch"`
	Version  int64  `json:"policy_version"`
	Route    int64  `json:"routing_epoch"`
	Policy   string `json:"routing_policy_digest"`
	Egress   string `json:"egress_requested"`
	Exit     int64  `json:"egress_epoch"`
}

func desktopAuthority(root, snapshot string, now float64) (desktopGeneration, bool) {
	var want desktopGeneration
	read := func(path string, dst any) bool {
		data, err := os.ReadFile(filepath.Join(root, path))
		return err == nil && json.Unmarshal(data, dst) == nil
	}
	absent := func(path string) bool {
		_, err := os.Lstat(filepath.Join(root, path))
		return errors.Is(err, os.ErrNotExist)
	}
	text := func(path string) string {
		data, _ := os.ReadFile(filepath.Join(root, "proxy-mode", path))
		return strings.TrimSpace(string(data))
	}
	var state struct {
		desktopGeneration
		Mode      string `json:"mode"`
		Requested string `json:"routing_requested"`
		Applied   string `json:"routing_applied"`
		Phase     string `json:"routing_phase"`
		Seconds   *int   `json:"authorization_lease_seconds"`
	}
	var source struct {
		Boot     string `json:"boot_id"`
		Snapshot string `json:"snapshot"`
	}
	if snapshot == "" || now < 0 || text("intent") != "proxy" || !absent("dns-policy/sleeping") ||
		!absent("netsec/failed") ||
		!read("proxy-mode/state.json", &state) || state.Mode != "proxy" || state.Version <= 0 ||
		!read("proxy-mode/source.json", &source) || source.Snapshot != snapshot {
		return want, false
	}
	want = state.desktopGeneration
	want.Boot, want.Digest = text("boot-id"), snapshot
	if want.Boot == "" || source.Boot != want.Boot {
		return want, false
	}
	want.Revoked = text("authorization-epoch")
	if want.Revoked == "" {
		if !absent("proxy-mode/authorization-epoch") {
			return want, false
		}
		want.Revoked = "0"
	}
	if state.Requested != "" {
		var selection struct {
			desktopGeneration
			Requested string `json:"routing_requested"`
			Applied   string `json:"routing_applied"`
			Phase     string `json:"routing_phase"`
		}
		if state.Phase != "ready" || state.Requested != state.Applied ||
			(state.Requested != "rules" && state.Requested != "proxy-only") ||
			!read("proxy-mode/routing.json", &selection) || selection.Boot != want.Boot ||
			selection.Requested != state.Requested || selection.Applied != state.Applied ||
			selection.Phase != "ready" || selection.Route != want.Route || selection.Policy != want.Policy ||
			want.Route <= 0 || want.Policy == "" {
			return want, false
		}
		if state.Requested == "rules" {
			want.Egress, want.Exit = "", 0
		} else if want.Egress == "" || want.Exit <= 0 ||
			selection.Egress != want.Egress || selection.Exit != want.Exit {
			return want, false
		}
	} else if !absent("proxy-mode/routing.json") {
		return want, false
	}
	var verified struct {
		Boot    string  `json:"boot_id"`
		Version int64   `json:"version"`
		Result  string  `json:"result"`
	}
	if !read("netsec/state.json", &verified) || verified.Boot != want.Boot || verified.Version != want.Version ||
		(verified.Result != "ok" && verified.Result != "degraded") {
		return want, false
	}
	if state.Seconds == nil {
		// In deployments without lease expiry, current B policy remains authoritative.
		return want, true
	}
	var lease struct {
		desktopGeneration
		Until float64 `json:"until"`
	}
	return want, read("proxy-mode/authorization.json", &lease) &&
		lease.desktopGeneration == want && lease.Until > now
}

type desktopLease struct {
	sync.Mutex
	read func() (desktopGeneration, bool)
	binding desktopGeneration
	revoked chan struct{}
	active int
	watching bool
}

func (guard *desktopLease) revoke() {
	if guard.revoked != nil {
		close(guard.revoked)
		guard.revoked = nil
	}
}

func (guard *desktopLease) check() bool {
	current, ok := guard.read()
	if !ok || current != guard.binding {
		guard.revoke()
	}
	if ok && guard.revoked == nil {
		guard.revoked = make(chan struct{})
		guard.binding = current
	}
	return ok
}

func (guard *desktopLease) monitor() {
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for range ticker.C {
		guard.Lock()
		if guard.active == 0 {
			guard.revoke()
			guard.watching = false
			guard.Unlock()
			return
		}
		guard.check()
		guard.Unlock()
	}
}

func (guard *desktopLease) begin(parent context.Context) (context.Context, func(), bool) {
	guard.Lock()
	defer guard.Unlock()
	if !guard.check() {
		return nil, nil, false
	}
	if !guard.watching {
		guard.watching = true
		go guard.monitor()
	}
	guard.active++
	revoked := guard.revoked
	ctx, cancel := context.WithCancel(parent)
	go func() {
		select {
		case <-revoked:
			cancel()
		case <-ctx.Done():
		}
	}()
	return ctx, func() {
		cancel()
		guard.Lock()
		guard.active--
		guard.Unlock()
	}, true
}

var desktopController = desktopLease{read: func() (desktopGeneration, bool) {
	var ts unix.Timespec
	if unix.ClockGettime(unix.CLOCK_BOOTTIME, &ts) != nil {
		return desktopGeneration{}, false
	}
	return desktopAuthority("/run", os.Getenv("LF_CONTROLLER_SNAPSHOT"),
		float64(ts.Sec)+float64(ts.Nsec)/1e9)
}}

func lfRecoveryDNSAllowed(name, qtype string) bool {
	if qtype != "" && qtype != "A" {
		return false
	}
	var targets []string
	if json.Unmarshal([]byte(os.Getenv("LF_CONTROLLER_DNS_TARGETS")), &targets) != nil {
		return false
	}
	for _, target := range targets {
		if parsed, err := url.Parse(target); err == nil && parsed.Hostname() != "" {
			target = parsed.Hostname()
		}
		if strings.EqualFold(strings.TrimSuffix(name, "."), strings.TrimSuffix(target, ".")) {
			return name != ""
		}
	}
	return false
}

func lfDesktopRequest(next http.HandlerFunc, dns bool) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if !lfguard.Managed() {
			next(w, r)
			return
		}
		if root, _ := r.Context().Value(lfControllerRoot{}).(bool); root {
			if dns && !lfRecoveryDNSAllowed(r.URL.Query().Get("name"), r.URL.Query().Get("type")) {
				lfRefuseDelay(w, r, http.StatusForbidden, "DNS recovery target is not authorized")
				return
			}
			next(w, r)
			return
		}
		ctx, stop, ok := desktopController.begin(r.Context())
		if !ok {
			lfRefuseDelay(w, r, http.StatusForbidden, "desktop network requests require active proxy authorization")
			return
		}
		defer stop()
		if dns {
			ctx = lfguard.WithDesktopDNS(ctx)
		}
		next(w, r.WithContext(ctx))
	}
}
