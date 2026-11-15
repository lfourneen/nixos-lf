package lfguard

import (
	"context"
	"errors"
	"net"
	"net/netip"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	C "github.com/metacubex/mihomo/constant"
	D "github.com/miekg/dns"
)

type validationWaitContext struct {
	context.Context
	ready chan struct{}
	once  sync.Once
}

func (c *validationWaitContext) Done() <-chan struct{} {
	c.once.Do(func() { close(c.ready) })
	return c.Context.Done()
}

func validationFixture(t *testing.T, handler D.HandlerFunc) string {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	started := make(chan struct{})
	server := &D.Server{Listener: listener, Net: "tcp", Handler: handler,
		NotifyStartedFunc: func() { close(started) }}
	go func() { _ = server.ActivateAndServe() }()
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("validator startup")
	}
	t.Cleanup(func() { _ = server.Shutdown() })
	return listener.Addr().String()
}

func validationResponse(request *D.Msg, address string, ttl uint32) *D.Msg {
	reply := new(D.Msg).SetReply(request)
	if request.Question[0].Qtype == D.TypeA {
		reply.Answer = []D.RR{&D.A{Hdr: D.RR_Header{Name: request.Question[0].Name,
			Rrtype: D.TypeA, Class: D.ClassINET, Ttl: ttl}, A: net.ParseIP(address)}}
	}
	return reply
}

func TestValidatedReplyRespectsTTLAndQuestionType(t *testing.T) {
	now := time.Now()
	for _, test := range []struct {
		name    string
		records []string
		ttl     time.Duration
		count   int
	}{
		{"cap", []string{"example. 60 IN A 203.0.113.1"}, 10 * time.Second, 1},
		{"short", []string{"example. 1 IN A 203.0.113.1"}, time.Second, 1},
		{"zero", []string{"example. 0 IN A 203.0.113.1"}, 0, 1},
		{"alias", []string{"example. 1 IN CNAME target.example.", "target.example. 60 IN A 203.0.113.1"}, time.Second, 1},
		{"wrong-family", []string{"example. 60 IN AAAA 2001:db8::1"}, 10 * time.Second, 0},
	} {
		t.Run(test.name, func(t *testing.T) {
			reply := new(D.Msg)
			for _, record := range test.records {
				rr, err := D.NewRR(record)
				if err != nil {
					t.Fatal(err)
				}
				reply.Answer = append(reply.Answer, rr)
			}
			answer := validatedReply(reply, D.TypeA, now)
			if answer.expiry.Sub(now) != test.ttl || len(answer.ips) != test.count {
				t.Fatal(answer)
			}
		})
	}
}

func TestValidatedCacheExpiryCopiesAndZeroTTL(t *testing.T) {
	var queries atomic.Int32
	var addressQueries atomic.Int32
	var ttl atomic.Uint32
	ttl.Store(1)
	endpoint := validationFixture(t, func(w D.ResponseWriter, request *D.Msg) {
		queries.Add(1)
		if request.Question[0].Qtype == D.TypeA {
			addressQueries.Add(1)
		}
		_ = w.WriteMsg(validationResponse(request, "203.0.113.1", ttl.Load()))
	})
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8"}, "local-dnssec": endpoint})
	ips, err := validatedIPs(context.Background(), "Example.")
	if err != nil {
		t.Fatal(err)
	}
	ips[0] = netip.MustParseAddr("127.0.0.1")
	ips, err = validatedIPs(context.Background(), "example")
	if err != nil || len(ips) != 1 || ips[0].String() != "203.0.113.1" || queries.Load() != 2 {
		t.Fatal("mutable or unshared cached answer", ips, err, queries.Load())
	}
	time.Sleep(1100 * time.Millisecond)
	if _, err = validatedIPs(context.Background(), "example"); err != nil || queries.Load() != 4 {
		t.Fatal("DNS TTL extended", err, queries.Load())
	}
	ttl.Store(0)
	for attempt := 0; attempt < 2; attempt++ {
		if ips, err = validatedIPs(context.Background(), "zero.example"); err != nil || len(ips) != 1 {
			t.Fatal("zero-TTL answer unavailable for its own query", ips, err)
		}
	}
	if addressQueries.Load() != 4 {
		t.Fatal("zero-TTL answer cached", addressQueries.Load())
	}
}

