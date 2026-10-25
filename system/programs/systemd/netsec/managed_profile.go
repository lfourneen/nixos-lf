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
	data, err := json.Marshal(raw)
	if err != nil {
		return "", err
	}
	var document map[string]any
	if err = json.Unmarshal(data, &document); err != nil {
		return "", err
	}
	return lfValidate(document, false)
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
