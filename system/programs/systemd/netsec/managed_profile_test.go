package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLFManagedPolicy(t *testing.T) {
	path := filepath.Join(t.TempDir(), "policy.json")
	if err := os.WriteFile(path, []byte(`{"listeners":[],"tunnels":[],"dns.listen":"127.0.0.1:1053","tun.include-uid":[],"tun.stack":"mixed","tun.dns-mode":"hijack"}`), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("LF_MANAGED_PROXY_POLICY", path)
	valid := map[string]any{"dns": map[string]any{"listen": "127.0.0.1:1053"}, "tun": map[string]any{"stack": "Mixed", "dns-mode": "hijack"}}
	if id, err := lfValidate(valid, false); err != nil || len(id) != 64 {
		t.Fatalf("valid: %s %v", id, err)
	}
	for key, value := range map[string]any{"listeners": []any{map[string]any{"type": "http", "port": 18080}}, "tunnels": []any{"tcp/80"}} {
		bad := map[string]any{key: value}
		if _, err := lfValidate(bad, true); err == nil {
			t.Fatalf("accepted extra %s", key)
		}
	}
	if err := ValidateManagedPatch(map[string]any{"dns": map[string]any{"listen": "0.0.0.0:53"}}); err == nil {
		t.Fatal("accepted DNS drift")
	}
	if err := ValidateManagedPatch(map[string]any{"tun": map[string]any{"include-uid": []any{float64(1234)}}}); err == nil {
		t.Fatal("accepted route drift")
	}
	// sing-tun falls back to its own default when dns-mode is unset, so only an
	// explicit pin keeps the :53 hijack from being switched off over the API.
	for _, mode := range []string{"native", "disabled", ""} {
		if err := ValidateManagedPatch(map[string]any{"tun": map[string]any{"dns-mode": mode}}); err == nil {
			t.Fatalf("accepted tun dns-mode drift: %q", mode)
		}
	}
	if err := ValidateManagedPatch(map[string]any{"tun": map[string]any{"dns-mode": "hijack"}}); err != nil {
		t.Fatalf("rejected the managed dns-mode: %v", err)
	}
	if err := ValidateManagedPatch(map[string]any{"mode": "global"}); err != nil {
		t.Fatal(err)
	}
}

func TestLFManagedNixConfig(t *testing.T) {
	for _, name := range []string{"LF_MANAGED_TEST_POLICY", "LF_MANAGED_TEST_HTTP_POLICY"} {
		t.Run(name, func(t *testing.T) {
			path := os.Getenv(name)
			if path == "" {
				t.Skip("Nix integration policy not supplied")
			}
			testLFManagedNixConfig(t, path)
		})
	}
}

func testLFManagedNixConfig(t *testing.T, path string) {
	t.Helper()
	t.Setenv("LF_MANAGED_PROXY_POLICY", path)
	policy, _, err := lfManagedPolicy()
	if err != nil {
		t.Fatal(err)
	}
	document := map[string]any{}
	for key, value := range policy {
		parts := strings.Split(key, ".")
		object := document
		for _, part := range parts[:len(parts)-1] {
			child, ok := object[part].(map[string]any)
			if !ok {
				child = map[string]any{}
				object[part] = child
			}
			object = child
		}
		object[parts[len(parts)-1]] = value
	}
	data, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := UnmarshalRawConfig(data)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = lfValidateRaw(raw); err != nil {
		t.Fatal(err)
	}
	for key, value := range map[string]any{
		"direct-nameserver":               []any{"udp://192.0.2.53:5353"},
		"direct-nameserver-follow-policy": true,
		"proxy-server-nameserver-policy":  map[string]any{"+.example.com": []any{"udp://192.0.2.53:5353"}},
		"fake-ip-filter-mode":             "whitelist",
		"fake-ip-range6":                  "fd00::/64",
	} {
		var changed map[string]any
		if err = json.Unmarshal(data, &changed); err != nil {
			t.Fatal(err)
		}
		changed["dns"].(map[string]any)[key] = value
		encoded, err := json.Marshal(changed)
		if err != nil {
			t.Fatal(err)
		}
		candidate, err := UnmarshalRawConfig(encoded)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = lfValidateRaw(candidate); err == nil {
			t.Fatalf("final RawConfig accepted DNS drift: %s", key)
		}
	}
	// Only an enabled TUN pins dns-mode. The HTTP-only policy intentionally
	// omits active TUN settings, so changing an unused mode is not policy drift.
	if _, pinned := policy["tun.dns-mode"]; pinned {
		for _, mode := range []string{"native", "disabled", ""} {
			var changed map[string]any
			if err = json.Unmarshal(data, &changed); err != nil {
				t.Fatal(err)
			}
			changed["tun"].(map[string]any)["dns-mode"] = mode
			encoded, err := json.Marshal(changed)
			if err != nil {
				t.Fatal(err)
			}
			candidate, err := UnmarshalRawConfig(encoded)
			if err != nil {
				t.Fatal(err)
			}
			if _, err = lfValidateRaw(candidate); err == nil {
				t.Fatalf("final RawConfig accepted tun dns-mode drift: %q", mode)
			}
		}
	}
	raw.Listeners = []map[string]any{{"type": "http", "port": 18080}}
	if _, err = lfValidateRaw(raw); err == nil {
		t.Fatal("final RawConfig accepted extra listener")
	}
}
