package lfguard

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/netip"
	"os"
	"sort"
	"strings"
	"sync/atomic"

	C "github.com/metacubex/mihomo/constant"
)

type targetGrant struct {
	Proxy   string   `json:"proxy"`
	CIDR    string   `json:"cidr"`
	Inlets  []string `json:"inlets"`
	Network string   `json:"network"`
	Ports   []uint16 `json:"ports"`
	prefix  netip.Prefix
}

type policy struct {
	ProxyOnly          bool          `json:"proxy-only"`
	Prefixes           []string      `json:"prefixes"`
	AllowedDomains     []string      `json:"allowed-domains"`
	MagicDNSSuffixFile string        `json:"magic-dns-suffix-file"`
	BlockedDomainsFile string        `json:"blocked-domains-file"`
	BlockedDomains     []string      `json:"blocked-domains"`
	BlockExceptions    []string      `json:"blocked-domain-exceptions"`
	LocalTargetCIDRs   []string      `json:"local-target-cidrs"`
	RemoteTargetGrants []targetGrant `json:"remote-target-grants"`
	NAT64Prefixes      []string      `json:"nat64-prefixes"`
	FakeIPPrefix       string        `json:"fake-ip-prefix"`
	local              []netip.Prefix
	nat64              []netip.Prefix
	fake               netip.Prefix
	parsed             []netip.Prefix
	blockedNames       map[string]bool
}

var current atomic.Pointer[policy]

