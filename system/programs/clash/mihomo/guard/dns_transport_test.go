package dns

import (
	"context"
	"net"
	"sync/atomic"
	"testing"
	"time"

	"github.com/metacubex/mihomo/component/trie"
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

type lfRefusedTransportClient struct {
	calls atomic.Int32
}

func (c *lfRefusedTransportClient) Address() string  { return "local refusal test" }
func (c *lfRefusedTransportClient) ResetConnection() {}
func (c *lfRefusedTransportClient) ExchangeContext(_ context.Context, request *D.Msg) (*D.Msg, error) {
	c.calls.Add(1)
	return new(D.Msg).SetRcode(request, D.RcodeServerFailure), nil
}

func TestLFManagedTransportValidationFallback(t *testing.T) {
	remote := &lfRefusedTransportClient{}
	validation := &lfRawTransportClient{address: "203.0.113.7"}
	service := &lfTransportService{
		resolver: NewResolverFromClient(remote), validation: NewResolverFromClient(validation),
	}
	request := new(D.Msg)
	request.SetQuestion("example.", D.TypeDNSKEY)
	reply, err := service.ServeMsg(context.Background(), request)
	if err != nil || reply == nil || len(reply.Answer) != 1 {
		t.Fatal("validation material did not fall back to DIRECT", reply, err)
	}
	if address := reply.Answer[0].(*D.A).A.String(); address != "203.0.113.7" {
		t.Fatalf("validation material came from the wrong resolver: %s", address)
	}
	if remote.calls.Load() < 1 || validation.calls.Load() != 1 {
		t.Fatalf("validation material did not try the leaf then DIRECT: remote=%d validation=%d",
			remote.calls.Load(), validation.calls.Load())
	}

	// Application records still use the selected leaf when it answers.
	leaf := &lfRawTransportClient{address: "203.0.113.8"}
	service = &lfTransportService{resolver: NewResolverFromClient(leaf),
		validation: NewResolverFromClient(validation)}
	application := new(D.Msg)
	application.SetQuestion("application.example.", D.TypeA)
	if _, err := service.ServeMsg(context.Background(), application); err != nil {
		t.Fatal(err)
	}
	if leaf.calls.Load() != 1 || validation.calls.Load() != 1 {
		t.Fatalf("application query used the validation resolver: leaf=%d validation=%d",
			leaf.calls.Load(), validation.calls.Load())
	}
}

func TestLFManagedTransportValidationHold(t *testing.T) {
	remote := &lfRawTransportClient{address: "203.0.113.9"}
	validation := &lfRawTransportClient{address: "203.0.113.10"}
	service := &lfTransportService{resolver: NewResolverFromClient(remote),
		validation: NewResolverFromClient(validation)}
	service.directUntil.Store(time.Now().Add(time.Minute).UnixNano())
	request := new(D.Msg)
	request.SetQuestion("example.", D.TypeDNSKEY)
	reply, err := service.ServeMsg(context.Background(), request)
	if err != nil || reply == nil || len(reply.Answer) != 1 {
		t.Fatal(reply, err)
	}
	if remote.calls.Load() != 0 || validation.calls.Load() != 1 {
		t.Fatalf("degraded window retried the unavailable leaf: remote=%d validation=%d",
			remote.calls.Load(), validation.calls.Load())
	}
}

func TestLFManagedTransportValidationMaterialClassification(t *testing.T) {
	for _, kind := range []uint16{D.TypeDS, D.TypeDNSKEY, D.TypeNSEC, D.TypeNSEC3, D.TypeRRSIG,
		D.TypeCDS, D.TypeCDNSKEY, D.TypeTA, D.TypeDLV} {
		if !lfValidationMaterial(D.Question{Name: "example.", Qtype: kind}) {
			t.Fatalf("DNSSEC material type %d was not classified", kind)
		}
	}
	if !lfValidationMaterial(D.Question{Name: "_ta-4f66-9728.", Qtype: D.TypeNULL}) {
		t.Fatal("RFC 5011 key-tag probe was not classified")
	}
	for _, kind := range []uint16{D.TypeA, D.TypeAAAA, D.TypeHTTPS, D.TypeCNAME, D.TypeTXT} {
		if lfValidationMaterial(D.Question{Name: "www.example.com.", Qtype: kind}) {
			t.Fatalf("application type %d was classified as DNSSEC material", kind)
		}
	}
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
	reply, err := (&lfTransportService{resolver: r}).ServeMsg(context.Background(), request)
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
	t.Cleanup(func() { _ = ReCreateManagedDNSTransports("", "", nil, nil, nil, Resolvers{}) })
	for attempt := 0; attempt < 2; attempt++ {
		if err := ReCreateManagedDNSTransports(addresses[0], addresses[1], nil, nil, &net.ListenConfig{},
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
	if err := ReCreateManagedDNSTransports("0.0.0.0:1057", addresses[1], nil, nil, &net.ListenConfig{},
		Resolvers{Resolver: app, ProxyResolver: bootstrap}); err == nil {
		t.Fatal("non-loopback transport listener accepted")
	}
}

func TestLFManagedApplicationUsesRemoteAlternateAfterFailure(t *testing.T) {
	primary := &lfRefusedTransportClient{}
	alternate := &lfRawTransportClient{address: "203.0.113.8"}
	validation := &lfRawTransportClient{address: "203.0.113.9"}
	service := &lfTransportService{resolver: NewResolverFromClient(primary),
		alternate: NewResolverFromClient(alternate), validation: NewResolverFromClient(validation)}
	query := new(D.Msg)
	query.SetQuestion("foreign.example.", D.TypeA)
	reply, err := service.ServeMsg(context.Background(), query)
	if err != nil || len(reply.Answer) != 1 || alternate.calls.Load() != 1 || validation.calls.Load() != 0 {
		t.Fatal("foreign application fallback did not stay on the remote path", reply, err)
	}
	domestic := &lfRefusedTransportClient{}
	domains := trie.New[[]dnsClient]()
	if err := domains.Insert("domestic.example", []dnsClient{domestic}); err != nil {
		t.Fatal(err)
	}
	service.resolver.policy = []dnsPolicy{domainTriePolicy{domains}}
	query.SetQuestion("domestic.example.", D.TypeA)
	if _, err := service.ServeMsg(context.Background(), query); err == nil || domestic.calls.Load() != 1 ||
		alternate.calls.Load() != 1 || validation.calls.Load() != 0 {
		t.Fatal("explicit domain policy escaped its resolver")
	}
}
