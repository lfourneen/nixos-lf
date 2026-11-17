package config

// Applied at the final RawConfig parser, after every GUI/profile extension.
// No subscriptions, credentials or destinations are included in the policy ID.
import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/netip"
	"os"
	"reflect"
	"sort"
	"strings"

	"github.com/metacubex/mihomo/component/lfguard"
)

func lfManagedPolicy() (map[string]any, string, error) {
	path := os.Getenv("LF_MANAGED_PROXY_POLICY")
	if path == "" {
		return nil, "", nil
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, "", err
	}
	// The Nix-side expected digest covers the JSON text itself; tolerate any
	// trailing newline a store writer may add without changing the contract.
	data = bytes.TrimSpace(data)
	var policy map[string]any
	if err = json.Unmarshal(data, &policy); err != nil {
		return nil, "", err
	}
	digest := sha256.Sum256(data)
	return policy, hex.EncodeToString(digest[:]), nil
}

func lfValue(document map[string]any, path string) (any, bool) {
	var value any = document
	for _, key := range strings.Split(path, ".") {
		object, ok := value.(map[string]any)
		if !ok {
			return nil, false
		}
		value, ok = object[key]
		if !ok {
			return nil, false
		}
	}
	return value, true
}

func lfCanonical(value any) any {
	switch v := value.(type) {
	case []any:
		values := make([]string, len(v))
		for i, item := range v {
			data, _ := json.Marshal(lfCanonical(item))
			values[i] = string(data)
		}
		sort.Strings(values)
		return values
	case map[string]any:
		values := map[string]any{}
		for key, item := range v {
			values[key] = lfCanonical(item)
		}
		return values
	}
	return value
}

// lfCanonicalSubset reports whether every element of actual also appears in
// expected, comparing the same canonical form lfCanonical uses elsewhere.
func lfCanonicalSubset(actual, expected []any) bool {
	available := make(map[string]struct{}, len(expected))
	for _, value := range expected {
		data, _ := json.Marshal(lfCanonical(value))
		available[string(data)] = struct{}{}
	}
	for _, value := range actual {
		data, _ := json.Marshal(lfCanonical(value))
		if _, ok := available[string(data)]; !ok {
			return false
		}
	}
	return true
}

func lfValidate(document map[string]any, partial bool) (string, error) {
	policy, id, err := lfManagedPolicy()
	if err != nil || policy == nil {
		return id, err
	}
	for path, expected := range policy {
		actual, exists := lfValue(document, path)
		if path == "tun.stack" {
			if text, ok := actual.(string); ok {
				actual = strings.ToLower(text)
			}
		}
		if !exists && partial {
			continue
		}
		// omitempty/nil represent the same disabled value for optional fields.
		if !exists || actual == nil {
			switch v := expected.(type) {
			case []any:
				if len(v) == 0 {
					continue
				}
			case string:
				if v == "" {
					continue
				}
			case bool:
				if !v {
					continue
				}
			case map[string]any:
				if len(v) == 0 {
					continue
				}
			}
		}
		if !reflect.DeepEqual(lfCanonical(actual), lfCanonical(expected)) {
			return "", fmt.Errorf("managed proxy policy rejects %s", path)
		}
	}
	return id, nil
}

func lfValidateRaw(raw *RawConfig) (string, error) {
	if err := lfManagedProfileDefaults(raw); err != nil {
		return "", err
	}
	if err := lfValidateRuleInvariants(raw); err != nil {
		return "", err
	}
	data, err := json.Marshal(raw)
	if err != nil {
		return "", err
	}
	var document map[string]any
	if err = json.Unmarshal(data, &document); err != nil {
		return "", err
	}
	if err := lfValidateSubscription(document, raw.LFPrivateTargets); err != nil {
		return "", err
	}
	id, err := lfValidate(document, false)
	if err == nil {
		err = lfguard.Configure(raw.LFPrivateTargets)
	}
	return id, err
}

