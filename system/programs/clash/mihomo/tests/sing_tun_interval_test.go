//go:build linux

package tun

import (
	"encoding/binary"
	"errors"
	"net/netip"
	"reflect"
	"testing"

	"github.com/mdlayher/netlink"
	"github.com/metacubex/nftables"
	"golang.org/x/sys/unix"
)

// Exercise the actual upstream builder and decode its outgoing netlink data.
// A maximum-address range must have only a start element, while an ordinary
// range still needs a successor end marker. No kernel privileges are needed.
func TestLFManagedIntervalEndpoints(t *testing.T) {
	for _, tc := range []struct {
		name   string
		family nftables.TableFamily
		prefix string
		want   map[string]bool // address -> interval end
	}{
		{"ipv4", nftables.TableFamilyIPv4, "192.0.2.0/24", map[string]bool{"192.0.2.0": false, "192.0.3.0": true}},
		{"ipv4-max-range", nftables.TableFamilyIPv4, "255.255.255.254/31", map[string]bool{"255.255.255.254": false}},
		{"ipv4-max-host", nftables.TableFamilyIPv4, "255.255.255.255/32", map[string]bool{"255.255.255.255": false}},
		{"ipv6", nftables.TableFamilyIPv6, "2001:db8::/126", map[string]bool{"2001:db8::": false, "2001:db8::4": true}},
		{"ipv6-max-range", nftables.TableFamilyIPv6, "ffff:ffff:ffff:ffff:ffff:ffff:ffff:fffe/127", map[string]bool{"ffff:ffff:ffff:ffff:ffff:ffff:ffff:fffe": false}},
		{"ipv6-max-host", nftables.TableFamilyIPv6, "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff/128", map[string]bool{"ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff": false}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := make(map[string]bool)
			stopped := errors.New("captured netlink transaction")
			nft := &nftables.Conn{TestDial: func(messages []netlink.Message) ([]netlink.Message, error) {
				for _, message := range messages {
					if message.Header.Type != netlink.HeaderType(unix.NFNL_SUBSYS_NFTABLES<<8|unix.NFT_MSG_NEWSETELEM) {
						continue
					}
					for _, list := range lfIntervalAttrs(t, message.Data[4:]) {
						if list.Type&^uint16(netlink.Nested|netlink.NetByteOrder) != unix.NFTA_SET_ELEM_LIST_ELEMENTS {
							continue
						}
						for _, element := range lfIntervalAttrs(t, list.Data) {
							var address netip.Addr
							var end bool
							for _, field := range lfIntervalAttrs(t, element.Data) {
								switch field.Type & ^uint16(netlink.Nested|netlink.NetByteOrder) {
								case unix.NFTA_SET_ELEM_KEY:
									for _, value := range lfIntervalAttrs(t, field.Data) {
										if value.Type&^uint16(netlink.Nested|netlink.NetByteOrder) == unix.NFTA_DATA_VALUE {
											address, _ = netip.AddrFromSlice(value.Data)
										}
									}
								case unix.NFTA_SET_ELEM_FLAGS:
									end = binary.BigEndian.Uint32(field.Data)&unix.NFT_SET_ELEM_INTERVAL_END != 0
								}
							}
							if !address.IsValid() {
								t.Fatal("missing interval address")
							}
							if _, exists := got[address.String()]; exists {
								t.Fatalf("duplicate interval key %s", address)
							}
							got[address.String()] = end
						}
					}
				}
				return nil, stopped
			}}
			table := &nftables.Table{Name: "lf_interval_test", Family: nftables.TableFamilyINet}
			_, err := nftablesCreateIPSet(nft, table, 0, "", tc.family, nil,
				[]netip.Prefix{netip.MustParsePrefix(tc.prefix)}, false, false)
			if err != nil {
				t.Fatal(err)
			}
			if err := nft.Flush(); !errors.Is(err, stopped) {
				t.Fatalf("capture transaction: %v", err)
			}
			if !reflect.DeepEqual(got, tc.want) {
				t.Fatalf("interval elements: got %v, want %v", got, tc.want)
			}
		})
	}
}

func lfIntervalAttrs(t *testing.T, data []byte) []netlink.Attribute {
	t.Helper()
	attrs, err := netlink.UnmarshalAttributes(data)
	if err != nil {
		t.Fatal(err)
	}
	return attrs
}
