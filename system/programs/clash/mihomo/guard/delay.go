package lfguard

import (
	"context"
	"fmt"
	"net/url"
	"strconv"
	"strings"
	"time"

	"golang.org/x/exp/slices"
	"golang.org/x/time/rate"
)

const delayConcurrency = 10

var (
	delaySlots       = make(chan struct{}, delayConcurrency)
	healthDelaySlots = make(chan struct{}, delayConcurrency)
	delayRate        = rate.NewLimiter(10, 24)
)

type healthDelayKey struct{}

func WithHealthDelay(ctx context.Context) context.Context {
	return context.WithValue(ctx, healthDelayKey{}, true)
}

func validateDelayPolicy(p *policy) error {
	if p.DelayTestURL != "" && !validDelayURL(p.DelayTestURL, true) {
		return fmt.Errorf("managed delay test requires an exact HTTPS URL")
	}
	for _, target := range p.HealthTestURLs {
		if !validDelayURL(target, false) {
			return fmt.Errorf("invalid managed health-test URL")
		}
	}
	return nil
}

func validDelayURL(raw string, httpsOnly bool) bool {
	u, err := url.Parse(raw)
	if err != nil || strings.ContainsAny(raw, "\\# \t\r\n") || u.User != nil || u.Hostname() == "" ||
		(u.Scheme != "https" && (httpsOnly || u.Scheme != "http")) {
		return false
	}
	if u.Port() == "" {
		return !strings.HasSuffix(u.Host, ":")
	}
	port, err := strconv.ParseUint(u.Port(), 10, 16)
	return err == nil && port > 0
}

func ManagedDelay() bool {
	p := current.Load()
	return p != nil && p.DelayTestURL != ""
}

func DelayAllowed(target string, health bool) bool {
	p := current.Load()
	return p == nil || p.DelayTestURL == "" || target == p.DelayTestURL ||
		(health && slices.Contains(p.HealthTestURLs, target))
}

// Cover every actual dial, including background and on-failure provider tests.
// Bound ordinary calls across profiles and leave separate slots for root health.
func BeginDelay(ctx context.Context, target string) (context.Context, func(), error) {
	if !ManagedDelay() {
		return ctx, func() {}, nil
	}
	health, _ := ctx.Value(healthDelayKey{}).(bool)
	if !DelayAllowed(target, health) {
		return ctx, func() {}, fmt.Errorf("managed delay test endpoint is not authorized")
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	slots := delaySlots
	if health {
		slots = healthDelaySlots
	}
	select {
	case slots <- struct{}{}:
	case <-ctx.Done():
		cancel()
		return ctx, func() {}, ctx.Err()
	}
	release := func() {
		<-slots
		cancel()
	}
	if !health {
		if err := delayRate.Wait(ctx); err != nil {
			release()
			return ctx, func() {}, err
		}
	}
	if err := ctx.Err(); err != nil {
		release()
		return ctx, func() {}, err
	}
	return ctx, release, nil
}