func TestValidatedCancelledCallerDoesNotCancelOtherWaiters(t *testing.T) {
	started, release := make(chan struct{}, 2), make(chan struct{})
	defer close(release)
	endpoint := validationFixture(t, func(w D.ResponseWriter, request *D.Msg) {
		started <- struct{}{}
		select {
		case <-release:
		case <-time.After(2 * time.Second):
		}
		_ = w.WriteMsg(validationResponse(request, "203.0.113.1", 0))
	})
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8"}, "local-dnssec": endpoint})
	ctx, cancel := context.WithCancel(context.Background())
	first := make(chan error, 1)
	go func() { _, err := validatedIPs(ctx, "shared.example"); first <- err }()
	<-started
	<-started
	cancel()
	if err := <-first; !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
	second := make(chan error, 1)
	secondContext := &validationWaitContext{Context: context.Background(), ready: make(chan struct{})}
	go func() {
		ips, err := validatedIPs(secondContext, "shared.example")
		if err == nil && len(ips) != 1 {
			err = errors.New("shared zero-TTL answer lost")
		}
		second <- err
	}()
	select {
	case <-secondContext.ready:
	case <-time.After(time.Second):
		t.Fatal("waiter did not join")
	}
	// Release both replies only after the second caller has joined the flight.
	release <- struct{}{}
	release <- struct{}{}
	if err := <-second; err != nil {
		t.Fatal("other caller cancelled shared work", err)
	}
}

func TestValidatedPolicySwapCannotReuseInflightAnswers(t *testing.T) {
	started, release := make(chan struct{}, 2), make(chan struct{})
	defer close(release)
	oldEndpoint := validationFixture(t, func(w D.ResponseWriter, request *D.Msg) {
		started <- struct{}{}
		select {
		case <-release:
		case <-time.After(2 * time.Second):
		}
		_ = w.WriteMsg(validationResponse(request, "203.0.113.1", 60))
	})
	newEndpoint := validationFixture(t, func(w D.ResponseWriter, request *D.Msg) {
		_ = w.WriteMsg(validationResponse(request, "203.0.113.2", 60))
	})
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8"}, "local-dnssec": oldEndpoint})
	old := make(chan error, 1)
	go func() { _, err := validatedIPs(context.Background(), "swap.example"); old <- err }()
	<-started
	<-started
	if err := Configure(map[string]any{"prefixes": []string{"10.0.0.0/8"}, "local-dnssec": newEndpoint}); err != nil {
		t.Fatal(err)
	}
	if err := <-old; err == nil {
		t.Fatal("old policy query survived replacement")
	}
	for attempt := 0; attempt < 2; attempt++ {
		ips, err := validatedIPs(context.Background(), "swap.example")
		if err != nil || len(ips) != 1 || ips[0].String() != "203.0.113.2" {
			t.Fatal(ips, err)
		}
	}
}

func TestValidatedLookupRejectsFailuresWithoutRetryOrFallback(t *testing.T) {
	var queries atomic.Int32
	endpoint := validationFixture(t, func(w D.ResponseWriter, request *D.Msg) {
		queries.Add(1)
		_ = w.WriteMsg(new(D.Msg).SetRcode(request, D.RcodeServerFailure))
	})
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8"}, "local-dnssec": endpoint})
	for attempt := 0; attempt < 2; attempt++ {
		metadata := &C.Metadata{Type: C.HTTP, NetWork: C.TCP, Host: "bogus.example", DstPort: 443}
		_, err := Protect(context.Background(), metadata, metadata,
			func(context.Context, string) ([]netip.Addr, error) { t.Error("unvalidated fallback"); return nil, nil })
		if err == nil {
			t.Fatal("refused answers accepted")
		}
	}
	if queries.Load() != 4 {
		t.Fatal("failure cached or DNSSEC lookup retried", queries.Load())
	}
}

func TestValidatedSlowFamilyAndInflightLimit(t *testing.T) {
	endpoint := validationFixture(t, func(w D.ResponseWriter, request *D.Msg) {
		if request.Question[0].Qtype == D.TypeAAAA {
			time.Sleep(600 * time.Millisecond)
		}
		_ = w.WriteMsg(validationResponse(request, "203.0.113.1", 60))
	})
	ConfigureT(t, map[string]any{"prefixes": []string{"10.0.0.0/8"}, "local-dnssec": endpoint})
	started := time.Now()
	if ips, err := validatedIPs(context.Background(), "slow.example"); err != nil || len(ips) != 1 {
		t.Fatal(ips, err)
	}
	if time.Since(started) >= 500*time.Millisecond {
		t.Fatal("slow family delayed valid addresses")
	}
	c := current.Load().validated
	c.Lock()
	for index := 0; index < validatedAnswerLimit; index++ {
		c.inflight[netip.AddrFrom4([4]byte{192, 0, 2, byte(index)}).String()] = &validatedFlight{cancel: func() {}}
	}
	c.Unlock()
	if _, err := validatedIPs(context.Background(), "overload.example"); err == nil {
		t.Fatal("unbounded lookup work accepted")
	}
}
