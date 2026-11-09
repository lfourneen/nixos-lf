package lfguard

import (
	"bufio"
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
	ProxyOnly          bool     `json:"proxy-only"`
	Prefixes           []string `json:"prefixes"`
	AllowedDomains     []string `json:"allowed-domains"`
	MagicDNSSuffixFile string   `json:"magic-dns-suffix-file"`
	BlockedDomainsFile string   `json:"blocked-domains-file"`
	BlockedDomains     []string `json:"blocked-domains"`
	BlockExceptions    []string `json:"blocked-domain-exceptions"`
	parsed             []netip.Prefix
	blockedNames       map[string]bool
}

var current atomic.Pointer[policy]

// Called by the DIRECT adapter before opening TCP or UDP sockets, including
// named direct adapters and COMPATIBLE. A group selecting DIRECT cannot bypass
// this check. Internal resolver/bootstrap metadata has a separate trust scope.
func CheckDirect(metadata *C.Metadata) error {
	p := current.Load()
	if p != nil && p.ProxyOnly && (metadata.Type != C.INNER || metadata.LFRuleDNS) {
		scope := "application"
		if metadata.LFRuleDNS {
			scope = "rule-routed DNS"
		}
		return fmt.Errorf("managed proxy policy refuses %s DIRECT egress", scope)
	}
	return nil
}

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
	if err := p.loadBlocklist(); err != nil {
		return err
	}
	current.Store(p)
	return nil
}

// Match dnsmasq's bare suffix rules, including underscores and longest-match
// exceptions. The store file is part of the root policy's closure, never a
// subscription-controlled provider or a runtime download.
func filterDomain(value string) bool {
	if value == "" {
		return false
	}
	for _, label := range strings.Split(value, ".") {
		if label == "" {
			return false
		}
		for _, c := range label {
			if !(c >= 'a' && c <= 'z' || c >= '0' && c <= '9' || c == '-' || c == '_') {
				return false
			}
		}
	}
	return true
}

func (p *policy) loadBlocklist() error {
	p.blockedNames = make(map[string]bool)
	add := func(name string, blocked bool) error {
		if !filterDomain(name) {
			return fmt.Errorf("invalid blocklist domain")
		}
		p.blockedNames[name] = blocked
		return nil
	}
	if p.BlockedDomainsFile != "" {
		file, err := os.Open(p.BlockedDomainsFile)
		if err != nil {
			return fmt.Errorf("managed blocklist: %w", err)
		}
		defer file.Close()
		scanner := bufio.NewScanner(file)
		for scanner.Scan() {
			if err := add(scanner.Text(), true); err != nil {
				return err
			}
		}
		if err := scanner.Err(); err != nil {
			return err
		}
	}
	for _, name := range p.BlockedDomains {
		if err := add(name, true); err != nil {
			return err
		}
	}
	// dnsmasq's server=/name/# wins over address=/name/ at equal specificity.
	for _, name := range p.BlockExceptions {
		if err := add(name, false); err != nil {
			return err
		}
	}
	return nil
}

func CheckDomain(host string) error {
	p := current.Load()
	if p == nil {
		return nil
	}
	host = strings.ToLower(strings.TrimSuffix(host, "."))
	for host != "" {
		if blocked, found := p.blockedNames[host]; found {
			if blocked {
				return fmt.Errorf("managed DNS filter: blocked domain")
			}
			return nil
		}
		_, rest, found := strings.Cut(host, ".")
		if !found {
			break
		}
		host = rest
	}
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

// IsGlobalUnicast includes several non-public and transition ranges. This is
// a targeted denylist, not a claim that every IANA special prefix is rejected.
var specialTargets = []netip.Prefix{
	netip.MustParsePrefix("192.0.0.0/24"),
	netip.MustParsePrefix("198.18.0.0/15"),
	netip.MustParsePrefix("240.0.0.0/4"),
	netip.MustParsePrefix("::/96"),
	netip.MustParsePrefix("64:ff9b:1::/48"),
	netip.MustParsePrefix("2001::/32"),
	netip.MustParsePrefix("2002::/16"),
}

var wellKnownNAT64 = netip.MustParsePrefix("64:ff9b::/96")

func (p *policy) blocked(ip netip.Addr) bool {
	ip = ip.Unmap()
	if !ip.IsValid() || !ip.IsGlobalUnicast() || ip.IsLoopback() || ip.IsLinkLocalUnicast() {
		return true
	}
	if wellKnownNAT64.Contains(ip) {
		value := ip.As16()
		return p.blocked(netip.AddrFrom4([4]byte{value[12], value[13], value[14], value[15]}))
	}
	for _, prefix := range specialTargets {
		// IANA's globally reachable PCP/TURN anycast exceptions.
		if prefix.Contains(ip) && ip.String() != "192.0.0.9" && ip.String() != "192.0.0.10" {
			return true
		}
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
	if err := CheckDomain(host); err != nil {
		return nil, err
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
		// An unverified second lookup must not bypass private-target validation.
		return nil, fmt.Errorf("managed target guard: DNS target could not be validated")
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
