package route

import (
	"context"
	"net"
	"net/url"
	"strconv"
	"time"

	"github.com/metacubex/chi/render"
	"github.com/metacubex/http"
	"github.com/metacubex/mihomo/common/utils"
	"github.com/metacubex/mihomo/component/lfguard"
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
	lfDelayRequests = make(chan struct{}, 16)
	lfGroupDelay    = make(chan struct{}, 1)
)

type lfDelayCall struct {
	ctx      context.Context
	stop     func()
	target   string
	expected utils.IntRanges[uint16]
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
	if group != "" {
		limit = 32
	}
	timeout, timeoutErr := strconv.ParseInt(query.Get("timeout"), 10, limit)
	expected, expectedErr := utils.NewUnsignedRanges[uint16](query.Get("expected"))
	if queryErr != nil || timeoutErr != nil || expectedErr != nil {
		return lfRefuseDelay(w, r, http.StatusBadRequest, "invalid delay test parameters")
	}
	ctx := r.Context()
	target := query.Get("url")
	release := func() {}
	if managed {
		if timeout <= 0 || timeout > 10000 || len(query["url"]) != 1 || len(query["timeout"]) != 1 ||
			len(query["expected"]) > 1 || query.Get("expected") != "" {
			return lfRefuseDelay(w, r, http.StatusBadRequest, "invalid managed delay test parameters")
		}
		root, _ := ctx.Value(lfControllerRoot{}).(bool)
		health := root && group == "lf-remote" && timeout <= 1200
		if !lfguard.DelayAllowed(target, health) {
			return lfRefuseDelay(w, r, http.StatusForbidden, "managed delay test endpoint is not authorized")
		}
		if health {
			ctx = lfguard.WithHealthDelay(ctx)
		} else {
			slots := lfDelayRequests
			if group != "" {
				slots = lfGroupDelay
			}
			select {
			case slots <- struct{}{}:
				release = func() { <-slots }
			default:
				return lfRefuseDelay(w, r, http.StatusTooManyRequests, "managed delay tests are busy")
			}
		}
	}
	ctx, cancel := context.WithTimeout(ctx, time.Duration(timeout)*time.Millisecond)
	return &lfDelayCall{
		ctx: ctx, stop: func() { cancel(); release() }, target: target, expected: expected,
	}
}
