package config

// Applied at the final RawConfig parser, after every GUI/profile extension.
// No subscriptions, credentials or destinations are included in the policy ID.
import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
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
	data, err := json.Marshal(raw)
	if err != nil {
		return "", err
	}
	var document map[string]any
	if err = json.Unmarshal(data, &document); err != nil {
		return "", err
	}
	id, err := lfValidate(document, false)
	if err == nil {
		err = lfguard.Configure(raw.LFPrivateTargets)
	}
	return id, err
}

// Upgrade only known older managed values before the final policy check, on
// every full-config path. Controller PATCH remains strict; no caller-supplied
// value may weaken the resulting policy.
func lfManagedProfileDefaults(raw *RawConfig) error {
	policy, _, err := lfManagedPolicy()
	if err != nil || policy == nil {
		return err
	}
	if value, ok := policy["lf-private-targets"].(map[string]any); ok {
		// Entirely derived from the root policy: a cached profile may predate
		// this generation or carry an older prefix list, and refusing to start
		// over a field the policy already owns is a lockout, not a defence.
		// PATCH (ValidateManagedPatch) stays strict.
		raw.LFPrivateTargets = value
	}
	if err := lfUpgradeMagicDNS(raw, policy); err != nil {
		return err
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
	data, err := json.Marshal(raw)
	if err != nil {
		return err
	}
	var document map[string]any
	if err = json.Unmarshal(data, &document); err != nil {
		return err
	}
	tun, _ := document["tun"].(map[string]any)
	if tun == nil {
		return nil
	}
	actual, ok := tun["route-exclude-address"].([]any)
	if ok && reflect.DeepEqual(lfCanonical(actual), lfCanonical(want)) {
		return nil
	}
	tun["route-exclude-address"] = want
	merged, err := json.Marshal(document)
	if err != nil {
		return err
	}
	return json.Unmarshal(merged, raw)
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
	if expected, ok := policy["dns.fake-ip-filter"].([]any); ok {
		legacy := make([]any, 0, len(expected))
		added := false
		for _, value := range expected {
			if value == "+.ts.net" {
				added = true
			} else {
				legacy = append(legacy, value)
			}
		}
		if added && reflect.DeepEqual(lfCanonical(actual["fake-ip-filter"]), lfCanonical(legacy)) {
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