// lfValidateRuleInvariants rejects only the rule that would bypass the proxy
// for every destination. Rule lists are otherwise user-authoritative and are
// authorized by clash-on; this gate makes an accidental or malicious
// "send everything direct" profile fail at startup instead of silently
// disabling the proxy while the TUN still carries every flow.
func lfValidateRuleInvariants(raw *RawConfig) error {
	policy, _, err := lfManagedPolicy()
	if err != nil {
		return err
	}
	if policy == nil {
		// Development and fixture builds run without the managed policy; the
		// invariant belongs to the managed deployment only.
		return nil
	}
	for _, rule := range raw.Rule {
		fields := strings.Split(rule, ",")
		if len(fields) < 2 {
			continue
		}
		kind := strings.ToUpper(strings.TrimSpace(fields[0]))
		if kind != "MATCH" && kind != "FINAL" {
			continue
		}
		if strings.EqualFold(strings.TrimSpace(fields[1]), "DIRECT") {
			return fmt.Errorf("managed proxy policy rejects an unconditional DIRECT rule")
		}
	}
	return nil
}

// Upgrade only known older managed values before the final policy check, on
// every full-config path. Controller PATCH remains strict; no caller-supplied
// value may weaken the resulting policy.
func lfManagedProfileDefaults(raw *RawConfig) error {
	policy, _, err := lfManagedPolicy()
	if err != nil || policy == nil {
		return err
	}
	// This transport is wholly root-owned, including across cached profiles.
	raw.LFDNSTransport, _ = policy["lf-dns-transport"].(map[string]any)
	if value, ok := policy["profile.store-fake-ip"].(bool); ok {
		raw.Profile.StoreFakeIP = value
	}
	if value, ok := policy["geodata-mode"].(bool); ok {
		raw.GeodataMode = value
	}
	// Adopt only the known old empty-listener form. Foreign listeners still
	// fail exact policy validation, and PATCH never performs this upgrade.
	if len(raw.Listeners) == 0 {
		if value, ok := policy["listeners"]; ok {
			encoded, err := json.Marshal(value)
			if err != nil {
				return err
			}
			if err = json.Unmarshal(encoded, &raw.Listeners); err != nil {
				return err
			}
		}
	}
	if value, ok := policy["geo-auto-update"].(bool); ok {
		// The core runs as a service identity; it must not download geodata on
		// its own schedule. PATCH cannot reach this field anyway, but a cached
		// profile or a GUI profile extension may still carry the old value.
		raw.GeoAutoUpdate = value
	}
	if value, ok := policy["lf-private-targets"].(map[string]any); ok {
		// Entirely derived from the root policy: a cached profile may predate
		// this generation or carry an older prefix list, and refusing to start
		// over a field the policy already owns is a lockout, not a defence.
		// PATCH (ValidateManagedPatch) stays strict.
		raw.LFPrivateTargets = value
	}
	if enabled, ok := policy["dns.ipv6"].(bool); ok {
		raw.DNS.IPv6 = enabled
	}
	if enabled, ok := policy["sniffer.enable"].(bool); ok {
		// The strict system policy owns sniffing, including cached profiles.
		raw.Sniffer.Enable = enabled
	}
	if err := lfUpgradeMagicDNS(raw, policy); err != nil {
		return err
	}
	if err := lfUpgradeLocalDNSSEC(raw, policy); err != nil {
		return err
	}
	if len(raw.Tun.ExcludeUID) == 0 {
		if value, ok := policy["tun.exclude-uid"]; ok {
			data, err := json.Marshal(value)
			if err != nil {
				return err
			}
			if err = json.Unmarshal(data, &raw.Tun.ExcludeUID); err != nil {
				return err
			}
		}
	}
	if err := lfApplyTunExcludes(raw, policy); err != nil {
		return err
	}
	defaults := map[string]any{}
	for _, key := range []string{"iproute2-rule-index", "auto-redirect-input-mark",
		"auto-redirect-output-mark", "auto-redirect-iproute2-fallback-rule-index"} {
		if value, exists := policy["tun."+key]; exists {
			defaults[key] = value
		}
	}
	data, err := json.Marshal(defaults)
	if err != nil {
		return err
	}
	var tun RawTun
	if err := json.Unmarshal(data, &tun); err != nil {
		return err
	}
	if raw.Tun.IPRoute2RuleIndex == 0 {
		raw.Tun.IPRoute2RuleIndex = tun.IPRoute2RuleIndex
	}
	if raw.Tun.AutoRedirectInputMark == 0 {
		raw.Tun.AutoRedirectInputMark = tun.AutoRedirectInputMark
	}
	if raw.Tun.AutoRedirectOutputMark == 0 {
		raw.Tun.AutoRedirectOutputMark = tun.AutoRedirectOutputMark
	}
	if raw.Tun.AutoRedirectIPRoute2FallbackRuleIndex == 0 {
		raw.Tun.AutoRedirectIPRoute2FallbackRuleIndex = tun.AutoRedirectIPRoute2FallbackRuleIndex
	}
	return nil
}

