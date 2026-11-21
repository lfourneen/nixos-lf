package config

import (
	"fmt"
	"strconv"
	"strings"
)

// Configured and cached groups share the system test endpoint. Apply the
// defaults after validating fixed egress's immutable source group.
func lfApplyDelayProfile(raw *RawConfig) {
	target, _ := raw.LFPrivateTargets["delay-test-url"].(string)
	if target == "" {
		return
	}
	for _, group := range raw.ProxyGroup {
		if group == nil {
			continue
		}
		kind, _ := lfDelayField(group, "type").(string)
		lfConstrainDelay(group, target)
		if kind == "select" || kind == "relay" {
			group["interval"] = 0
		}
	}
	for _, provider := range raw.ProxyProvider {
		if provider == nil {
			continue
		}
		value := lfDelayField(provider, "health-check")
		check, ok := value.(map[string]any)
		if !ok {
			if value != nil {
				continue // Let the provider decoder report a malformed health-check.
			}
			check = map[string]any{"enable": false}
		}
		lfConstrainDelay(check, target)
		provider["health-check"] = check
	}
}

func lfDelayField(fields map[string]any, name string) any {
	if value, ok := fields[name]; ok {
		return value
	}
	for key, value := range fields {
		if strings.EqualFold(key, name) {
			return value
		}
	}
	return nil
}

func lfConstrainDelay(fields map[string]any, target string) {
	fields["url"] = target
	fields["interval"] = lfDelayLimit(fields, "interval", 300, 86400, 300)
	fields["timeout"] = lfDelayLimit(fields, "timeout", 100, 5000, 5000)
	fields["expected-status"] = ""
}

func lfDelayLimit(fields map[string]any, name string, minimum, maximum, defaultValue int) int {
	value, err := strconv.Atoi(fmt.Sprint(lfDelayField(fields, name)))
	if err != nil || value == 0 {
		return defaultValue
	}
	if value < minimum {
		return minimum
	}
	if value > maximum {
		return maximum
	}
	return value
}
