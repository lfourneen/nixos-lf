package dns

import (
	"context"
	"net"
	"testing"
	"time"

	"github.com/metacubex/mihomo/component/fakeip"
	"github.com/metacubex/mihomo/component/lfguard"
	C "github.com/metacubex/mihomo/constant"
	icontext "github.com/metacubex/mihomo/context"
	D "github.com/miekg/dns"
)

type desktopTestDNS struct {
	started chan struct{}
	stopped chan struct{}
}

func (d desktopTestDNS) Address() string  { return "desktop DNS fixture" }
func (d desktopTestDNS) ResetConnection() {}
func (d desktopTestDNS) ExchangeContext(ctx context.Context, _ *D.Msg) (*D.Msg, error) {
	d.started <- struct{}{}
	<-ctx.Done()
	d.stopped <- struct{}{}
	return nil, ctx.Err()
}

func TestLFManagedDesktopDNSDoesNotDetachOnCancellation(t *testing.T) {
	client := desktopTestDNS{make(chan struct{}, 4), make(chan struct{}, 4)}
	resolver := NewResolverFromClient(client)
	ctx, cancel := context.WithCancel(lfguard.WithDesktopDNS(context.Background()))
	defer cancel()
	query := new(D.Msg).SetQuestion("desktop.example.", D.TypeA)
	done := make(chan error, 1)
	go func() {
		_, err := resolver.ExchangeContext(ctx, query)
		done <- err
	}()
	select {
	case <-client.started:
	case <-time.After(time.Second):
		t.Fatal("DNS exchange did not reach its fixture")
	}
	cancel()
	select {
	case <-client.stopped:
	case <-time.After(time.Second):
		t.Fatal("DNS exchange lost its caller context")
	}
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("canceled DNS query reported success")
		}
	case <-time.After(time.Second):
		t.Fatal("canceled DNS exchange did not return")
	}
	select {
	case <-client.started:
		t.Fatal("canceled DNS exchange continued in the background")
	case <-time.After(100 * time.Millisecond):
	}
}

func TestLFManagedValidationPrecedesSyntheticAnswers(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	started := make(chan struct{})
	server := &D.Server{Listener: listener, Net: "tcp", NotifyStartedFunc: func() { close(started) },
		Handler: D.HandlerFunc(func(w D.ResponseWriter, r *D.Msg) {
			if r.CheckingDisabled {
				t.Error("client CD reached local validator")
			}
			reply := new(D.Msg).SetReply(r)
			switch r.Question[0].Name {
			case "bogus.example.":
				reply.Rcode = D.RcodeServerFailure
			case "missing.example.":
				reply.Rcode = D.RcodeNameError
			case "empty.example.":
				// NOERROR without the requested address is NODATA.
			case "alias-only.example.":
				record, _ := D.NewRR("alias-only.example. 60 IN CNAME empty.example.")
				reply.Answer = []D.RR{record}
			default:
				record, _ := D.NewRR(r.Question[0].Name + " 60 IN A 203.0.113.10")
				reply.Answer = []D.RR{record}
			}
			_ = w.WriteMsg(reply)
		})}
	go func() { _ = server.ActivateAndServe() }()
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("validator startup")
	}
	t.Cleanup(func() { _ = server.Shutdown(); _ = lfguard.Configure(nil) })
	if err := lfguard.Configure(map[string]any{"prefixes": []string{"10.0.0.0/8"}, "local-dnssec": listener.Addr().String()}); err != nil {
		t.Fatal(err)
	}
	calls := 0
	handler := lfDomainFilter(func(ctx *icontext.DNSContext, r *D.Msg) (*D.Msg, error) {
		calls++
		ctx.SetType(icontext.DNSTypeFakeIP)
		reply := new(D.Msg).SetReply(r)
		reply.AuthenticatedData = true
		return reply, nil
	}, true)
	for _, test := range []struct {
		name  string
		code  int
		calls int
	}{
		{"bogus.example.", D.RcodeServerFailure, 0}, {"missing.example.", D.RcodeNameError, 0},
		{"empty.example.", D.RcodeSuccess, 0}, {"alias-only.example.", D.RcodeSuccess, 0},
		{"unsigned.example.", D.RcodeSuccess, 1},
	} {
		r := new(D.Msg)
		r.SetQuestion(test.name, D.TypeA)
		r.CheckingDisabled = true
		reply, err := handler(icontext.NewDNSContext(context.Background()), r)
		if err != nil || reply.Rcode != test.code || calls != test.calls || reply.AuthenticatedData {
			t.Fatal(test, reply, err, calls)
		}
	}
	_ = server.Shutdown()
	for _, size := range []int{0, 2} {
		request := new(D.Msg)
		request.SetQuestion("bogus.example.", D.TypeA)
		if size == 0 {
			request.Question = nil
		} else {
			request.Question = append(request.Question, request.Question[0])
		}
		reply, err := handler(icontext.NewDNSContext(context.Background()), request)
		if err != nil || reply.Rcode != D.RcodeFormatError || calls != 1 {
			t.Fatal("malformed query bypassed validation", reply, err, calls)
		}
	}
	r := new(D.Msg)
	r.SetQuestion("unsigned.example.", D.TypeA)
	if _, err := handler(icontext.NewDNSContext(context.Background()), r); err == nil || calls != 1 {
		t.Fatal("validator outage bypassed", err, calls)
	}
}

func TestLFManagedServiceRecordsThroughFakeIP(t *testing.T) {
	if err := lfguard.Configure(map[string]any{"prefixes": []string{"10.0.0.0/8"}}); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = lfguard.Configure(nil) })
	for _, kind := range []uint16{D.TypeHTTPS, D.TypeSVCB} {
		request := new(D.Msg)
		request.SetQuestion("origin.example.", kind)
		calls := 0
		next := func(_ *icontext.DNSContext, message *D.Msg) (*D.Msg, error) {
			calls++
			reply := new(D.Msg).SetReply(message)
			record, err := D.NewRR("origin.example. 60 IN " + D.TypeToString[kind] + " 1 . alpn=\"http/1.1\" ech=AAUAAwABAA==")
			if err != nil {
				t.Fatal(err)
			}
			reply.Answer = []D.RR{record}
			return reply, nil
		}
		handler := withFakeIP(&fakeip.Skipper{}, nil, nil, 60)(next)
		reply, err := handler(icontext.NewDNSContext(context.Background()), request)
		if err != nil || calls != 1 || len(reply.Answer) != 1 {
			t.Fatal("lost service record", kind, reply, err)
		}
		// Unmanaged fixtures retain upstream fake-IP suppression.
		_ = lfguard.Configure(nil)
		reply, err = handler(icontext.NewDNSContext(context.Background()), request)
		if err != nil || calls != 1 || len(reply.Answer) != 0 {
			t.Fatal("changed unmanaged behavior", kind, reply, err)
		}
		_ = lfguard.Configure(map[string]any{"prefixes": []string{"10.0.0.0/8"}})
	}
}

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