// A generated profile can outlive the Nix generation that produced it. The
// local validator replaces only exact, root-described predecessor DNS values.
// Foreign resolver maps still fail validation and PATCH never upgrades them.
func lfUpgradeLocalDNSSEC(raw *RawConfig, policy map[string]any) error {
	targets, _ := policy["lf-private-targets"].(map[string]any)
	if endpoint, _ := targets["local-dnssec"].(string); endpoint == "" {
		return nil
	}
	previous, _ := targets["previous-dns"].(map[string]any)
	data, err := json.Marshal(raw.DNS)
	if err != nil {
		return err
	}
	var actual map[string]any
	if err = json.Unmarshal(data, &actual); err != nil {
		return err
	}
	upgrade := map[string]any{}
	for key, legacy := range previous {
		expected, pinned := policy["dns."+key]
		if !pinned {
			continue
		}
		matches := reflect.DeepEqual(lfCanonical(actual[key]), lfCanonical(legacy))
		if key == "nameserver-policy" {
			withoutMagic := map[string]any{}
			for name, value := range legacy.(map[string]any) {
				if name != "+.ts.net" {
					withoutMagic[name] = value
				}
			}
			matches = matches || reflect.DeepEqual(lfCanonical(actual[key]), lfCanonical(withoutMagic))
		}
		if matches {
			upgrade[key] = expected
		}
	}
	data, err = json.Marshal(upgrade)
	if err != nil {
		return err
	}
	if _, changed := upgrade["nameserver-policy"]; changed {
		raw.DNS.NameServerPolicy = nil
	}
	return json.Unmarshal(data, &raw.DNS)
}

// A generated profile can outlive the Nix generation that produced it. The
// tunnel exclusion list is derived from machine.privateV4, so a newer
// generation may add an entry an older profile cannot know about, and an older
// profile may carry one this policy no longer lists. The policy owns the list,
// so apply it verbatim: a stale profile still starts, a missing entry is added,
// and an extra entry is dropped instead of honoured, since an extra exclusion
// would move traffic out of the tunnel. The final check then sees exactly the
// policy list rather than refusing to start.
func lfApplyTunExcludes(raw *RawConfig, policy map[string]any) error {
	expected, ok := policy["tun.route-exclude-address"].([]any)
	if !ok || len(expected) == 0 {
		return nil
	}
	want := make([]any, 0, len(expected))
	for _, item := range expected {
		if _, ok := item.(string); !ok {
			return nil
		}
		want = append(want, item)
	}
	data, err := json.Marshal(raw.Tun.RouteExcludeAddress)
	if err != nil {
		return err
	}
	var actual []any
	if err = json.Unmarshal(data, &actual); err != nil {
		return err
	}
	if reflect.DeepEqual(lfCanonical(actual), lfCanonical(want)) {
		return nil
	}
	// Replace only the owned field. Re-encoding the whole RawConfig through
	// JSON used to turn every number in the untyped proxy and listener maps
	// into a float64, and the inbound structure decoder then rendered a
	// listener port as "7.898E+03", rejecting the entire profile.
	prefixes := make([]netip.Prefix, 0, len(want))
	for _, item := range want {
		prefix, err := netip.ParsePrefix(item.(string))
		if err != nil {
			return nil
		}
		prefixes = append(prefixes, prefix)
	}
	raw.Tun.RouteExcludeAddress = prefixes
	return nil
}