// The managed DNS service preserves HTTPS/SVCB discovery even with fake-IP.
func Managed() bool { return current.Load() != nil }

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
	for _, text := range p.LocalTargetCIDRs {
		prefix, err := internalPrefix(text)
		if err != nil {
			return err
		}
		p.local = append(p.local, prefix)
	}
	for i := range p.RemoteTargetGrants {
		g := &p.RemoteTargetGrants[i]
		prefix, err := internalPrefix(g.CIDR)
		if err != nil {
			return err
		}
		g.prefix = prefix
		if strings.TrimSpace(g.Proxy) != g.Proxy || g.Proxy == "" || len(g.Inlets) == 0 || len(g.Ports) == 0 ||
			(g.Network != "tcp" && g.Network != "udp") {
			return fmt.Errorf("invalid remote target grant")
		}
		for _, inlet := range g.Inlets {
			if !localInlet(inlet) && inlet != "TPROXY" {
				return fmt.Errorf("invalid grant inlet")
			}
		}
		for _, port := range g.Ports {
			if port == 0 {
				return fmt.Errorf("invalid grant port")
			}
		}
	}
	p.nat64 = []netip.Prefix{netip.MustParsePrefix("64:ff9b::/96")}
	for _, text := range p.NAT64Prefixes {
		prefix, err := netip.ParsePrefix(text)
		if err != nil || !prefix.Addr().Is6() || prefix != prefix.Masked() {
			return fmt.Errorf("invalid NAT64 prefix")
		}
		switch prefix.Bits() {
		case 32, 40, 48, 56, 64, 96:
		default:
			return fmt.Errorf("invalid RFC 6052 prefix length")
		}
		p.nat64 = append(p.nat64, prefix)
	}
	// Overlapping declared translators use the most specific prefix.
	sort.Slice(p.nat64, func(i, j int) bool { return p.nat64[i].Bits() > p.nat64[j].Bits() })
	if p.FakeIPPrefix != "" {
		prefix, err := netip.ParsePrefix(p.FakeIPPrefix)
		if err != nil || !prefix.Addr().Is4() || prefix != prefix.Masked() {
			return fmt.Errorf("invalid fake-IP network")
		}
		p.fake = prefix
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

// Authorization scopes are deliberately smaller than the special-address
// denylist: reserved, multicast and synthetic ranges cannot become grants.
var internalScopes = []netip.Prefix{
	netip.MustParsePrefix("10.0.0.0/8"), netip.MustParsePrefix("172.16.0.0/12"),
	netip.MustParsePrefix("192.168.0.0/16"), netip.MustParsePrefix("100.64.0.0/10"),
	netip.MustParsePrefix("127.0.0.0/8"), netip.MustParsePrefix("169.254.0.0/16"),
	netip.MustParsePrefix("fc00::/7"), netip.MustParsePrefix("fe80::/10"),
	netip.MustParsePrefix("::1/128"), netip.MustParsePrefix("0.0.0.0/8"), netip.MustParsePrefix("::/128"),
}

func internalPrefix(text string) (netip.Prefix, error) {
	prefix, err := netip.ParsePrefix(text)
	if err == nil && prefix == prefix.Masked() && !prefix.Addr().Is4In6() {
		for _, scope := range internalScopes {
			if scope.Bits() <= prefix.Bits() && scope.Contains(prefix.Addr()) {
				return prefix, nil
			}
		}
	}
	return netip.Prefix{}, fmt.Errorf("target CIDR must be canonical and internal: %s", text)
}

var metadataTargets = []netip.Prefix{
	netip.MustParsePrefix("169.254.169.254/32"), netip.MustParsePrefix("169.254.170.2/32"),
	netip.MustParsePrefix("100.100.100.200/32"), netip.MustParsePrefix("fd00:ec2::254/128"),
}

func contains(prefixes []netip.Prefix, ip netip.Addr) bool {
	for _, prefix := range prefixes {
		if prefix.Contains(ip) {
			return true
		}
	}
	return false
}

func (p *policy) translated(ip netip.Addr) (netip.Addr, bool) {
	for _, prefix := range p.nat64 {
		if !prefix.Contains(ip) {
			continue
		}
		value := ip.As16()
		var embedded [4]byte
		pos := prefix.Bits() / 8
		for i := range embedded {
			if pos == 8 {
				pos++
			} // RFC 6052 reserved u octet
			embedded[i] = value[pos]
			pos++
		}
		return netip.AddrFrom4(embedded), true
	}
	return netip.Addr{}, false
}

func (p *policy) unsafe(ip netip.Addr) bool {
	ip = ip.Unmap()
	if !ip.IsValid() || ip.Zone() != "" || ip.IsUnspecified() || ip.IsMulticast() ||
		(ip.Is4() && netip.MustParsePrefix("0.0.0.0/8").Contains(ip)) ||
		contains(metadataTargets, ip) || (p.fake.IsValid() && p.fake.Contains(ip)) {
		return true
	}
	if embedded, ok := p.translated(ip); ok {
		return p.blocked(embedded)
	}
	return contains(specialTargets, ip) && ip.String() != "192.0.0.9" && ip.String() != "192.0.0.10"
}

func (p *policy) blocked(ip netip.Addr) bool {
	ip = ip.Unmap()
	if p.unsafe(ip) {
		return true
	}
	if _, ok := p.translated(ip); ok {
		return false
	}
	return !ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() ||
		contains(internalScopes, ip) || contains(p.parsed, ip)
}

func infrastructure(m *C.Metadata) bool { return m.Type == C.INNER && !m.LFRuleDNS }

func localInlet(inlet string) bool {
	switch inlet {
	case "HTTP", "HTTPS", "SOCKS4", "SOCKS5", "REDIR", "TUN":
		return true
	}
	return false
}

// Called at the actual leaf, never by guessing a group's next selection. The
// same route is retained for every destination in a reused UDP association.
func CheckEgress(m *C.Metadata, direct bool, leaf string) error {
	p := current.Load()
	if p == nil || infrastructure(m) {
		return nil
	}
	if direct {
		if err := CheckDirect(m); err != nil {
			return err
		}
	}
	ip := m.DstIP.Unmap()
	if m.Host != "" || p.unsafe(ip) {
		return fmt.Errorf("managed target guard: unsafe or unpinned target")
	}
	m.LFEgressDirect, m.LFEgressProxy = direct, leaf
	if !p.blocked(ip) {
		return nil
	}
	if !m.LFLocalTarget {
		return fmt.Errorf("managed target guard: private DNS target")
	}
	if direct && localInlet(strings.ToUpper(m.Type.String())) && contains(p.local, ip) {
		return nil
	}
	if !direct {
		for _, g := range p.RemoteTargetGrants {
			if g.Proxy != leaf || g.Network != strings.ToLower(m.NetWork.String()) || !g.prefix.Contains(ip) {
				continue
			}
			inlet, port := false, false
			for _, v := range g.Inlets {
				inlet = inlet || v == strings.ToUpper(m.Type.String())
			}
			for _, v := range g.Ports {
				port = port || v == m.DstPort
			}
			if inlet && port {
				return nil
			}
		}
	}
	return fmt.Errorf("managed target guard: internal target is not authorized for this egress")
}

// Protect runs before TCP/UDP dialing. All application names, including local
// suffixes, resolve locally and remain pinned. Synthetic addresses must already
// have been recovered to their name by preHandleMetadata.
func Protect(ctx context.Context, original, dial *C.Metadata,
	lookup func(context.Context, string) ([]netip.Addr, error)) (*C.Metadata, error) {
	p := current.Load()
	if p == nil || infrastructure(original) || dial.LFGuarded {
		return dial, nil
	}
	host := strings.ToLower(strings.TrimSuffix(original.Host, "."))
	result := dial.Clone()
	result.LFGuarded = true
	ip, literalErr := netip.ParseAddr(host)
	if host == "" || literalErr == nil {
		if literalErr != nil {
			ip = original.DstIP
		}
		ip = ip.Unmap()
		if p.unsafe(ip) {
			return nil, fmt.Errorf("managed target guard: unsafe literal target")
		}
		result.Host, result.DstIP, result.LFLocalTarget = "", ip, true
		return result, nil
	}
	if err := CheckDomain(host); err != nil {
		return nil, err
	}
	result.LFLocalTarget = p.allows(host)
	if original.DstIP.IsValid() && (p.unsafe(original.DstIP) || (!result.LFLocalTarget && p.blocked(original.DstIP))) {
		return nil, fmt.Errorf("managed target guard: domain selected a private address")
	}
	if lookup == nil {
		return nil, fmt.Errorf("managed target guard: no resolver")
	}
	ips, err := lookup(ctx, host)
	if err != nil {
		ips, err = lookup(ctx, host)
	}
	if err != nil || len(ips) == 0 {
		return nil, fmt.Errorf("managed target guard: DNS target could not be validated")
	}
	for _, ip := range ips {
		if p.unsafe(ip) || (!result.LFLocalTarget && p.blocked(ip)) {
			return nil, fmt.Errorf("managed target guard: private DNS answer")
		}
	}
	result.DstIP = ips[0].Unmap()
	for _, ip := range ips {
		if ip.Unmap().Is4() {
			result.DstIP = ip.Unmap()
			break
		}
	}
	result.Host = ""
	return result, nil
}
