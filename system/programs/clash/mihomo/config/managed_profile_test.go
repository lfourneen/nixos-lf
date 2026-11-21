package config

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/metacubex/mihomo/common/yaml"
	"github.com/metacubex/mihomo/component/lfguard"
)

func TestLFManagedRemovesLegacyMagicDNS(t *testing.T) {
	policy := map[string]any{
		"lf-private-targets":    map[string]any{"magic-dns-resolver": "100.100.100.100"},
		"dns.fake-ip-filter":    []any{"*.home.arpa"},
		"dns.nameserver-policy": map[string]any{},
	}
	const previous = `{"fake-ip-filter":["*.home.arpa","+.ts.net"],
		"nameserver-policy":{"+.ts.net":["udp://100.100.100.100:53#DIRECT"]}}`
	for _, test := range []struct {
		profile string
		removed bool
	}{
		{previous, true},
		{strings.Replace(previous, "100.100.100.100", "192.0.2.53", 1), false},
	} {
		var raw RawConfig
		if err := json.Unmarshal([]byte(test.profile), &raw.DNS); err != nil {
			t.Fatal(err)
		}
		if err := lfUpgradeMagicDNS(&raw, policy); err != nil {
			t.Fatal(err)
		}
		fields, err := json.Marshal(raw.DNS)
		if err != nil || strings.Contains(string(fields), "+.ts.net") == test.removed ||
			!reflect.DeepEqual(raw.DNS.FakeIPFilter, []string{"*.home.arpa"}) {
			t.Fatal("unexpected legacy MagicDNS normalization", string(fields), err)
		}
	}
}

func TestLFManagedEmptyPolicyFailsClosed(t *testing.T) {
	for _, policy := range []string{"null", "{}"} {
		t.Run(policy, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "policy.json")
			if err := os.WriteFile(path, []byte(policy), 0600); err != nil {
				t.Fatal(err)
			}
			t.Setenv("LF_MANAGED_PROXY_POLICY", path)
			raw, err := UnmarshalRawConfig([]byte("mode: global\ntun:\n  enable: true\n"))
			if err != nil {
				t.Fatal(err)
			}
			if _, err = lfValidateRaw(raw); err == nil {
				t.Fatal("empty managed policy was treated as an unmanaged configuration")
			}
		})
	}
}

func TestLFManagedDelayProfileMigratesCachedGroupsAndProviders(t *testing.T) {
	path := filepath.Join(t.TempDir(), "policy.json")
	target := "https://approved.fixture/one?policy=root"
	value := map[string]any{"lf-private-targets": map[string]any{
		"prefixes": []string{"10.0.0.0/8"}, "delay-test-url": target,
	}}
	contents, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, contents, 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("LF_MANAGED_PROXY_POLICY", path)
	t.Cleanup(func() { _ = lfguard.Configure(nil) })
	raw, err := UnmarshalRawConfig([]byte(`
proxy-groups:
  - {name: Group, type: fallback, proxies: [Node], URL: 'http://other.fixture', interval: 1, timeout: 60000, expected-status: '503'}
  - {name: Choice, type: select, proxies: [Node], interval: 1500}
proxy-providers:
  other:
    type: http
    url: 'https://other.fixture/source'
    health-check: {enable: true, url: 'https://other.fixture/probe', interval: 2, timeout: 55555}
`))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := lfValidateRaw(raw); err != nil {
		t.Fatal(err)
	}
	for i, fields := range []map[string]any{raw.ProxyGroup[0], raw.ProxyGroup[1],
		raw.ProxyProvider["other"]["health-check"].(map[string]any)} {
		interval := 300
		if i == 1 {
			interval = 0
		}
		if fields["url"] != target || fields["interval"] != interval ||
			fields["timeout"] != 5000 || fields["expected-status"] != "" {
			t.Fatalf("delay policy not applied to configuration %d: %v", i, fields)
		}
	}
	if raw.ProxyProvider["other"]["url"] != "https://other.fixture/source" ||
		raw.ProxyProvider["other"]["health-check"].(map[string]any)["enable"] != true {
		t.Fatal("delay policy changed provider import settings")
	}
}