// A generated profile can outlive the Nix generation that produced it. Match
// each complete pre-MagicDNS value exactly, then add only the currently managed
// entry. Arbitrary DNS drift is still rejected by the final policy check.
func lfUpgradeMagicDNS(raw *RawConfig, policy map[string]any) error {
	data, err := json.Marshal(raw.DNS)
	if err != nil {
		return err
	}
	var actual map[string]any
	if err := json.Unmarshal(data, &actual); err != nil {
		return err
	}
	upgrade := map[string]any{}
	// A generated profile can outlive the generation that produced it: its
	// filter list may only lack entries that later generations added. Adopt the
	// managed list when the actual one is a subset of it; a list carrying
	// anything the policy does not describe is foreign drift and is still
	// rejected by the final check. Runtime PATCH never reaches this path, so a
	// PATCH with an old value stays rejected.
	if expected, ok := policy["dns.fake-ip-filter"].([]any); ok {
		actualList, _ := actual["fake-ip-filter"].([]any)
		if !reflect.DeepEqual(lfCanonical(actualList), lfCanonical(expected)) &&
			lfCanonicalSubset(actualList, expected) {
			upgrade["fake-ip-filter"] = expected
		}
	}
	if expected, ok := policy["dns.nameserver-policy"].(map[string]any); ok {
		if _, added := expected["+.ts.net"]; added {
			legacy := map[string]any{}
			for key, value := range expected {
				if key != "+.ts.net" {
					legacy[key] = value
				}
			}
			if reflect.DeepEqual(lfCanonical(actual["nameserver-policy"]), lfCanonical(legacy)) {
				upgrade["nameserver-policy"] = expected
			}
		}
	}
	if len(upgrade) == 0 {
		return nil
	}
	data, err = json.Marshal(upgrade)
	if err != nil {
		return err
	}
	var updated RawDNS
	if err := json.Unmarshal(data, &updated); err != nil {
		return err
	}
	if _, ok := upgrade["fake-ip-filter"]; ok {
		raw.DNS.FakeIPFilter = updated.FakeIPFilter
	}
	if _, ok := upgrade["nameserver-policy"]; ok {
		raw.DNS.NameServerPolicy = updated.NameServerPolicy
	}
	return nil
}

// Validate PATCH before the controller can create or change any listeners.
func ValidateManagedPatch(document map[string]any) error {
	// The upstream tun PATCH schema uses a non-pointer bool for enable: an
	// omitted enable in a supplied tun object means false, not "unchanged".
	if tun, ok := document["tun"].(map[string]any); ok {
		if _, exists := tun["enable"]; !exists {
			tun["enable"] = false
		}
	}
	_, err := lfValidate(document, true)
	return err
}

func ManagedPolicyID() string {
	_, id, err := lfManagedPolicy()
	if err != nil {
		return ""
	}
	return id
}

// Fixed nodes are snapshot inputs. Mutable sources require explicit trust for
// grants whose leaf name could later refer to a different remote identity.
func lfValidateSubscription(document, targets map[string]any) error {
	providers, _ := document["proxy-providers"].(map[string]any)
	mode, _ := targets["subscription-trust"].(string)
	if mode == "fixed" && len(providers) != 0 {
		return fmt.Errorf("fixed subscription trust refuses proxy providers")
	}
	// Nix JSON is decoded to []any; callers may supply []map values in tests.
	if value := reflect.ValueOf(targets["remote-target-grants"]); value.IsValid() && value.Kind() == reflect.Slice && value.Len() > 0 {
		if len(providers) != 0 && targets["allow-dynamic-remote-grants"] != true {
			return fmt.Errorf("mutable provider identities require explicit remote-grant trust")
		}
	}
	return nil
}
