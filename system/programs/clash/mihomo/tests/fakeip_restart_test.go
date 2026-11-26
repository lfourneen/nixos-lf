package fakeip

import (
	"net/netip"
	"os"
	"testing"
)

func TestPersistentMappingAfterUncleanStop(t *testing.T) {
	for _, cidr := range []string{"198.18.0.1/16", "fd00:abcd::1/96"} {
		t.Run(cidr, func(t *testing.T) {
			options := Options{IPNet: netip.MustParsePrefix(cidr), Size: 10}
			pool, path, err := createCachefileStore(options)
			if err != nil {
				t.Fatal(err)
			}
			defer os.Remove(path)
			defer pool.store.(*cachefileStore).cache.Close()

			first := pool.Lookup("first.example")
			if address := pool.Lookup("key-offset-fake-ip"); address == first {
				t.Fatal("DNS lookup returned the allocation cursor")
			}
			// A replacement core sees only committed data, without StoreState.
			restarted, err := New(options)
			if err != nil {
				t.Fatal(err)
			}
			restarted.store = pool.store
			restarted.restoreState()
			if next := restarted.Lookup("second.example"); next == first {
				t.Fatalf("reassigned outstanding address %v", first)
			}
			if host, ok := restarted.LookBack(first); !ok || host != "first.example" {
				t.Fatalf("reverse mapping for %v is %q, present=%v", first, host, ok)
			}
			if got := restarted.Lookup("first.example"); got != first {
				t.Fatalf("address changed from %v to %v", first, got)
			}
		})
	}
}
