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
