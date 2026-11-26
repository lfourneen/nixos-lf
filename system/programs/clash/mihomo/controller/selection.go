package route

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"strconv"
	"sync"
	"time"

	"github.com/metacubex/chi/render"
	"github.com/metacubex/http"
	"github.com/metacubex/mihomo/adapter/outboundgroup"
	"github.com/metacubex/mihomo/component/lfguard"
	C "github.com/metacubex/mihomo/constant"
	"github.com/metacubex/mihomo/tunnel"
)

func lfHealthDelay(w http.ResponseWriter, r *http.Request) {
	root, _ := r.Context().Value(lfControllerRoot{}).(bool)
	proxy := r.Context().Value(CtxKeyProxy).(C.Proxy)
	group := tunnel.Proxies()["lf-remote"]
	if !root || group == nil || !lfguard.ManagedDelay() || lfguard.SingleEgress() || len(r.URL.RawQuery) > 4096 {
		lfRefuseDelay(w, r, http.StatusForbidden, "leaf probe not authorized")
		return
	}
	selector, ok := group.Adapter().(*outboundgroup.Selector)
	target := r.URL.Query().Get("url")
	if ok && lfguard.DelayAllowed(target, true) && target != "" {
		for _, leaf := range selector.Proxies() {
			if leaf != proxy {
				continue
			}
			switch leaf.Type() {
			case C.Shadowsocks, C.Socks5, C.Http, C.Vmess, C.Vless, C.Trojan, C.Hysteria2, C.Tuic, C.AnyTLS:
				ctx, cancel := context.WithTimeout(lfguard.WithHealthDelay(r.Context()), 1200*time.Millisecond)
				defer cancel()
				_, err := leaf.URLTest(ctx, target, nil)
				render.JSON(w, r, render.M{"ok": err == nil})
				return
			}
		}
	}
	lfRefuseDelay(w, r, http.StatusForbidden, "leaf probe not authorized")
}

const selectionTrialTimeout = 7 * time.Second

type selectionState struct {
	proxy    C.Proxy
	revision uint64
	original string
	timer    *time.Timer
}

var selections = struct {
	sync.Mutex
	boot  string
	next  uint64
	slots map[string]*selectionState
}{slots: make(map[string]*selectionState)}

func (s *selectionState) advance() {
	selections.next++
	s.revision = selections.next
	if s.timer != nil {
		s.timer.Stop()
		s.timer = nil
	}
	s.original = ""
}

func controlledSelector(name string) bool { return name == "lf-remote" || name == "lf-dns" }

// This lock also surrounds ordinary GUI writes, even if the GUI picks the
// same leaf that a probe provisionally selected.
func lfLockSelection(proxy C.Proxy) func() {
	if !controlledSelector(proxy.Name()) {
		return func() {}
	}
	selections.Lock()
	return selections.Unlock
}

func lfChangedSelection(proxy C.Proxy) {
	if controlledSelector(proxy.Name()) {
		if state := selections.slots[proxy.Name()]; state != nil {
			state.advance()
		}
	}
}

func lfSelection(w http.ResponseWriter, r *http.Request) {
	root, _ := r.Context().Value(lfControllerRoot{}).(bool)
	proxy := r.Context().Value(CtxKeyProxy).(C.Proxy)
	selector, ok := proxy.Adapter().(*outboundgroup.Selector)
	if !root || !ok || !controlledSelector(proxy.Name()) || lfguard.SingleEgress() {
		lfRefuseDelay(w, r, http.StatusForbidden, "selector operation not authorized")
		return
	}
	var request struct {
		Token  string `json:"token"`
		Action string `json:"action"`
		Name   string `json:"name"`
	}
	if r.Method != http.MethodGet && render.DecodeJSON(r.Body, &request) != nil {
		lfRefuseDelay(w, r, http.StatusBadRequest, "invalid selector operation")
		return
	}
	selections.Lock()
	defer selections.Unlock()
	if selections.boot == "" {
		var nonce [16]byte
		if _, err := rand.Read(nonce[:]); err != nil {
			lfRefuseDelay(w, r, http.StatusServiceUnavailable, "selector generation unavailable")
			return
		}
		selections.boot = hex.EncodeToString(nonce[:])
	}
	state := selections.slots[proxy.Name()]
	if state == nil || state.proxy != proxy {
		state = &selectionState{proxy: proxy}
		state.advance()
		selections.slots[proxy.Name()] = state
	}
	token := func() string { return selections.boot + ":" + strconv.FormatUint(state.revision, 10) }
	if r.Method == http.MethodGet {
		render.JSON(w, r, render.M{"now": selector.Now(), "token": token()})
		return
	}
	if request.Token != token() {
		lfRefuseDelay(w, r, http.StatusConflict, "selector changed")
		return
	}
	switch request.Action {
	case "trial":
		if state.original != "" {
			lfRefuseDelay(w, r, http.StatusConflict, "selector already being tested")
			return
		}
		original := selector.Now()
		if request.Name == original || selector.Set(request.Name) != nil {
			lfRefuseDelay(w, r, http.StatusBadRequest, "invalid selector candidate")
			return
		}
		state.advance()
		state.original = original
		revision := state.revision
		state.timer = time.AfterFunc(selectionTrialTimeout, func() {
			selections.Lock()
			defer selections.Unlock()
			if state.revision == revision {
				if tunnel.Proxies()[proxy.Name()] == proxy {
					_ = selector.Set(original)
				}
				state.advance()
			}
		})
	case "commit", "rollback":
		if state.original == "" {
			lfRefuseDelay(w, r, http.StatusConflict, "selector trial ended")
			return
		}
		if request.Action == "rollback" {
			_ = selector.Set(state.original)
		} else {
			// As with a normal committed selector update, retain the choice.
			lfSaveSelection(proxy, selector.Now())
		}
		state.advance()
	default:
		lfRefuseDelay(w, r, http.StatusBadRequest, "invalid selector operation")
		return
	}
	render.JSON(w, r, render.M{"now": selector.Now(), "token": token()})
}