func TestLFManagedSystemControlsShadowGUIConfig(t *testing.T) {
	path := filepath.Join(t.TempDir(), "policy.json")
	policy := `{"tcp-concurrent":true,"interface-name":"lf-host","tun.enable":false,"tun.stack":"mixed","tun.dns-mode":"hijack"}`
	if err := os.WriteFile(path, []byte(policy), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("LF_MANAGED_PROXY_POLICY", path)
	raw, err := UnmarshalRawConfig([]byte("mode: global\ntcp-concurrent: false\ninterface-name: Mihomo\ntun:\n  enable: true\n  stack: gvisor\n  dns-mode: normal\n  gso: true\n  udp-timeout: 17\n  endpoint-independent-nat: true\n"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := lfValidateRaw(raw); err != nil {
		t.Fatalf("GUI preference blocked a managed start: %v", err)
	}
	if raw.Mode.String() != "rule" {
		t.Fatalf("GUI mode changed the core mode: %s", raw.Mode)
	}
	if !raw.TCPConcurrent || raw.Interface != "lf-host" {
		t.Fatal("GUI preferences changed managed dialing settings")
	}
	for key, value := range map[string]any{"tcp-concurrent": false, "interface-name": "Mihomo"} {
		if err := ValidateManagedPatch(map[string]any{key: value}); err == nil {
			t.Fatalf("controller accepted a change to %s", key)
		}
	}
	if raw.Tun.Enable {
		t.Fatal("GUI preference enabled the managed TUN")
	}
	if raw.Tun.Stack.String() != "Mixed" || raw.Tun.DNSMode != "hijack" {
		t.Fatalf("GUI preference changed managed TUN settings: %#v", raw.Tun)
	}
	defaults, err := UnmarshalRawConfig(nil)
	if err != nil {
		t.Fatal(err)
	}
	if raw.Tun.GSO != defaults.Tun.GSO || raw.Tun.UDPTimeout != defaults.Tun.UDPTimeout ||
		raw.Tun.EndpointIndependentNat != defaults.Tun.EndpointIndependentNat {
		t.Fatal("unmanaged TUN fields survived GUI shadowing")
	}
}

func TestLFManagedDNSTransportCannotAdoptModeAUpstreams(t *testing.T) {
	path := filepath.Join(t.TempDir(), "policy.json")
	expected := map[string]any{
		"dns":              map[string]any{"nameserver": []string{"https://1.1.1.1/dns-query#lf-remote"}},
		"bootstrap-listen": "127.0.0.1:1058",
	}
	data, _ := json.Marshal(map[string]any{"lf-dns-transport": expected})
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("LF_MANAGED_PROXY_POLICY", path)
	raw := DefaultRawConfig()
	raw.LFDNSTransport = map[string]any{"dns": map[string]any{
		"nameserver": []string{"tls://101.226.4.6"},
	}}
	if _, err := lfValidateRaw(raw); err != nil {
		t.Fatal(err)
	}
	actual, _ := json.Marshal(raw.LFDNSTransport)
	want, _ := json.Marshal(expected)
	if string(actual) != string(want) {
		t.Fatal("cached profile replaced B upstreams", string(actual))
	}
	if err := ValidateManagedPatch(map[string]any{"lf-dns-transport": map[string]any{
		"dns": map[string]any{"nameserver": []string{"tls://101.226.4.6"}},
	}}); err == nil {
		t.Fatal("controller replaced B upstreams")
	}
}

func TestLFManagedStrictSniffer(t *testing.T) {
	path := filepath.Join(t.TempDir(), "policy.json")
	if err := os.WriteFile(path, []byte(`{"sniffer.enable":false}`), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("LF_MANAGED_PROXY_POLICY", path)
	raw := DefaultRawConfig()
	raw.Sniffer.Enable = true
	if _, err := lfValidateRaw(raw); err != nil {
		t.Fatal(err)
	}
	if raw.Sniffer.Enable {
		t.Fatal("cached profile enabled strict-mode sniffing")
	}
	if err := ValidateManagedPatch(map[string]any{"sniffer": map[string]any{"enable": true}}); err == nil {
		t.Fatal("PATCH enabled strict-mode sniffing")
	}
}

func TestLFManagedFakeIPPersistenceSurvivesRoutingRestarts(t *testing.T) {
	path := filepath.Join(t.TempDir(), "policy.json")
	if err := os.WriteFile(path, []byte(`{"profile.store-fake-ip":true}`), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("LF_MANAGED_PROXY_POLICY", path)
	raw := DefaultRawConfig()
	raw.Profile.StoreFakeIP = false
	if _, err := lfValidateRaw(raw); err != nil {
		t.Fatal(err)
	}
	if !raw.Profile.StoreFakeIP {
		t.Fatal("routing restart could lose live application fake-IP mappings")
	}
}

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

func TestLFManagedTunRouting(t *testing.T) {
	path := os.Getenv("LF_MANAGED_TEST_POLICY")
	if path == "" {
		t.Skip("Nix integration policy not supplied")
	}
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
	// Upgrade a pre-pin generated profile without waiting for the GUI to run.
	keys := []string{"iproute2-rule-index", "auto-redirect-input-mark",
		"auto-redirect-output-mark", "auto-redirect-iproute2-fallback-rule-index"}
	for _, zero := range []bool{false, true} {
		for _, key := range keys {
			if zero {
				document["tun"].(map[string]any)[key] = 0
			} else {
				delete(document["tun"].(map[string]any), key)
			}
		}
		for _, marshal := range []func(any) ([]byte, error){json.Marshal, yaml.Marshal} {
			data, err := marshal(document)
			if err != nil {
				t.Fatal(err)
			}
			raw, err := UnmarshalRawConfig(data)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := lfValidateRaw(raw); err != nil {
				t.Fatalf("legacy generated profile, zero=%v: %v", zero, err)
			}
		}
	}
	for _, key := range keys {
		document["tun"].(map[string]any)[key] = policy["tun."+key]
	}
	for key, value := range map[string]any{
		"iproute2-rule-index":                        40000,
		"auto-redirect-input-mark":                   6666,
		"auto-redirect-output-mark":                  6666,
		"auto-redirect-iproute2-fallback-rule-index": 52,
		"file-descriptor":                            123,
	} {
		if _, pinned := policy["tun."+key]; !pinned {
			continue
		}
		original := document["tun"].(map[string]any)[key]
		document["tun"].(map[string]any)[key] = value
		for _, marshal := range []func(any) ([]byte, error){json.Marshal, yaml.Marshal} {
			data, err := marshal(document)
			if err != nil {
				t.Fatal(err)
			}
			raw, err := UnmarshalRawConfig(data)
			if err != nil {
				t.Fatal(err)
			}
			if _, err = lfValidateRaw(raw); err != nil {
				t.Fatalf("GUI tun.%s preference blocked a managed start: %v", key, err)
			}
			applied, err := json.Marshal(raw)
			if err != nil {
				t.Fatal(err)
			}
			var result map[string]any
			if err = json.Unmarshal(applied, &result); err != nil {
				t.Fatal(err)
			}
			actual, _ := json.Marshal(result["tun"].(map[string]any)[key])
			expected, _ := json.Marshal(policy["tun."+key])
			if string(actual) != string(expected) {
				t.Fatalf("tun.%s preference survived the load: %s, want %s", key, actual, expected)
			}
		}
		document["tun"].(map[string]any)[key] = original
	}
}

// Every new upstream TUN input needs an explicit policy decision at upgrades.
func TestLFManagedTunSchemaCoverage(t *testing.T) {
	path := os.Getenv("LF_MANAGED_TEST_POLICY")
	if path == "" {
		t.Skip("Nix integration policy not supplied")
	}
	t.Setenv("LF_MANAGED_PROXY_POLICY", path)
	policy, _, err := lfManagedPolicy()
	if err != nil {
		t.Fatal(err)
	}
	if policy["tun.enable"] != true {
		t.Skip("TUN disabled")
	}
	allowed := map[string]bool{}
	for _, key := range []string{
		"auto-detect-interface", "gso", "gso-max-size", "endpoint-independent-nat",
		"udp-timeout", "icmp-timeout", "disable-icmp-forwarding", "congestion-controller",
		"include-android-user", "include-package", "exclude-package",
		"recvmsgx", "sendmsgx", "processors-per-channel",
	} {
		allowed[key] = true
	}
	schema := reflect.TypeOf(RawTun{})
	for index := 0; index < schema.NumField(); index++ {
		key := strings.Split(schema.Field(index).Tag.Get("json"), ",")[0]
		if _, managed := policy["tun."+key]; !managed && !allowed[key] {
			t.Errorf("unclassified upstream TUN field: %s", key)
		}
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
	if _, pinned := policy["lf-private-targets"]; pinned {
		for _, value := range []any{map[string]any{}, map[string]any{"prefixes": []any{}, "allowed-domains": []any{"example.com"}}} {
			var changed map[string]any
			if err := json.Unmarshal(data, &changed); err != nil {
				t.Fatal(err)
			}
			changed["lf-private-targets"] = value
			encoded, err := json.Marshal(changed)
			if err != nil {
				t.Fatal(err)
			}
			candidate, err := UnmarshalRawConfig(encoded)
			if err == nil {
				// The policy owns this field, so the load succeeds but the
				// weakened profile value must not survive it.
				_, err = lfValidateRaw(candidate)
			}
			if err != nil {
				t.Fatal("weakened profile must load with the policy value", err)
			}
			if !reflect.DeepEqual(candidate.LFPrivateTargets, policy["lf-private-targets"]) {
				t.Fatal("weakened private target policy survived the load", candidate.LFPrivateTargets)
			}
			if err := ValidateManagedPatch(map[string]any{"lf-private-targets": value}); err == nil {
				t.Fatal("accepted private target PATCH drift")
			}
		}
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
			if _, err = lfValidateRaw(candidate); err != nil {
				t.Fatalf("GUI tun dns-mode preference blocked a managed start: %v", err)
			}
			if candidate.Tun.DNSMode != policy["tun.dns-mode"].(string) {
				t.Fatalf("tun dns-mode preference survived: %q", candidate.Tun.DNSMode)
			}
		}
	}
	raw.Listeners = []map[string]any{{"type": "http", "port": 18080}}
	if _, err = lfValidateRaw(raw); err == nil {
		t.Fatal("final RawConfig accepted extra listener")
	}
}

// Captured managed fields from the generation before MagicDNS/target guarding.
// No subscriptions or credentials are needed to reproduce the upgrade failure.
func TestLFManagedPreviousGeneration(t *testing.T) {
	path := os.Getenv("LF_MANAGED_TEST_POLICY")
	fixture := os.Getenv("LF_MANAGED_TEST_LEGACY_PROFILE")
	if path == "" || fixture == "" {
		t.Skip("Nix upgrade fixture not supplied")
	}
	t.Setenv("LF_MANAGED_PROXY_POLICY", path)
	policy, _, err := lfManagedPolicy()
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(fixture)
	if err != nil {
		t.Fatal(err)
	}
	for _, marshal := range []func(any) ([]byte, error){json.Marshal, yaml.Marshal} {
		var document map[string]any
		if err := json.Unmarshal(data, &document); err != nil {
			t.Fatal(err)
		}
		encoded, err := marshal(document)
		if err != nil {
			t.Fatal(err)
		}
		raw, err := UnmarshalRawConfig(encoded)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := lfValidateRaw(raw); err != nil {
			t.Fatalf("previous generation cannot start: %v", err)
		}
		// Full-config callers that construct RawConfig directly must also receive
		// defaults, rather than depending on one specific YAML entry point.
		direct := DefaultRawConfig()
		if err := json.Unmarshal(data, direct); err != nil {
			t.Fatal(err)
		}
		if _, err := lfValidateRaw(direct); err != nil {
			t.Fatalf("direct RawConfig: %v", err)
		}
	}
	for _, field := range []string{"fake-ip-filter", "nameserver-policy"} {
		var document map[string]any
		if err := json.Unmarshal(data, &document); err != nil {
			t.Fatal(err)
		}
		dns := document["dns"].(map[string]any)
		expected := policy["dns."+field]
		err := ValidateManagedPatch(map[string]any{"dns": map[string]any{field: dns[field]}})
		if (err == nil) != reflect.DeepEqual(lfCanonical(expected), lfCanonical(dns[field])) {
			t.Fatalf("runtime PATCH accepted a non-current value or rejected the current %s: %v", field, err)
		}
		if field == "fake-ip-filter" {
			dns[field] = append(dns[field].([]any), "+.attacker.example")
		} else {
			dns[field].(map[string]any)["+.attacker.example"] = []any{"udp://192.0.2.53"}
		}
		encoded, err := json.Marshal(document)
		if err != nil {
			t.Fatal(err)
		}
		raw, err := UnmarshalRawConfig(encoded)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := lfValidateRaw(raw); err == nil {
			t.Fatalf("upgrade concealed arbitrary %s drift", field)
		}
	}
}

// The exclusion list is derived from machine.privateV4, so an older profile may
// omit an entry a newer generation added, or carry one the policy no longer
// lists. The policy owns the list: a stale profile still starts, and an entry
// the policy does not list is dropped instead of honoured, since an extra
// exclusion would move traffic out of the tunnel.
func TestLFManagedPreviousGenerationAppliesPolicyExclusions(t *testing.T) {
	path := os.Getenv("LF_MANAGED_TEST_POLICY")
	fixture := os.Getenv("LF_MANAGED_TEST_LEGACY_PROFILE")
	if path == "" || fixture == "" {
		t.Skip("Nix upgrade fixture not supplied")
	}
	t.Setenv("LF_MANAGED_PROXY_POLICY", path)
	data, err := os.ReadFile(fixture)
	if err != nil {
		t.Fatal(err)
	}
	var document map[string]any
	if err := json.Unmarshal(data, &document); err != nil {
		t.Fatal(err)
	}
	tun, _ := document["tun"].(map[string]any)
	if tun == nil {
		t.Skip("fixture has no tun section")
	}
	list, _ := tun["route-exclude-address"].([]any)
	if list == nil {
		t.Skip("fixture has no tunnel exclusion list")
	}
	tun["route-exclude-address"] = append(list, "203.0.113.0/24")
	encoded, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := UnmarshalRawConfig(encoded)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := lfValidateRaw(raw); err != nil {
		t.Fatalf("stale profile with an extra exclusion failed to start: %v", err)
	}
	applied, err := json.Marshal(raw)
	if err != nil {
		t.Fatal(err)
	}
	var result map[string]any
	if err := json.Unmarshal(applied, &result); err != nil {
		t.Fatal(err)
	}
	resultTun, _ := result["tun"].(map[string]any)
	if resultTun == nil {
		t.Fatal("policy application dropped the tun section")
	}
	final, _ := resultTun["route-exclude-address"].([]any)
	if len(final) == 0 {
		t.Fatal("policy application dropped the exclusion list")
	}
	for _, item := range final {
		if item == "203.0.113.0/24" {
			t.Fatal("kept an exclusion the policy does not list")
		}
	}
}

func TestLFManagedSubscriptionTrust(t *testing.T) {
	document := map[string]any{"proxy-providers": map[string]any{"source": map[string]any{"url": "https://example.invalid/private?token=secret"}}}
	if lfValidateSubscription(document, map[string]any{"subscription-trust": "fixed"}) == nil {
		t.Fatal("fixed trust accepted mutable source")
	}
	targets := map[string]any{"subscription-trust": "source", "remote-target-grants": []any{map[string]any{"proxy": "same-name"}}}
	if lfValidateSubscription(document, targets) == nil {
		t.Fatal("mutable identity inherited remote grant")
	}
	targets["allow-dynamic-remote-grants"] = true
	if err := lfValidateSubscription(document, targets); err != nil {
		t.Fatal(err)
	}
}

// A stale profile that only needs the tunnel exclusion upgrade must still start
// when it carries typed listener ports. Re-encoding the whole RawConfig through
// JSON turned "port: 7898" into a float64, which the inbound structure decoder
// then rendered as "7.898E+03" and rejected as an invalid range.
func TestLFManagedExclusionUpgradeKeepsListenerPorts(t *testing.T) {
	path := filepath.Join(t.TempDir(), "policy.json")
	policy := `{"listeners":[{"listen":"127.0.0.1","name":"lf-strict","port":7898,"type":"mixed","udp":false}],` +
		`"tun.route-exclude-address":["10.0.0.0/8"]}`
	if err := os.WriteFile(path, []byte(policy), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("LF_MANAGED_PROXY_POLICY", path)
	profile := strings.Join([]string{
		"listeners:",
		"- listen: 127.0.0.1",
		"  name: lf-strict",
		"  port: 7898",
		"  type: mixed",
		"  udp: false",
		"tun:",
		"  route-exclude-address:",
		"  - 10.1.0.0/16",
		"",
	}, "\n")
	raw, err := UnmarshalRawConfig([]byte(profile))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := lfValidateRaw(raw); err != nil {
		t.Fatalf("stale profile failed to start: %v", err)
	}
	if len(raw.Listeners) != 1 {
		t.Fatalf("policy upgrade changed the listener count: %d", len(raw.Listeners))
	}
	if port := raw.Listeners[0]["port"]; port != int(7898) {
		t.Fatalf("listener port lost its integer type: %T %v", port, port)
	}
	if _, err := parseListeners(raw); err != nil {
		t.Fatalf("policy upgrade broke listener parsing: %v", err)
	}
}

func TestLFManagedInjectedListenerPort(t *testing.T) {
	path := filepath.Join(t.TempDir(), "policy.json")
	policy := `{"listeners":[{"listen":"127.0.0.1","name":"lf-strict","port":7898,"type":"mixed","udp":false}]}`
	if err := os.WriteFile(path, []byte(policy), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("LF_MANAGED_PROXY_POLICY", path)
	raw := DefaultRawConfig()
	if _, err := lfValidateRaw(raw); err != nil {
		t.Fatal(err)
	}
	if len(raw.Listeners) != 1 || raw.Listeners[0]["port"] != float64(7898) {
		t.Fatal("policy did not inject the JSON numeric listener port")
	}
	listeners, err := parseListeners(raw)
	if err != nil {
		t.Fatal(err)
	}
	if got := listeners["lf-strict"].RawAddress(); got != "127.0.0.1:7898" {
		t.Fatalf("injected listener address: %s", got)
	}
	raw.Listeners[0]["port"] = float64(7899)
	if _, err := lfValidateRaw(raw); err == nil {
		t.Fatal("accepted listener port outside the managed policy")
	}
}

func TestLFManagedNumericListenerPortBounds(t *testing.T) {
	for _, test := range []struct {
		name string
		port any
		want string
	}{
		{"integer", int(7898), "127.0.0.1:7898"},
		{"float64", float64(7898), "127.0.0.1:7898"},
		{"float32", float32(7898), "127.0.0.1:7898"},
		{"maximum", float64(65535), "127.0.0.1:65535"},
		{"fraction", float64(7898.5), ""},
		{"negative", float64(-1), ""},
		{"overflow-float", float64(65536), ""},
		{"overflow-integer", int(65536), ""},
		{"overflow-range", "7898-65536", ""},
		{"huge", float64(1e300), ""},
		{"nan", math.NaN(), ""},
		{"infinity", math.Inf(1), ""},
	} {
		t.Run(test.name, func(t *testing.T) {
			raw := DefaultRawConfig()
			raw.Listeners = []map[string]any{{
				"name": "port-test", "type": "mixed", "listen": "127.0.0.1",
				"port": test.port, "udp": false,
			}}
			listeners, err := parseListeners(raw)
			if test.want == "" {
				if err == nil {
					t.Fatalf("accepted invalid listener port: %v", test.port)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if got := listeners["port-test"].RawAddress(); got != test.want {
				t.Fatalf("listener address: got %s, want %s", got, test.want)
			}
		})
	}
}
