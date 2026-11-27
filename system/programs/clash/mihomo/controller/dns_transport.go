package executor

import (
	"github.com/metacubex/mihomo/adapter/inbound"
	"github.com/metacubex/mihomo/config"
	"github.com/metacubex/mihomo/dns"
	"github.com/metacubex/mihomo/log"
)

func updateManagedDNSTransport(c *config.LFDNSTransport, ipv6 bool) {
	if c == nil {
		_ = dns.ReCreateManagedDNSTransports("", "", nil, nil, nil, dns.Resolvers{})
		return
	}
	d := c.DNS
	r := dns.NewResolver(dns.Config{
		Main: d.NameServer, Default: d.DefaultNameserver,
		ProxyServer: d.ProxyServerNameserver, ProxyServerPolicy: d.ProxyServerPolicy,
		IPv6: d.IPv6 && ipv6, IPv6Timeout: d.IPv6Timeout,
		Policy: d.NameServerPolicy, CacheAlgorithm: d.CacheAlgorithm, CacheMaxSize: d.CacheMaxSize,
	})
	// Retry default Rules upstreams through the application selector. A fixed
	// exit or a differently configured upstream has no alternate authority.
	var alternate *dns.Resolver
	var servers []dns.NameServer
	for _, server := range d.NameServer {
		if server.ProxyName != "lf-dns" {
			servers = nil
			break
		}
		server.ProxyName = "lf-remote"
		servers = append(servers, server)
	}
	if len(servers) > 0 {
		alternate = dns.NewResolver(dns.Config{Main: servers, IPv6: d.IPv6 && ipv6}).Resolver
	}
	var validation *dns.Resolver
	if len(c.Validation) > 0 {
		validation = dns.NewResolver(dns.Config{
			Main: c.Validation,
			IPv6: d.IPv6 && ipv6, IPv6Timeout: d.IPv6Timeout,
			CacheAlgorithm: d.CacheAlgorithm, CacheMaxSize: d.CacheMaxSize,
		}).Resolver
	}
	lc := inbound.NewListenConfig()
	lc.SetRouteMark(d.ListenRoutingMark)
	if err := dns.ReCreateManagedDNSTransports(d.Listen, c.BootstrapListen, validation, alternate, lc, r); err != nil {
		// Missing listeners fail the supervisor's passive identity check.
		log.Errorln("Managed encrypted DNS transport unavailable: %s", err)
	}
}
