package lfguard

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/netip"
	"os"
	"strings"
	"sync/atomic"

	C "github.com/metacubex/mihomo/constant"
)

type policy struct {
	Prefixes           []string `json:"prefixes"`
	AllowedDomains     []string `json:"allowed-domains"`
	MagicDNSSuffixFile string   `json:"magic-dns-suffix-file"`
	parsed             []netip.Prefix
}

var current atomic.Pointer[policy]

func validDomain(domain string) bool {
	if len(domain) == 0 || len(domain) > 253 {
		return false
	}
	for _, label := range strings.Split(domain, ".") {
		if len(label) == 0 || len(label) > 63 || label[0] == '-' || label[len(label)-1] == '-' {
			return false
		}
		for _, c := range label {
			if !(c >= 'a' && c <= 'z' || c >= '0' && c <= '9' || c == '-') {
				return false
			}
		}
	}
	return true
}

func Configure(value map[string]any) error {
	if value == nil {
		current.Store(nil)
		return nil
	}
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	p := new(policy)
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err = decoder.Decode(p); err != nil {
		return err
	}
	if len(p.Prefixes) == 0 {
		return fmt.Errorf("managed target guard requires private prefixes")
	}
	for _, text := range p.Prefixes {
		prefix, err := netip.ParsePrefix(text)
		if err != nil {
			return err
		}
		p.parsed = append(p.parsed, prefix.Masked())
	}
	for _, domain := range p.AllowedDomains {
		if domain != strings.ToLower(domain) || !validDomain(domain) {
			return fmt.Errorf("invalid private domain exception")
		}
	}
	if p.MagicDNSSuffixFile != "" && p.MagicDNSSuffixFile != "/run/netsec-tailnet/domain" {
		return fmt.Errorf("invalid MagicDNS context path")
	}
	current.Store(p)
	return nil
}

func (p *policy) allows(host string) bool {
	for _, domain := range p.AllowedDomains {
		if host == domain || strings.HasSuffix(host, "."+domain) {
			return true
		}
	}
	if p.MagicDNSSuffixFile != "" {
		// This file is maintained by the root-only resolved policy unit, and
		// revoked when the authenticated tailnet changes or disappears.
		if data, err := os.ReadFile(p.MagicDNSSuffixFile); err == nil && len(data) <= 254 {
			domain := strings.TrimSpace(string(data))
			if validDomain(domain) && strings.HasSuffix(domain, ".ts.net") &&
				(host == domain || strings.HasSuffix(host, "."+domain)) {
				return true
			}
		}
	}
	return false
}

func (p *policy) blocked(ip netip.Addr) bool {
	ip = ip.Unmap()
	if !ip.IsValid() || !ip.IsGlobalUnicast() || ip.IsLoopback() || ip.IsLinkLocalUnicast() {
		return true
	}
	for _, prefix := range p.parsed {
		if prefix.Contains(ip) {
			return true
		}
	}
	return false
}

// Protect runs after routing/sniffing but before either TCP or UDP can dial.
// Preserve explicit IP traffic and internal infrastructure; untrusted domain
// targets are resolved once, all answers checked, then sent as an IP so neither
// DIRECT nor a remote proxy can perform a second, unchecked target lookup.
func Protect(ctx context.Context, original, dial *C.Metadata,
	lookup func(context.Context, string) ([]netip.Addr, error)) (*C.Metadata, error) {
	p := current.Load()
	if p == nil || original.Type == C.INNER || original.Host == "" {
		return dial, nil
	}
	host := strings.ToLower(strings.TrimSuffix(original.Host, "."))
	if _, err := netip.ParseAddr(host); err == nil {
		return dial, nil
	}
	if p.allows(host) {
		return dial, nil
	}
	if original.DstIP.IsValid() && p.blocked(original.DstIP) {
		return nil, fmt.Errorf("managed target guard: domain selected a private address")
	}
	ips, err := lookup(ctx, host)
	if err != nil {
		// One retry: a single timeout is usually a resolver warming up, and an
		// unresolved dial must not be reported as a dead node. A pre-resolved
		// private address is still refused above, and every answer of a
		// successful lookup is still checked below.
		ips, err = lookup(ctx, host)
	}
	if err != nil || len(ips) == 0 {
		// The connection proceeds and resolves on its own path; refusing it here
		// turned every DNS hiccup into a host-wide connection failure.
		return dial, nil
	}
	for _, ip := range ips {
		if p.blocked(ip) {
			return nil, fmt.Errorf("managed target guard: private DNS answer")
		}
	}
	result := dial.Clone()
	result.DstIP = ips[0].Unmap()
	// Match the core's existing ResolveIP preference for IPv4, rather than
	// pinning an arbitrary AAAA on a machine without a working IPv6 uplink.
	for _, ip := range ips {
		if ip.Unmap().Is4() {
			result.DstIP = ip.Unmap()
			break
		}
	}
	result.Host = ""
	return result, nil
}
