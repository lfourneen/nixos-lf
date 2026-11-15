package lfguard

import (
	"context"
	"fmt"
	"net/netip"
	"strings"
	"sync"
	"time"

	D "github.com/miekg/dns"
)

const (
	validatedAnswerTTL   = 10 * time.Second
	validatedAnswerLimit = 256
	validatedLookupTime  = 3 * time.Second
)

type validatedAnswer struct {
	ips    []netip.Addr
	expiry time.Time
}

type validatedFlight struct {
	done   chan struct{}
	cancel context.CancelFunc
	answer validatedAnswer
	err    error
}

// Each immutable policy owns its cache and in-flight work. An old validator's
// result cannot populate or satisfy a lookup under a replacement policy.
type validatedCache struct {
	sync.Mutex
	retired  bool
	answers  map[string]validatedAnswer
	inflight map[string]*validatedFlight
}

func newValidatedCache() *validatedCache {
	return &validatedCache{answers: map[string]validatedAnswer{}, inflight: map[string]*validatedFlight{}}
}

func installPolicy(p *policy) {
	old := current.Swap(p)
	if old == nil || old.validated == nil {
		return
	}
	c := old.validated
	c.Lock()
	c.retired = true
	c.answers = nil
	for _, flight := range c.inflight {
		flight.cancel()
	}
	c.Unlock()
}

func validatedIPs(ctx context.Context, host string) ([]netip.Addr, error) {
	return validatedIPsForPolicy(ctx, current.Load(), host)
}

func validatedIPsForPolicy(ctx context.Context, p *policy, host string) ([]netip.Addr, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if p == nil || p.LocalDNSSEC == "" || p.validated == nil {
		return nil, fmt.Errorf("local DNSSEC validator is unavailable")
	}
	key := strings.ToLower(strings.TrimSuffix(host, "."))
	c := p.validated
	c.Lock()
	if c.retired || current.Load() != p {
		c.Unlock()
		return nil, fmt.Errorf("local DNSSEC policy changed")
	}
	if answer, ok := c.answers[key]; ok {
		if time.Now().Before(answer.expiry) {
			ips := append([]netip.Addr(nil), answer.ips...)
			c.Unlock()
			return ips, nil
		}
		delete(c.answers, key)
	}
	flight, ok := c.inflight[key]
	if !ok {
		if len(c.inflight) >= validatedAnswerLimit {
			c.Unlock()
			return nil, fmt.Errorf("local DNSSEC lookups are busy")
		}
		// A short-lived caller must not cancel other consumers of this query.
		// Independent work is bounded even after all callers have gone away.
		lookupCtx, cancel := context.WithTimeout(context.Background(), validatedLookupTime)
		flight = &validatedFlight{done: make(chan struct{}), cancel: cancel}
		c.inflight[key] = flight
		go c.lookup(lookupCtx, p, key, flight)
	}
	c.Unlock()
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-flight.done:
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if current.Load() != p {
			return nil, fmt.Errorf("local DNSSEC policy changed")
		}
		return append([]netip.Addr(nil), flight.answer.ips...), flight.err
	}
}

func (c *validatedCache) lookup(ctx context.Context, p *policy, key string, flight *validatedFlight) {
	defer flight.cancel()
	answer, err := lookupValidated(ctx, p, key)
	c.Lock()
	defer c.Unlock()
	if c.retired || current.Load() != p {
		answer, err = validatedAnswer{}, fmt.Errorf("local DNSSEC policy changed")
	}
	flight.answer, flight.err = answer, err
	delete(c.inflight, key)
	if err == nil && len(answer.ips) > 0 && time.Now().Before(answer.expiry) {
		for name, entry := range c.answers {
			if !time.Now().Before(entry.expiry) {
				delete(c.answers, name)
			}
		}
		if len(c.answers) >= validatedAnswerLimit {
			for name := range c.answers {
				delete(c.answers, name)
				break
			}
		}
		c.answers[key] = answer
	}
	close(flight.done)
}

// Unbound is the validator. Its returned TTLs already reflect DNSSEC validity
// limits (RFC 4035, section 5.3.3). Reuse must not extend those TTLs, including
// a short-lived CNAME, or turn a zero-TTL answer into a reusable entry.
func validatedReply(reply *D.Msg, kind uint16, now time.Time) validatedAnswer {
	answer := validatedAnswer{expiry: now.Add(validatedAnswerTTL)}
	for _, record := range reply.Answer {
		ttl := record.Header().Ttl
		if ttl > 1<<31-1 {
			ttl = 0
		}
		if until := now.Add(time.Duration(ttl) * time.Second); until.Before(answer.expiry) {
			answer.expiry = until
		}
		switch value := record.(type) {
		case *D.A:
			if kind == D.TypeA {
				if ip, ok := netip.AddrFromSlice(value.A); ok {
					answer.ips = append(answer.ips, ip.Unmap())
				}
			}
		case *D.AAAA:
			if kind == D.TypeAAAA {
				if ip, ok := netip.AddrFromSlice(value.AAAA); ok {
					answer.ips = append(answer.ips, ip.Unmap())
				}
			}
		}
	}
	return answer
}

func lookupValidated(ctx context.Context, p *policy, host string) (validatedAnswer, error) {
	type outcome struct {
		answer validatedAnswer
		err    error
	}
	kinds := []uint16{D.TypeA, D.TypeAAAA}
	results := make(chan outcome, len(kinds))
	for _, kind := range kinds {
		go func(kind uint16) {
			request := new(D.Msg)
			request.SetQuestion(D.Fqdn(host), kind)
			reply, err := validateDNS(ctx, p, request)
			if err != nil || reply == nil || reply.Rcode != D.RcodeSuccess || reply.Truncated {
				results <- outcome{err: fmt.Errorf("local DNSSEC resolver refused target")}
				return
			}
			results <- outcome{answer: validatedReply(reply, kind, time.Now())}
		}(kind)
	}
	answer := validatedAnswer{expiry: time.Now().Add(validatedAnswerTTL)}
	var refused error
	var timer *time.Timer
	var settled <-chan time.Time
	defer func() {
		if timer != nil {
			timer.Stop()
		}
	}()
	for range kinds {
		select {
		case <-settled:
			return answer, nil
		case <-ctx.Done():
			if ctx.Err() != context.DeadlineExceeded || len(answer.ips) == 0 {
				return validatedAnswer{}, ctx.Err()
			}
			return answer, nil
		case result := <-results:
			if result.err != nil {
				refused = result.err
				continue
			}
			if len(result.answer.ips) > 0 {
				answer.ips = append(answer.ips, result.answer.ips...)
				if result.answer.expiry.Before(answer.expiry) {
					answer.expiry = result.answer.expiry
				}
				if timer == nil {
					// Collect a promptly available second family without letting
					// a stalled one consume the good answer's lifetime or all of
					// a node delay test's request budget.
					grace := 100 * time.Millisecond
					if remaining := time.Until(answer.expiry); remaining < grace {
						grace = remaining
					}
					timer = time.NewTimer(grace)
					settled = timer.C
				}
			}
		}
	}
	if len(answer.ips) > 0 {
		return answer, nil
	}
	if refused == nil {
		refused = fmt.Errorf("local DNSSEC resolver returned no address")
	}
	return validatedAnswer{}, refused
}
