package executor

import (
	"github.com/metacubex/mihomo/adapter/inbound"
	"github.com/metacubex/mihomo/config"
	"github.com/metacubex/mihomo/dns"
	"github.com/metacubex/mihomo/log"
)

func updateManagedDNSTransport(c *config.LFDNSTransport, ipv6 bool) {
	if c == nil {
		_ = dns.ReCreateManagedDNSTransports("", "", nil, dns.Resolvers{})
		return
	}
	d := c.DNS
	r := dns.NewResolver(dns.Config{
		Main: d.NameServer, Default: d.DefaultNameserver,
		ProxyServer: d.ProxyServerNameserver, ProxyServerPolicy: d.ProxyServerPolicy,
		IPv6: d.IPv6 && ipv6, IPv6Timeout: d.IPv6Timeout,
		Policy: d.NameServerPolicy, CacheAlgorithm: d.CacheAlgorithm, CacheMaxSize: d.CacheMaxSize,
	})
	lc := inbound.NewListenConfig()
	lc.SetRouteMark(d.ListenRoutingMark)
	if err := dns.ReCreateManagedDNSTransports(d.Listen, c.BootstrapListen, lc, r); err != nil {
		// Missing listeners fail the supervisor's passive identity check.
		log.Errorln("Managed encrypted DNS transport unavailable: %s", err)
	}
}
