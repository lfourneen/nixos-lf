package config

import (
	"encoding/json"
	"fmt"
	"net/netip"
	"net/url"

	P "github.com/metacubex/mihomo/constant/provider"
	"github.com/metacubex/mihomo/dns"
)

// Local DNSSEC validation and encrypted upstream selection are separate stages.
// The root policy owns both stages; the Mode-A forwarder is never substituted.
type LFDNSTransport struct {
	DNS *DNS
	// Material queries used by the local DNSSEC validator must stay independent
	// from the selected proxy leaf. The root policy pins these to literal
	// validating DoH endpoints reached through DIRECT.
	Validation      []dns.NameServer
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
	validation, err := lfParseValidationNameservers(raw.LFDNSTransport["validation-nameserver"])
	if err != nil {
		return nil, err
	}
	return &LFDNSTransport{DNS: parsed, Validation: validation, BootstrapListen: bootstrap}, nil
}

func lfParseValidationNameservers(value any) ([]dns.NameServer, error) {
	if value == nil {
		return nil, nil
	}
	data, err := json.Marshal(value)
	if err != nil {
		return nil, fmt.Errorf("managed DNS validation nameservers are invalid")
	}
	var servers []string
	if err := json.Unmarshal(data, &servers); err != nil {
		return nil, fmt.Errorf("managed DNS validation nameservers must be a string list")
	}
	if len(servers) == 0 {
		return nil, nil
	}
	parsed, err := parseNameServer(servers, false, false)
	if err != nil {
		return nil, fmt.Errorf("managed DNS validation nameservers: %w", err)
	}
	for _, server := range parsed {
		endpoint, err := url.Parse(server.Addr)
		if server.ProxyName != "DIRECT" || err != nil || endpoint.Scheme != "https" || endpoint.User != nil {
			return nil, fmt.Errorf("managed DNS validation nameservers require literal HTTPS via DIRECT")
		}
		if _, err := netip.ParseAddr(endpoint.Hostname()); err != nil {
			return nil, fmt.Errorf("managed validation resolver requires a literal address")
		}
	}
	return parsed, nil
}
