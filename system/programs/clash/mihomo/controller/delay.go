package route

import (
	"context"
	"errors"
	"net"
	"net/url"
	"strconv"
	"sync"
	"time"

	"github.com/metacubex/chi/render"
	"github.com/metacubex/http"
	"github.com/metacubex/mihomo/common/utils"
	"github.com/metacubex/mihomo/component/lfguard"
	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/transport/socks5"
	"golang.org/x/sys/unix"
)

type lfControllerRoot struct{}

// The GUI Unix relay connects as the desktop user. Only a direct root peer
// can request the separate health targets, regardless of request headers.
func lfControllerPeer(ctx context.Context, conn net.Conn) context.Context {
	socket, ok := conn.(*net.UnixConn)
	if !ok {
		return ctx
	}
	raw, err := socket.SyscallConn()
	if err != nil {
		return ctx
	}
	root := false
	err = raw.Control(func(fd uintptr) {
		cred, err := unix.GetsockoptUcred(int(fd), unix.SOL_SOCKET, unix.SO_PEERCRED)
		root = err == nil && cred.Uid == 0
	})
	if err == nil && root {
		return context.WithValue(ctx, lfControllerRoot{}, true)
	}
	return ctx
}

var (
	lfDelayRequests = make(chan struct{}, 256)
	lfDelayBatches  = struct {
		sync.Mutex
		active map[string]bool
	}{active: make(map[string]bool)}
)

func lfReserveDelay(batch string) func() {
	if batch == "" {
		select {
		case lfDelayRequests <- struct{}{}:
			return func() { <-lfDelayRequests }
		default:
			return nil
		}
	}
	lfDelayBatches.Lock()
	defer lfDelayBatches.Unlock()
	if lfDelayBatches.active[batch] || len(lfDelayBatches.active) >= 4 {
		return nil
	}
	lfDelayBatches.active[batch] = true
	return func() {
		lfDelayBatches.Lock()
		delete(lfDelayBatches.active, batch)
		lfDelayBatches.Unlock()
	}
}

type lfDelayCall struct {
	ctx      context.Context
	stop     func()
	target   string
	expected utils.IntRanges[uint16]
}

func (call *lfDelayCall) refuseRevoked(w http.ResponseWriter, r *http.Request) bool {
	if context.Cause(call.ctx) != lfguard.ErrDesktopRevoked {
		return false
	}
	lfRefuseDelay(w, r, http.StatusForbidden, lfguard.ErrDesktopRevoked.Error())
	return true
}

func lfSessionCheck(w http.ResponseWriter, r *http.Request) {
	root, _ := r.Context().Value(lfControllerRoot{}).(bool)
	if !root || len(r.URL.RawQuery) > 4096 {
		lfRefuseDelay(w, r, http.StatusForbidden, "session check is not authorized")
		return
	}
	target := r.URL.Query().Get("url")
	ctx, allowed := lfguard.WithSessionCheck(r.Context(), target)
	if !allowed {
		lfRefuseDelay(w, r, http.StatusForbidden, "session check is not authorized")
		return
	}
	proxy := r.Context().Value(CtxKeyProxy).(C.Proxy)
	switch proxy.Type() {
	case C.Shadowsocks, C.Socks5, C.Http, C.Vmess, C.Vless, C.Trojan, C.Hysteria2, C.Tuic, C.AnyTLS:
	default:
		lfRefuseDelay(w, r, http.StatusForbidden, "session check requires a remote leaf")
		return
	}
	ctx, cancel := context.WithTimeout(ctx, 1200*time.Millisecond)
	defer cancel()
	_, err := proxy.URLTest(ctx, target, nil)
	result := "ok"
	if err != nil {
		result = "session-unavailable"
		if errors.Is(err, socks5.ErrAuth) || err.Error() == "HTTP need auth" {
			result = "node-auth"
		}
	}
	render.JSON(w, r, render.M{"session": result})
}

func lfRefuseDelay(w http.ResponseWriter, r *http.Request, status int, message string) *lfDelayCall {
	render.Status(r, status)
	render.JSON(w, r, newError(message))
	return nil
}

func lfStartDelay(w http.ResponseWriter, r *http.Request, group string) *lfDelayCall {
	managed := lfguard.ManagedDelay()
	if managed && len(r.URL.RawQuery) > 4096 {
		return lfRefuseDelay(w, r, http.StatusBadRequest, "invalid delay test parameters")
	}
	query, queryErr := url.ParseQuery(r.URL.RawQuery)
	limit := 16
	if group != "" || managed {
		limit = 32
	}
	timeout, timeoutErr := strconv.ParseInt(query.Get("timeout"), 10, limit)
	expected, expectedErr := utils.NewUnsignedRanges[uint16](query.Get("expected"))
	if queryErr != nil || timeoutErr != nil || expectedErr != nil {
		return lfRefuseDelay(w, r, http.StatusBadRequest, "invalid delay test parameters")
	}
	ctx := r.Context()
	deadline := context.WithTimeout
	target := query.Get("url")
	release := func() {}
	if managed {
		if timeout <= 0 || len(query["url"]) != 1 || len(query["timeout"]) != 1 ||
			len(query["expected"]) > 1 || query.Get("expected") != "" {
			return lfRefuseDelay(w, r, http.StatusBadRequest, "invalid managed delay test parameters")
		}
		root, _ := ctx.Value(lfControllerRoot{}).(bool)
		health := root && group == "lf-remote" && timeout <= 1200
		// Verge uses this built-in URL when its writable preference is absent.
		// Translate the request locally and dial only the managed endpoint.
		if !health && (target == "" || target == "http://cp.cloudflare.com/generate_204") {
			target = lfguard.DelayTarget()
		}
		if !lfguard.DelayAllowed(target, health) {
			return lfRefuseDelay(w, r, http.StatusForbidden, "managed delay test endpoint is not authorized")
		}
		if health {
			ctx = lfguard.WithHealthDelay(ctx)
		} else {
			batch := ""
			if group != "" {
				batch = "group/" + group
				deadline = lfguard.ScheduledDelay
			}
			release = lfReserveDelay(batch)
			if release == nil {
				return lfRefuseDelay(w, r, http.StatusTooManyRequests, "managed delay tests are busy")
			}
			ctx, target = lfguard.InteractiveTarget(ctx)
			if timeout > lfguard.MaxInteractiveDelay.Milliseconds() {
				timeout = lfguard.MaxInteractiveDelay.Milliseconds()
			}
		}
	}
	ctx, cancel := deadline(ctx, time.Duration(timeout)*time.Millisecond)
	return &lfDelayCall{
		ctx: ctx, stop: func() { cancel(); release() }, target: target, expected: expected,
	}
}

func lfProviderDelay(w http.ResponseWriter, r *http.Request, name string) *lfDelayCall {
	if r.URL.RawQuery != "" {
		return lfRefuseDelay(w, r, http.StatusBadRequest, "managed provider tests use the configured endpoint")
	}
	release := lfReserveDelay("provider/" + name)
	if release == nil {
		return lfRefuseDelay(w, r, http.StatusTooManyRequests, "managed delay tests are busy")
	}
	ctx, target := lfguard.InteractiveTarget(r.Context())
	ctx, cancel := lfguard.ScheduledDelay(ctx, lfguard.MaxInteractiveDelay)
	return &lfDelayCall{
		ctx: ctx, stop: func() { cancel(); release() }, target: target,
	}
}
