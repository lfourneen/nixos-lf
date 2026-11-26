package lfguard

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/netip"
	"strconv"
	"time"

	C "github.com/metacubex/mihomo/constant"
)

// raceTCP dials at most two candidates at once. The caller is given only the
// winning stream, and a late success is closed without sending application data.
func raceTCP[T io.Closer](ctx context.Context, candidates []netip.Addr, dial func(context.Context, netip.Addr) (T, error)) (T, netip.Addr, error) {
	ctx, cancel := context.WithTimeout(ctx, 6*time.Second)
	defer cancel()
	type result struct {
		conn T
		ip   netip.Addr
		err  error
	}
	results := make(chan result)
	timer := time.NewTimer(250 * time.Millisecond)
	defer timer.Stop()
	started, running := 0, 0
	start := func() {
		ip := candidates[started]
		started++
		running++
		go func() {
			attempt, stop := context.WithTimeout(ctx, 2*time.Second)
			defer stop()
			conn, err := dial(attempt, ip)
			var zero T
			if err != nil && any(conn) != any(zero) {
				_ = conn.Close()
			}
			select {
			case results <- result{conn, ip, err}:
			case <-ctx.Done():
				if err == nil {
					_ = conn.Close()
				}
			}
		}()
	}
	start()
	var last error
	for running > 0 {
		select {
		case <-ctx.Done():
			var zero T
			return zero, netip.Addr{}, ctx.Err()
		case <-timer.C:
			if started < len(candidates) && running < 2 {
				start()
			}
		case answer := <-results:
			running--
			if answer.err == nil {
				if err := ctx.Err(); err != nil {
					_ = answer.conn.Close()
					var zero T
					return zero, netip.Addr{}, err
				}
				return answer.conn, answer.ip, nil
			}
			last = answer.err
			if started < len(candidates) {
				start()
				timer.Reset(250 * time.Millisecond)
			}
		}
	}
	var zero T
	return zero, netip.Addr{}, last
}

type nodeDialer struct {
	C.Dialer
	primary string
	port    string
	ips     []netip.Addr
}

// ParseNodeAlternates accepts only the worker's bounded literal address set.
// Old snapshots and adapters without a TCP endpoint retain their existing dialer.
func ParseNodeAlternates(mapping map[string]any) ([]netip.Addr, error) {
	raw, present := mapping["lf-node-alternates"]
	if !present {
		return nil, nil
	}
	p := current.Load()
	values, valid := raw.([]any)
	if p == nil || !p.RequireProtectedTransport || p.SingleEgress && p.WireGuardRelay != nil ||
		!valid || len(values) == 0 || len(values) > 2 || mapping["network"] == "kcp" || mapping["network"] == "h3" {
		return nil, errors.New("invalid managed node alternate addresses")
	}
	switch mapping["type"] {
	case "ss", "vmess", "vless", "trojan", "http", "socks5", "anytls":
	default:
		return nil, errors.New("managed node alternate addresses require TCP transport")
	}
	values = append([]any{mapping["server"]}, values...)
	ips := make([]netip.Addr, 0, len(values))
	for _, value := range values {
		text, _ := value.(string)
		ip, err := netip.ParseAddr(text)
		if err != nil || ip.Is4In6() || p.blocked(ip) {
			return nil, errors.New("invalid managed node endpoint")
		}
		if _, translated := p.translated(ip); translated {
			return nil, errors.New("translated managed node endpoint")
		}
		for _, previous := range ips {
			if ip == previous {
				return nil, errors.New("duplicate managed node endpoint")
			}
		}
		ips = append(ips, ip)
	}
	return ips, nil
}

func WithNodeAlternates(dialer C.Dialer, ips []netip.Addr, port int, preference C.DNSPrefer) C.Dialer {
	if len(ips) < 2 {
		return dialer
	}
	eligible := make([]netip.Addr, 0, len(ips))
	for _, ip := range ips {
		if (preference != C.IPv4Only || ip.Is4()) && (preference != C.IPv6Only || ip.Is6()) {
			eligible = append(eligible, ip)
		}
	}
	if preference == C.IPv6Prefer && len(eligible) > 1 && eligible[0].Is4() {
		for i, ip := range eligible {
			if ip.Is6() {
				eligible[0], eligible[i] = ip, eligible[0]
				break
			}
		}
	}
	service := strconv.Itoa(port)
	return nodeDialer{Dialer: dialer, primary: net.JoinHostPort(ips[0].String(), service), port: service, ips: eligible}
}

func (d nodeDialer) DialContext(ctx context.Context, network, address string) (net.Conn, error) {
	if address != d.primary || network != "tcp" {
		return d.Dialer.DialContext(ctx, network, address)
	}
	switch len(d.ips) {
	case 0:
		return nil, errors.New("no pinned node address for this address family")
	case 1:
		return d.Dialer.DialContext(ctx, network, net.JoinHostPort(d.ips[0].String(), d.port))
	}
	conn, _, err := raceTCP(ctx, d.ips, func(attempt context.Context, ip netip.Addr) (net.Conn, error) {
		return d.Dialer.DialContext(attempt, network, net.JoinHostPort(ip.String(), d.port))
	})
	if err != nil {
		return nil, fmt.Errorf("pinned node endpoints unavailable: %w", err)
	}
	return conn, nil
}
