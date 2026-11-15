package inbound

import (
	"bytes"
	"testing"

	"github.com/metacubex/mihomo/transport/socks5"
)

// Framing must reject incomplete addresses before the metadata parser runs.
func TestLFManagedSocksMalformedFrames(t *testing.T) {
	for _, atyp := range []byte{socks5.AtypDomainName, socks5.AtypIPv4, socks5.AtypIPv6} {
		var addr socks5.Addr
		switch atyp {
		case socks5.AtypDomainName:
			addr = append(socks5.Addr{atyp, 255}, bytes.Repeat([]byte{'a'}, 255)...)
		case socks5.AtypIPv4:
			addr = socks5.Addr{atyp, 192, 0, 2, 1}
		case socks5.AtypIPv6:
			addr = append(socks5.Addr{atyp}, make([]byte, 16)...)
		}
		addr = append(addr, 0x01, 0xbb)
		for size := 0; size < len(addr); size++ {
			frame := addr[:size]
			if got := socks5.SplitAddr(frame); got != nil {
				t.Fatalf("type %d size %d: accepted truncated frame", atyp, size)
			}
			if _, err := socks5.ReadAddr(bytes.NewReader(frame), make([]byte, socks5.MaxAddrLen)); err == nil {
				t.Fatalf("type %d size %d: read truncated frame", atyp, size)
			}
		}
	}
	if got := socks5.SplitAddr([]byte{0xff, 0, 0, 0}); got != nil {
		t.Fatal("accepted unknown address type")
	}
	if _, err := socks5.ReadAddr(bytes.NewReader([]byte{0xff}), make([]byte, socks5.MaxAddrLen)); err == nil {
		t.Fatal("read unknown address type")
	}
}
