package dns

import (
	"context"
	"testing"

	"github.com/metacubex/mihomo/component/lfguard"
	C "github.com/metacubex/mihomo/constant"
	D "github.com/miekg/dns"
)

func TestLFManagedDNSBeforeHostsFakeIPAndCache(t *testing.T) {
	if err := lfguard.Configure(map[string]any{
		"prefixes": []string{"10.0.0.0/8"}, "blocked-domains": []string{"ads.example"},
	}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = lfguard.Configure(nil) })
	for _, mode := range []C.DNSMode{C.DNSNormal, C.DNSMapping, C.DNSFakeIP} {
		// Uninitialized hosts/fake-IP/cache dependencies deliberately panic if
		// reached: a blocked name must terminate before any of these layers.
		service := NewService(nil, &ResolverEnhancer{mode: mode, useHosts: true})
		for _, kind := range []uint16{D.TypeA, D.TypeAAAA, D.TypeHTTPS, D.TypeTXT} {
			request := new(D.Msg)
			request.SetQuestion("Pixel.ADS.example.", kind)
			request.SetEdns0(1232, true)
			reply, err := service.ServeMsg(context.Background(), request)
			if err != nil || reply.Rcode != D.RcodeNameError || len(reply.Answer) != 0 || reply.AuthenticatedData {
				t.Fatal("filter did not precede DNS service", mode, kind, reply, err)
			}
			if reply.IsEdns0() == nil {
				t.Fatal("lost EDNS0 response")
			}
			// Internal real-IP/bootstrap lookups bypass the service/fake-IP
			// handler, but still cannot read a cached or upstream blocked name.
			reply, err = new(Resolver).ExchangeContext(context.Background(), request)
			if err != nil || reply.Rcode != D.RcodeNameError {
				t.Fatal(reply, err)
			}
		}
	}
}
