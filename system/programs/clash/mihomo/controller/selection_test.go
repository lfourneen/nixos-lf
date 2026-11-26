package route

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/metacubex/http"
	"github.com/metacubex/mihomo/adapter"
	"github.com/metacubex/mihomo/adapter/outboundgroup"
	C "github.com/metacubex/mihomo/constant"
	P "github.com/metacubex/mihomo/constant/provider"
)

type lfSelectionProvider struct{ lfDelayFixtureProvider }

func (lfSelectionProvider) Version() uint32 { return 1 }

func TestLFManagedSelectorTrialRespectsOperatorSelectingTheSameLeaf(t *testing.T) {
	provider := lfSelectionProvider{lfDelayFixtureProvider{proxies: []C.Proxy{
		lfDelayFixtureProxy{name: "a"}, lfDelayFixtureProxy{name: "b"},
	}}}
	selector, err := outboundgroup.NewSelector(outboundgroup.GroupCommonOption{Name: "lf-remote"},
		outboundgroup.SelectorOption{DefaultSelected: "a"}, provider.proxies[0], []P.ProxyProvider{provider})
	if err != nil {
		t.Fatal(err)
	}
	proxy := adapter.NewProxy(selector)
	t.Cleanup(func() {
		selections.Lock()
		defer selections.Unlock()
		if state := selections.slots[proxy.Name()]; state != nil {
			state.advance()
			delete(selections.slots, proxy.Name())
		}
	})
	call := func(method, action, token string, root bool, status int) string {
		t.Helper()
		body, _ := json.Marshal(map[string]string{"action": action, "token": token, "name": "b"})
		req, err := http.NewRequest(method, "/proxies/lf-remote/selection", strings.NewReader(string(body)))
		if err != nil {
			t.Fatal(err)
		}
		ctx := context.WithValue(req.Context(), CtxKeyProxy, C.Proxy(proxy))
		ctx = context.WithValue(ctx, lfControllerRoot{}, root)
		response := &lfPolicyRecorder{}
		lfSelection(response, req.WithContext(ctx))
		if response.code != status {
			t.Fatalf("%s %s: got status %d, expected %d: %s", method, action, response.code, status, response.body.String())
		}
		var result struct{ Token string }
		if response.code == 0 && json.Unmarshal(response.body.Bytes(), &result) != nil {
			t.Fatal(response.body.String())
		}
		return result.Token
	}
	call(http.MethodGet, "", "", false, http.StatusForbidden)
	initial := call(http.MethodGet, "", "", true, 0)
	first := call(http.MethodPut, "trial", initial, true, 0)
	next := call(http.MethodPut, "rollback", first, true, 0)
	if selector.Now() != "a" {
		t.Fatal("failed trial did not restore original selection")
	}
	second := call(http.MethodPut, "trial", next, true, 0)
	unlock := lfLockSelection(proxy)
	err = selector.Set("b") // The user explicitly selects the provisional value.
	lfChangedSelection(proxy)
	unlock()
	if err != nil {
		t.Fatal(err)
	}
	call(http.MethodPut, "rollback", second, true, http.StatusConflict)
	call(http.MethodPut, "commit", second, true, http.StatusConflict)
	if selector.Now() != "b" {
		t.Fatal("the stale trial overrode the operator choice")
	}
}
