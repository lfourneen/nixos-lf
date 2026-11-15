package config

import (
	"encoding/json"
	"fmt"

	P "github.com/metacubex/mihomo/constant/provider"
)

// Local DNSSEC validation and encrypted upstream selection are separate stages.
// The root policy owns both stages; the Mode-A forwarder is never substituted.
type LFDNSTransport struct {
	DNS             *DNS
	BootstrapListen string
}

func lfParseDNSTransport(raw *RawConfig, providers map[string]P.RuleProvider) (*LFDNSTransport, error) {
	if len(raw.LFDNSTransport) == 0 {
		return nil, nil
	}
	data, err := json.Marshal(raw.LFDNSTransport["dns"])
	if err != nil {
		return nil, err
	}
	forward := *raw
	forward.DNS = RawDNS{}
	if err = json.Unmarshal(data, &forward.DNS); err != nil {
		return nil, err
	}
	parsed, err := parseDNS(&forward, providers)
	if err != nil {
		return nil, err
	}
	bootstrap, _ := raw.LFDNSTransport["bootstrap-listen"].(string)
	if !parsed.Enable || parsed.Listen == "" || bootstrap == "" || len(parsed.ProxyServerNameserver) == 0 {
		return nil, fmt.Errorf("managed DNS transport requires application and bootstrap resolvers")
	}
	return &LFDNSTransport{DNS: parsed, BootstrapListen: bootstrap}, nil
}
