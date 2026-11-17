package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLFManagedRuleInvariants(t *testing.T) {
	policy := filepath.Join(t.TempDir(), "policy.json")
	if err := os.WriteFile(policy, []byte("{}"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("LF_MANAGED_PROXY_POLICY", policy)

	for _, tc := range []struct {
		name  string
		rules []string
		want  string
	}{
		{"match direct", []string{"MATCH,DIRECT"}, "unconditional DIRECT"},
		{"match direct spaced", []string{"MATCH, DIRECT"}, "unconditional DIRECT"},
		{"final direct", []string{"FINAL,DIRECT"}, "unconditional DIRECT"},
		{"match proxy", []string{"MATCH,CatchAll"}, ""},
		{"conditional direct", []string{"GEOIP,CN,DIRECT", "MATCH,Proxy"}, ""},
		{"conditional plus match direct", []string{"DOMAIN-SUFFIX,example.com,DIRECT", "MATCH,DIRECT"}, "unconditional DIRECT"},
		{"empty", nil, ""},
		{"short", []string{"MATCH"}, ""},
	} {
		err := lfValidateRuleInvariants(&RawConfig{Rule: tc.rules})
		if tc.want == "" {
			if err != nil {
				t.Fatalf("%s: unexpected error: %v", tc.name, err)
			}
			continue
		}
		if err == nil || !strings.Contains(err.Error(), tc.want) {
			t.Fatalf("%s: got %v, want error containing %q", tc.name, err, tc.want)
		}
	}
}

func TestLFUnmanagedRuleInvariants(t *testing.T) {
	t.Setenv("LF_MANAGED_PROXY_POLICY", "")
	if err := lfValidateRuleInvariants(&RawConfig{Rule: []string{"MATCH,DIRECT"}}); err != nil {
		t.Fatalf("unmanaged build must not enforce the managed rule gate: %v", err)
	}
}

func TestLFManagedSingleEgressRejectsAlternateGraphs(t *testing.T) {
	valid := func() *RawConfig {
		return &RawConfig{
			LFPrivateTargets: map[string]any{"proxy-only": true, "single-egress": true},
			Proxy:            []map[string]any{{"name": "node", "type": "socks5", "server": "1.1.1.1", "port": 1080}},
			ProxyGroup:       []map[string]any{{"name": "lf-remote", "type": "select", "proxies": []string{"node"}}},
			Rule:             []string{"IN-NAME,lf-strict,lf-remote", "MATCH,lf-remote"},
		}
	}
	if err := lfValidateSingleEgress(valid()); err != nil {
		t.Fatal(err)
	}
	for _, mutate := range []func(*RawConfig){
		func(r *RawConfig) { r.Proxy = append(r.Proxy, r.Proxy[0]) },
		func(r *RawConfig) { r.Proxy[0]["type"] = "direct" },
		func(r *RawConfig) { r.Proxy[0]["dialer-proxy"] = "other" },
		func(r *RawConfig) { r.Proxy[0]["server"] = "node.example" },
		func(r *RawConfig) { r.ProxyGroup[0]["type"] = "fallback" },
		func(r *RawConfig) { r.ProxyGroup[0]["include-all"] = true },
		func(r *RawConfig) { r.ProxyGroup[0]["proxies"] = []string{"node", "DIRECT"} },
		func(r *RawConfig) { r.Rule = []string{"MATCH,DIRECT"} },
	} {
		raw := valid()
		mutate(raw)
		if lfValidateSingleEgress(raw) == nil {
			t.Fatal("accepted an alternate egress graph")
		}
	}
}
