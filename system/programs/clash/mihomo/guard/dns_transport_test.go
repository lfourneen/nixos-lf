package dns

import (
	"context"
	"net"
	"sync/atomic"
	"testing"

	D "github.com/miekg/dns"
)

type lfRawTransportClient struct {
	calls   atomic.Int32
	address string
	last    *D.Msg
}

func (c *lfRawTransportClient) Address() string  { return "local test" }
func (c *lfRawTransportClient) ResetConnection() {}
func (c *lfRawTransportClient) ExchangeContext(_ context.Context, request *D.Msg) (*D.Msg, error) {
	c.calls.Add(1)
	c.last = request.Copy()
	reply := new(D.Msg).SetReply(request)
	if c.address != "" {
		record, _ := D.NewRR(request.Question[0].Name + " 60 IN A " + c.address)
		reply.Answer = []D.RR{record}
	}
	return reply, nil
}

func TestLFManagedTransportPreservesRawDNSSECAndFreshness(t *testing.T) {
	client := &lfRawTransportClient{address: "203.0.113.42"}
	r := NewResolverFromClient(client)
	request := new(D.Msg)
	request.SetQuestion("unsigned.example.", D.TypeA)
	// Seed the ordinary resolver cache without DNSSEC material.
	if _, err := r.ExchangeContext(context.Background(), request); err != nil {
		t.Fatal(err)
	}
	client.address = ""
	reply, err := (lfTransportService{r}).ServeMsg(context.Background(), request)
	if err != nil || client.calls.Load() != 2 || len(reply.Answer) != 0 {
		t.Fatal("transport reused an ordinary cached answer", reply, err, client.calls.Load())
	}
	if !client.last.CheckingDisabled || client.last.IsEdns0() == nil || !client.last.IsEdns0().Do() {
		t.Fatal("local validator did not receive complete DNSSEC material", client.last)
	}
	if request.CheckingDisabled || request.IsEdns0() != nil {
		t.Fatal("transport mutated its caller's request")
	}
}

func TestLFManagedTransportRolesAndLifecycle(t *testing.T) {
	addresses := []string{}
	for attempt := 0; attempt < 2; attempt++ {
		listener, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			t.Fatal(err)
		}
		addresses = append(addresses, listener.Addr().String())
		_ = listener.Close()
	}
	app := NewResolverFromClient(&lfRawTransportClient{address: "203.0.113.42"})
	bootstrap := NewResolverFromClient(&lfRawTransportClient{address: "203.0.113.43"})
	t.Cleanup(func() { _ = ReCreateManagedDNSTransports("", "", nil, Resolvers{}) })
	for attempt := 0; attempt < 2; attempt++ {
		if err := ReCreateManagedDNSTransports(addresses[0], addresses[1], &net.ListenConfig{},
			Resolvers{Resolver: app, ProxyResolver: bootstrap}); err != nil {
			t.Fatal(err)
		}
		for index, address := range addresses {
			for _, network := range []string{"tcp", "udp"} {
				request := new(D.Msg)
				request.SetQuestion("example.", D.TypeA)
				reply, _, err := (&D.Client{Net: network}).Exchange(request, address)
				if err != nil || len(reply.Answer) != 1 {
					t.Fatal(reply, err)
				}
				want := []string{"203.0.113.42", "203.0.113.43"}[index]
				if reply.Answer[0].(*D.A).A.String() != want {
					t.Fatal("resolver roles merged", reply)
				}
			}
		}
	}
	if err := ReCreateManagedDNSTransports("0.0.0.0:1057", addresses[1], &net.ListenConfig{},
		Resolvers{Resolver: app, ProxyResolver: bootstrap}); err == nil {
		t.Fatal("non-loopback transport listener accepted")
	}
}
