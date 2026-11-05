package tunnel

import (
	"context"
	"fmt"
	"net"
	"net/netip"
	"testing"
	"time"

	"github.com/metacubex/mihomo/component/lfguard"
	"github.com/metacubex/mihomo/component/resolver"
	C "github.com/metacubex/mihomo/constant"
)

type lfUDPResolver struct {
	resolver.Resolver
	calls  int
	answer netip.Addr
}

func (r *lfUDPResolver) Invalid() bool { return true }
func (r *lfUDPResolver) LookupIP(context.Context, string) ([]netip.Addr, error) {
	r.calls++
	return []netip.Addr{r.answer}, nil
}
func (r *lfUDPResolver) LookupIPv4(ctx context.Context, host string) ([]netip.Addr, error) {
	return r.LookupIP(ctx, host)
}

type lfUDPPacket struct {
	C.PacketAdapter
	metadata *C.Metadata
}

func (p *lfUDPPacket) Metadata() *C.Metadata { return p.metadata }
func (p *lfUDPPacket) Data() []byte          { return []byte("packet") }
func (p *lfUDPPacket) Drop()                 {}

type lfUDPConn struct {
	C.PacketConn
	writes []string
}

func (c *lfUDPConn) WriteTo(data []byte, address net.Addr) (int, error) {
	c.writes = append(c.writes, address.String())
	return len(data), nil
}
func (c *lfUDPConn) SetReadDeadline(time.Time) error { return nil }
func (c *lfUDPConn) ResolveUDP(context.Context, *C.Metadata) error {
	return fmt.Errorf("unchecked UDP resolution")
}

func TestLFManagedUDPMappingPinsValidatedTargets(t *testing.T) {
	if err := lfguard.Configure(map[string]any{"prefixes": []string{"10.0.0.0/8"}}); err != nil {
		t.Fatal(err)
	}
	old := resolver.DefaultResolver
	r := &lfUDPResolver{answer: netip.MustParseAddr("203.0.113.99")}
	resolver.DefaultResolver = r
	t.Cleanup(func() { resolver.DefaultResolver = old; _ = lfguard.Configure(nil) })
	sender := newPacketSender().(*packetSender)
	defer sender.Close()
	pc := new(lfUDPConn)
	initial := &C.Metadata{Host: "initial.example", DstPort: 1234, NetWork: C.UDP, Type: C.SOCKS5}
	pinned := initial.Clone()
	pinned.Host = ""
	pinned.DstIP = netip.MustParseAddr("203.0.113.42")
	sender.AddMapping(initial, pinned)
	sender.processPacket(pc, &lfUDPPacket{metadata: initial})
	if r.calls != 0 || len(pc.writes) != 1 || pc.writes[0] != "203.0.113.42:1234" {
		t.Fatal("initial dial mapping was re-resolved", r.calls, pc.writes)
	}
	other := initial.Clone()
	other.Host = "other.example"
	sender.processPacket(pc, &lfUDPPacket{metadata: other})
	r.answer = netip.MustParseAddr("10.1.2.3")
	sender.processPacket(pc, &lfUDPPacket{metadata: other})
	if r.calls != 1 || len(pc.writes) != 3 || pc.writes[1] != "203.0.113.99:1234" || pc.writes[2] != pc.writes[1] {
		t.Fatal("UDP target not pinned per mapping", r.calls, pc.writes)
	}
	blocked := other.Clone()
	blocked.Host = "new-private.example"
	sender.processPacket(pc, &lfUDPPacket{metadata: blocked})
	if r.calls != 2 || len(pc.writes) != 3 {
		t.Fatal("new private UDP target was sent", r.calls, pc.writes)
	}
	preResolved := other.Clone()
	preResolved.Host = "mapped-private.example"
	preResolved.DstIP = r.answer
	preResolved.DNSMode = C.DNSMapping
	sender.processPacket(pc, &lfUDPPacket{metadata: preResolved})
	if r.calls != 2 || len(pc.writes) != 3 {
		t.Fatal("Pure metadata erased the domain guard", r.calls, pc.writes)
	}
}
