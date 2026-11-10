package adapter

import (
	"context"
	"errors"
	"net/netip"
	"testing"

	"github.com/metacubex/mihomo/component/lfguard"
	C "github.com/metacubex/mihomo/constant"
)

type lfTargetLeaf struct {
	C.ProxyAdapter
	name   string
	kind   C.AdapterType
	calls  int
	target *C.Metadata
}

func (p *lfTargetLeaf) Name() string        { return p.name }
func (p *lfTargetLeaf) Type() C.AdapterType { return p.kind }
func (p *lfTargetLeaf) DialContext(_ context.Context, m *C.Metadata) (C.Conn, error) {
	p.calls++
	p.target = m.Clone()
	return nil, errors.New("fixture dial")
}
func (p *lfTargetLeaf) ListenPacketContext(_ context.Context, m *C.Metadata) (C.PacketConn, error) {
	p.calls++
	p.target = m.Clone()
	return nil, errors.New("fixture dial")
}

type lfTargetGroup struct {
	C.ProxyAdapter
	selected C.Proxy
}

func (p *lfTargetGroup) Type() C.AdapterType { return C.Selector }
func (p *lfTargetGroup) DialContext(ctx context.Context, m *C.Metadata) (C.Conn, error) {
	return p.selected.DialContext(ctx, m)
}
func (p *lfTargetGroup) ListenPacketContext(ctx context.Context, m *C.Metadata) (C.PacketConn, error) {
	return p.selected.ListenPacketContext(ctx, m)
}

func TestLFManagedTargetChecksActualLeaf(t *testing.T) {
	if err := lfguard.Configure(map[string]any{"prefixes": []string{"10.0.0.0/8"},
		"local-target-cidrs": []string{"127.0.0.0/8"}}); err != nil {
		t.Fatal(err)
	}
	defer lfguard.Configure(nil)
	direct := &lfTargetLeaf{name: "renamed", kind: C.Direct}
	remote := &lfTargetLeaf{name: "DIRECT", kind: C.Socks5} // names never confer direct authority
	choice := &lfTargetGroup{}
	nested := NewProxy(&lfTargetGroup{selected: NewProxy(choice)})
	for _, network := range []C.NetWork{C.TCP, C.UDP} {
		m := &C.Metadata{DstIP: netip.MustParseAddr("127.0.0.1"), DstPort: 80, Type: C.SOCKS5, NetWork: network}
		dial := func() {
			if network == C.TCP {
				nested.DialContext(context.Background(), m)
			} else {
				nested.ListenPacketContext(context.Background(), m)
			}
		}
		choice.selected = NewProxy(remote)
		dial()
		if remote.calls != 0 {
			t.Fatal("nested remote leaf received internal target")
		}
		before := direct.calls
		choice.selected = NewProxy(direct)
		dial()
		if direct.calls != before+1 || direct.target.Host != "" || direct.target.DstIP != m.DstIP {
			t.Fatal("local leaf lost compatibility/pinning")
		}
		choice.selected = NewProxy(remote)
		dial()
		if remote.calls != 0 {
			t.Fatal("changed group inherited direct authority")
		}
		m.DstIP = netip.MustParseAddr("203.0.113.10")
		dial()
		if remote.calls == 0 {
			t.Fatal("public literal was blocked")
		}
		remote.calls = 0
	}
}
