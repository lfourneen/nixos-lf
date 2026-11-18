def check_pinned(machine):
    try:
        machine.wait_until_succeeds(
            """network-arp status | jq -e '.ok and .interfaces.ens1.state == "pinned"'""", timeout=20)
    except Exception:
        print(machine.execute("network-arp status; ip -j addr show dev ens1; ip -4 route show table main; "
                              "nft list table arp lf_arp; journalctl -u proxy-mode --no-pager -n 30"))
        raise
    machine.succeed("""ip -j neigh show to 100.67.0.1 dev ens1 | jq -e '
      .[0] | .lladdr == "02:00:00:00:67:01" and (.state | index("PERMANENT")) != null'""")


def claim_drops(machine):
    return int(machine.succeed(
        "nft -j list chain arp lf_arp validate | jq '[.nftables[].rule.expr[]? | .counter.packets // 0] | add'"))


def check_arp_direct(machine, uid):
    with machine.nested("Direct access, scoped replies, and neighbour visibility"):
        check_pinned(machine)
        machine.succeed("test $(cat /proc/sys/net/ipv4/conf/ens1/arp_ignore) = 2")
        machine.succeed("ip netns exec outside arping -I peer0 -c 1 -w 2 100.67.0.2")
        machine.succeed("ip -n outside addr add 192.0.2.4/24 dev peer0")
        machine.fail("ip netns exec outside arping -I peer0 -s 192.0.2.4 -c 1 -w 2 100.67.0.2")
        machine.fail("ip netns exec outside arping -D -I peer0 -c 1 -w 2 100.67.0.2")
        machine.succeed("ip netns exec outside arping -D -I peer0 -c 1 -w 2 100.67.0.90")
        machine.succeed("ip link add virbr0 type veth peer name guest0; ip netns add arp-guest; "
                        "ip link set guest0 netns arp-guest; ip link set virbr0 up; "
                        "ip -n arp-guest link set guest0 up")
        machine.succeed("ip netns exec arp-guest arping -D -I guest0 -c 1 -w 2 100.67.0.2")
        machine.succeed(f"setpriv --reuid={uid} --clear-groups ip neigh show dev ens1 | grep -q 100.67.0.1")
        assert not machine.succeed(f"ip netns exec arp-guest setpriv --reuid={uid} --clear-groups ip neigh show").strip()
        machine.succeed(f"setpriv --reuid={uid} --clear-groups curl --noproxy '*' -fsS --max-time 3 "
                        "http://100.67.0.1:8080/ >/dev/null")
        machine.succeed("ping -6 -c 1 -W 2 2001:db8:dead::1")
    with machine.nested("Gateway MAC replacement requires configured trust"):
        previous = claim_drops(machine)
        machine.succeed("ip -n outside link set peer0 address 02:00:00:00:67:02")
        machine.fail("ip netns exec outside arping -I peer0 -c 1 -w 2 100.67.0.2")
        assert claim_drops(machine) > previous
        check_pinned(machine)
        machine.succeed("ip -n outside link set peer0 address 02:00:00:00:67:01")
        machine.succeed("ip netns exec outside arping -I peer0 -c 1 -w 2 100.67.0.2")


def check_arp_proxy(machine, uid, wait_proxy):
    with machine.nested("Changed next hop cannot retain a B lease"):
        machine.succeed("nft get element inet lf_filter b_authorization '{ ipv4 }'")
        machine.succeed("ip route replace default via 100.67.0.254 dev ens1 metric 1")
        machine.wait_until_succeeds(
            """jq -e '.mode == "blocked" and .reason == "arp-binding"' /run/proxy-mode/state.json""", timeout=25)
        machine.succeed("""network-arp status | jq -e '.interfaces.ens1.state == "gateway-mismatch"'""")
        machine.fail("ip neigh show to 100.67.0.1 dev ens1 | grep -q PERMANENT")
        machine.fail("nft get element inet lf_filter b_authorization '{ ipv4 }'")
        machine.fail(f"setpriv --reuid={uid} --clear-groups curl --noproxy '*' -fsS --max-time 2 "
                     "http://203.0.113.10:8080/")
        machine.succeed("ip route replace default via 100.67.0.1 dev ens1 metric 1")
        wait_proxy()
        check_pinned(machine)
    with machine.nested("Carrier loss discards the pin and recovery rebinds"):
        machine.succeed("ip -n outside link set peer0 down")
        machine.wait_until_succeeds(
            """network-arp status | jq -e '.interfaces.ens1.state == "disconnected"'""", timeout=20)
        machine.fail("ip neigh show dev ens1 | grep -q '100.67.0.1.*PERMANENT'")
        machine.succeed("ip -n outside link set peer0 up; "
                        "ip -n outside -6 addr replace 2001:db8:dead::1/64 dev peer0 nodad")
        wait_proxy()
        check_pinned(machine)
