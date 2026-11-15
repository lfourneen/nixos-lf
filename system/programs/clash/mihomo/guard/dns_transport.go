package dns

import (
	"context"
	"fmt"
	"io"
	"net"
	"strconv"
	"sync"
	"time"

	C "github.com/metacubex/mihomo/constant"
	D "github.com/miekg/dns"
)

// Only the local validator may use these listeners. This service transports
// raw DNSSEC material; it never uses hosts, fake-IP or the validating frontend.
type lfTransportService struct{ resolver *Resolver }

func (s lfTransportService) ServeMsg(ctx context.Context, request *D.Msg) (*D.Msg, error) {
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
	// Unbound owns freshness and validation. Reusing a Mihomo cache entry
	// fetched without DO, or its stale-cache policy, would change that contract.
	return s.resolver.exchangeWithoutCache(ctx, query)
}

var lfTransportServers struct {
	sync.Mutex
	servers   []*D.Server
	listeners []io.Closer
}

func ReCreateManagedDNSTransports(application, bootstrap string, lc C.InboundListenConfig, resolvers Resolvers) error {
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
		address  string
		resolver *Resolver
	}{{application, resolvers.Resolver}, {bootstrap, resolvers.ProxyResolver}}
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
		service := &Server{service: lfTransportService{role.resolver}}
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
