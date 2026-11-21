package lfguard

import (
	"context"
	"fmt"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/metacubex/mihomo/common/utils"
	C "github.com/metacubex/mihomo/constant"
	"golang.org/x/exp/slices"
	"golang.org/x/time/rate"
)

const delayConcurrency = 10
const interactiveConcurrency = 64
const MaxInteractiveDelay = 10 * time.Second

var (
	delaySlots       = make(chan struct{}, delayConcurrency)
	healthDelaySlots = make(chan struct{}, delayConcurrency)
	delayRate        = rate.NewLimiter(10, 24)
	interactiveSlots = make(chan struct{}, interactiveConcurrency)
	interactiveRate  = rate.NewLimiter(64, 128)
)

type healthDelayKey struct{}
type interactiveDelayKey struct{}
type backgroundTimeoutKey struct{}

func WithHealthDelay(ctx context.Context) context.Context {
	return context.WithValue(ctx, healthDelayKey{}, true)
}

func WithInteractiveDelay(ctx context.Context) context.Context {
	return context.WithValue(ctx, interactiveDelayKey{}, true)
}

// The provider's per-node deadline applies to the probe after scheduling.
func BackgroundDelay(ctx context.Context, timeout time.Duration) (context.Context, context.CancelFunc) {
	if !ManagedDelay() {
		return context.WithTimeout(ctx, timeout)
	}
	ctx = context.WithValue(ctx, backgroundTimeoutKey{}, timeout)
	return context.WithTimeout(ctx, 30*time.Second+timeout)
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

func DelayTarget() string {
	if p := current.Load(); p != nil {
		return p.DelayTestURL
	}
	return ""
}

func DelayAllowed(target string, health bool) bool {
	p := current.Load()
	return p == nil || p.DelayTestURL == "" || target == p.DelayTestURL ||
		(health && slices.Contains(p.HealthTestURLs, target))
}

// Cover every actual dial, including background and on-failure provider tests.
// The desktop burst is independent of background checks and root health.
func BeginDelay(ctx context.Context, target string) (context.Context, func(), error) {
	if !ManagedDelay() {
		return ctx, func() {}, nil
	}
	health, _ := ctx.Value(healthDelayKey{}).(bool)
	if !DelayAllowed(target, health) {
		return ctx, func() {}, fmt.Errorf("managed delay test endpoint is not authorized")
	}
	interactive, _ := ctx.Value(interactiveDelayKey{}).(bool)
	slots, limiter := delaySlots, delayRate
	if health {
		slots, limiter = healthDelaySlots, nil
	} else if interactive {
		slots, limiter = interactiveSlots, interactiveRate
	}
	select {
	case slots <- struct{}{}:
	case <-ctx.Done():
		return ctx, func() {}, ctx.Err()
	}
	if limiter != nil {
		if err := limiter.Wait(ctx); err != nil {
			<-slots
			return ctx, func() {}, err
		}
	}
	if err := ctx.Err(); err != nil {
		<-slots
		return ctx, func() {}, err
	}
	timeout, _ := ctx.Value(backgroundTimeoutKey{}).(time.Duration)
	if timeout <= 0 || timeout > MaxInteractiveDelay {
		timeout = MaxInteractiveDelay
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	return ctx, func() { cancel(); <-slots }, nil
}

// TestDelayBatch bounds the workers for both controller batch endpoints.
func TestDelayBatch(ctx context.Context, proxies []C.Proxy, target string, expected utils.IntRanges[uint16]) map[string]uint16 {
	limit := delayConcurrency
	if interactive, _ := ctx.Value(interactiveDelayKey{}).(bool); interactive {
		limit = interactiveConcurrency
	}
	slots := make(chan struct{}, limit)
	delays := make(map[string]uint16)
	var workers sync.WaitGroup
	var mutex sync.Mutex
probe:
	for _, proxy := range proxies {
		if ManagedDelay() && (proxy.Type() == C.Direct || proxy.Type() == C.Compatible) {
			continue
		}
		select {
		case slots <- struct{}{}:
		case <-ctx.Done():
			break probe
		}
		proxy := proxy
		workers.Add(1)
		go func() {
			defer workers.Done()
			defer func() { <-slots }()
			if delay, err := proxy.URLTest(ctx, target, expected); err == nil {
				mutex.Lock()
				delays[proxy.Name()] = delay
				mutex.Unlock()
			}
		}()
	}
	workers.Wait()
	return delays
}
