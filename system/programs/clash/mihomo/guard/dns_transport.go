package dns

import (
	"context"
	"fmt"
	"io"
	"net"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	C "github.com/metacubex/mihomo/constant"
	D "github.com/miekg/dns"
)

// Only the local validator may use these listeners. This service transports
// raw DNSSEC material; it never uses hosts, fake-IP or the validating frontend.
//
// Validation material (DS, DNSKEY, NSEC/NSEC3 and the RFC 5011 key-tag probe)
// is needed before Unbound can validate any name. The policy resolver may route
// those names through the selected proxy DNS leaf, so a dead leaf would also
// break domestic DIRECT browsing. Keep the current leaf as the preferred path,
// but fall back to a root-owned DIRECT validating resolver when it cannot answer
// promptly, and skip the leaf while that resolver is known to be degraded.
type lfTransportService struct {
	resolver    *Resolver
	validation  *Resolver
	alternate   *Resolver
	directUntil atomic.Int64
}

const (
	lfValidationRemoteBudget = time.Second
	lfValidationDirectHold   = 30 * time.Second
)

func lfValidationMaterial(question D.Question) bool {
	switch question.Qtype {
	case D.TypeDS, D.TypeDNSKEY, D.TypeRRSIG, D.TypeNSEC, D.TypeNSEC3,
		D.TypeNSEC3PARAM, D.TypeCDS, D.TypeCDNSKEY, D.TypeTA, D.TypeDLV:
		return true
	case D.TypeNULL:
		return strings.HasPrefix(strings.ToLower(question.Name), "_ta-")
	}
	return false
}

func lfUsableValidationReply(reply *D.Msg) bool {
	return reply != nil && !reply.Truncated &&
		reply.Rcode != D.RcodeServerFailure && reply.Rcode != D.RcodeRefused
}

func (s *lfTransportService) ServeMsg(ctx context.Context, request *D.Msg) (*D.Msg, error) {
	if len(request.Question) != 1 {
		return new(D.Msg).SetRcode(request, D.RcodeFormatError), nil
	}
	query := request.Copy()
	query.CheckingDisabled = true
	if query.IsEdns0() == nil {
		query.SetEdns0(1232, true)
	} else {
		query.IsEdns0().SetDo()
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	// Unbound owns caching. Send raw requests with the caller's deadline
	// instead of joining a longer-lived resolver singleflight.
	exchange := func(ctx context.Context, clients []dnsClient) (*D.Msg, error) {
		reply, _, err := batchExchange(ctx, clients, query)
		return reply, err
	}
	matched := s.resolver.matchPolicy(query)
	clients := matched
	if len(clients) == 0 {
		clients = s.resolver.main
	}
	if s.validation != nil && lfValidationMaterial(request.Question[0]) {
		if time.Now().UnixNano() >= s.directUntil.Load() {
			attempt, stop := context.WithTimeout(ctx, lfValidationRemoteBudget)
			reply, err := exchange(attempt, clients)
			stop()
			if err == nil && lfUsableValidationReply(reply) {
				s.directUntil.Store(0)
				return reply, nil
			}
			s.directUntil.Store(time.Now().Add(lfValidationDirectHold).UnixNano())
		}
		return exchange(ctx, s.validation.main)
	}
	// Explicit domain policies retain their resolver, including geosite:cn.
	if s.alternate != nil && len(matched) == 0 {
		attempt, stop := context.WithTimeout(ctx, 1500*time.Millisecond)
		reply, err := exchange(attempt, clients)
		stop()
		if err == nil && lfUsableValidationReply(reply) {
			return reply, nil
		}
		return exchange(ctx, s.alternate.main)
	}
	// Unbound owns freshness and validation. Reusing a Mihomo cache entry
	// fetched without DO, or its stale-cache policy, would change that contract.
	return exchange(ctx, clients)
}

var lfTransportServers struct {
	sync.Mutex
	servers   []*D.Server
	listeners []io.Closer
}

func ReCreateManagedDNSTransports(application, bootstrap string, validation, alternate *Resolver,
	lc C.InboundListenConfig, resolvers Resolvers) error {
	lfTransportServers.Lock()
	defer lfTransportServers.Unlock()
	closeAll := func() {
		for _, server := range lfTransportServers.servers {
			_ = server.Shutdown()
		}
		for _, listener := range lfTransportServers.listeners {
			_ = listener.Close()
		}
		lfTransportServers.servers = nil
		lfTransportServers.listeners = nil
	}
	closeAll()
	if application == "" && bootstrap == "" {
		return nil
	}
	roles := []struct {
		address    string
		resolver   *Resolver
		validation *Resolver
		alternate  *Resolver
	}{{application, resolvers.Resolver, validation, alternate}, {bootstrap, resolvers.ProxyResolver, nil, nil}}
	for _, role := range roles {
		host, port, err := net.SplitHostPort(role.address)
		number, portErr := strconv.Atoi(port)
		if err != nil || portErr != nil || host != "127.0.0.1" || number < 1024 || number > 65535 || role.resolver == nil || lc == nil {
			closeAll()
			return fmt.Errorf("managed DNS transport requires private loopback high ports and resolvers")
		}
		listener, err := lc.Listen(context.Background(), "tcp", role.address)
		if err != nil {
			closeAll()
			return err
		}
		packet, err := lc.ListenPacket(context.Background(), "udp", role.address)
		if err != nil {
			_ = listener.Close()
			closeAll()
			return err
		}
		service := &Server{service: &lfTransportService{resolver: role.resolver, validation: role.validation, alternate: role.alternate}}
		lfTransportServers.listeners = append(lfTransportServers.listeners, listener, packet)
		servers := []*D.Server{
			{Listener: listener, Handler: service.TCPHandler()},
			{PacketConn: packet, Handler: service.UDPHandler()},
		}
		for _, server := range servers {
			started, failed := make(chan struct{}), make(chan error, 1)
			server.NotifyStartedFunc = func() { close(started) }
			lfTransportServers.servers = append(lfTransportServers.servers, server)
			go func() { failed <- server.ActivateAndServe() }()
			select {
			case <-started:
			case err := <-failed:
				closeAll()
				return fmt.Errorf("managed DNS transport start: %v", err)
			case <-time.After(2 * time.Second):
				closeAll()
				return fmt.Errorf("managed DNS transport start timed out")
			}
		}
	}
	return nil
}
