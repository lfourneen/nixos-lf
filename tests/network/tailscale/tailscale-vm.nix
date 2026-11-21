{ pkgs, tailscale, restricted ? false }:

let
  tailPort = 41642;
in

pkgs.testers.runNixOSTest {
  name = if restricted then "restricted-direct-egress" else "tailscale-transport-policy";
  node.pkgs = pkgs.lib.mkForce (pkgs.extend (_final: prev: { unstable = prev; }));

  nodes.machine = { config, lib, pkgs, ... }: {
    imports = [
      ../../../system/programs/clash/service/identity.nix
      ../../../system/config/machine.nix
      ../../../system/config/network/options.nix
      ../../../system/config/network/proxy/options.nix
      ../../../system/config/network/proxy/profile.nix
      ../../../system/config/network/network.nix
      ../../../system/config/network/proxy/coordinator.nix
      ../../../system/config/network/firewall/bootstrap.nix
      ../../../system/config/network/firewall/alert.nix
    ];

    users.users.lfour = { isNormalUser = true; uid = 1000; };
    users.groups.gost = { };
    users.users.gost = { isSystemUser = true; group = "gost"; uid = config.my.machine.uids.gost; };
    users.groups.dhcpcd = { };
    users.users.dhcpcd = { isSystemUser = true; group = "dhcpcd"; };
    services.tailscale.package = lib.mkForce tailscale;
    services.tailscale.port = tailPort;
    my.hardening.allowPrivateEgress = !restricted;
    my.proxy.authorizationLeaseSeconds = lib.mkIf (!restricted) 120;

    my.hardening.directEgress = lib.mkIf restricted {
      mode = "restricted";
      unboundDotInProxy = false;
      timeServerCidrs = [ "203.0.113.10/32" "2001:db8:ffff::10/128" ];
      exceptions = [
        { interface = "ens1"; cidr = "10.44.0.1/32"; uids = [ 1000 ]; tcpPorts = [ 42424 ]; udpPorts = [ 42424 ]; }
        { interface = "ens1"; cidr = "fd42:44::1/128"; uids = [ 1000 ]; tcpPorts = [ 42424 ]; udpPorts = [ 42424 ]; }
        { interface = "ens1"; cidr = "239.255.42.1/32"; uids = [ 1000 ]; udpPorts = [ 53530 ]; }
        { interface = "ens1"; cidr = "ff02::4242/128"; uids = [ 1000 ]; udpPorts = [ 53530 ]; }
      ];
    };

    services.avahi.enable = lib.mkForce false;
    networking.interfaces.ens1.useDHCP = lib.mkForce false;
    networking.networkmanager.unmanaged = [ "interface-name:eth*" "interface-name:ens1" "interface-name:wlo1" ];

    # Exercise the actual production fragments without a proxy core, live
    # tailnet, public control server, or mode supervisor repairing test changes.
    systemd.services = lib.genAttrs [ "proxy-mode" "tailscaled" "tailscaled-set"
      "dhcpcd" "dnsmasq" "unbound" "chronyd" ] (_: { enable = lib.mkForce false; });
    environment.etc."tail-mode-b.nft".text = config.my.proxy.proxyModeRules;
    environment.etc."tail-mode-c.nft".text = config.my.proxy.blockedModeRules;
    environment.etc."tail-fixture.py".source = ./tailscale_fixture.py;
    environment.etc."egress-fixture.py".source = ../firewall/egress_fixture.py;
    environment.etc."tail-strict.nft".source = pkgs.runCommand "tail-strict-input-rules" { nativeBuildInputs = [ pkgs.python3 ]; } ''
      python3 - <<'PY' > "$out"
      import runpy
      firewall = runpy.run_path('${../../../system/config/network/proxy/routing.py}')['firewall']
      print(firewall(dict(state='/run/synthetic-missing-authorization', topology=dict(core4='169.254.252.2', core6='fd42:72::2'),
                          interface='clash-test-host', uplinks=['ens1', 'wlo1'], guest_interfaces=[], guests=[],
                          dhcp_uid=${toString config.my.machine.uids.dhcpcd}), 'blocked'))
      PY
    '';

    # Remove only host grants to test revocation; keep the production static
    # policy and restricted infrastructure identities exactly as configured.
    environment.etc."tail-revoked-b.nft".text = (import ../../../system/config/network/network.nix {
      inherit pkgs lib;
      config = config // { my = config.my // { hardening = config.my.hardening // {
        directEgress = config.my.hardening.directEgress // { exceptions = [ ]; };
      }; }; };
    }).my.proxy.proxyModeRules;

    environment.etc."tail-expected".source = config.my.proxy.expectedRules;
    environment.etc."tail-rule-check".source = config.my.proxy.rulesCheck;
    environment.etc."tail-mode-script".source = config.my.proxy.modeScript;
    environment.systemPackages = [ pkgs.python3 pkgs.iproute2 pkgs.nftables pkgs.jq tailscale ];
    virtualisation.vlans = [ 9 ];
    virtualisation.memorySize = 768;
  };

  testScript = ''
    import json
    restricted = ${if restricted then "True" else "False"}

    def mode(name):
        if name == "direct":
            machine.succeed("/etc/tail-mode-script --reload")
        else:
            machine.succeed(f"nft -f /etc/tail-mode-{'b' if name == 'proxy' else 'c'}.nft")
        machine.succeed(f"nft -j list ruleset >/run/tail-rules.json; /etc/tail-rule-check compare /etc/tail-expected/{name}.json /run/tail-rules.json")
        if name == "proxy" and not restricted:
            machine.succeed("nft 'add element inet lf_filter b_authorization { ipv4, ipv6 }'")

    def probe(address, port=42424, mark=0, uid=0):
        return f"${pkgs.python3}/bin/python3 /etc/tail-fixture.py client {address} {port} --mark {mark} --uid {uid}"

    start_all()
    machine.wait_for_unit("nftables.service")
    machine.succeed("ip netns add outside; ip -n outside link set lo up")
    for interface, peer, v4, v6 in (("ens1", "peer0", "198.51.100", "2001:db8:dead"),
                                    ("wlo1", "peer1", "192.0.2", "2001:db8:cafe")):
        machine.succeed(f"ip link add {interface} type veth peer name {peer}; ip link set {peer} netns outside; ip link set {interface} up; ip -n outside link set {peer} up")
        machine.succeed(f"ip addr add {v4}.2/24 dev {interface}; ip -n outside addr add {v4}.1/24 dev {peer}; ip -6 addr add {v6}::2/64 dev {interface} nodad; ip -n outside -6 addr add {v6}::1/64 dev {peer} nodad")
    machine.succeed("ip -n outside addr add 203.0.113.10/32 dev peer0; ip -n outside -6 addr add 2001:db8:ffff::10/128 dev peer0 nodad")
    machine.succeed("systemd-run --unit=tail-peer ip netns exec outside ${pkgs.python3}/bin/python3 /etc/tail-fixture.py server; systemd-run --unit=tail-api ${pkgs.python3}/bin/python3 /etc/tail-fixture.py api")
    machine.wait_for_file("/run/netsec-tailapi.sock")
    machine.wait_until_succeeds("ip netns exec outside ss -lun | grep -q ':42424 '")

    if not restricted:
        with subtest("Tailscale physical UDP requires the live marked listener and allowed mode"):
            tail_port = ${toString tailPort}
            fixture = "${pkgs.python3}/bin/python3 /etc/tail-fixture.py"
            destinations = ("198.51.100.2", "2001:db8:dead::2", "192.0.2.2", "2001:db8:cafe::2")

            def outside(address, sport, *, send_only=False):
                command = "ip netns exec outside " + probe(address, tail_port) + f" --sport {sport}"
                return command + (" --send-only" if send_only else "")

            def count(chain, verdict):
                table = "lf_routing" if chain.startswith("lf_routing_") else "lf_filter"
                output = json.loads(machine.succeed(f"nft -j list chain inet {table} {chain}"))
                rules = [item["rule"]["expr"] for item in output["nftables"] if "rule" in item]
                return sum(expr["counter"]["packets"] for rule in rules
                           if any(verdict in action for action in rule)
                           for expr in rule if "counter" in expr)

            def observe(chain, verdict, start=43010):
                previous = count(chain, verdict)
                for address in destinations:
                    machine.succeed(outside(address, start, send_only=True))
                current = count(chain, verdict)
                assert current >= previous + len(destinations), (chain, verdict, previous, current)

            def wait_transport(process):
                for family in (4, 6):
                    machine.wait_until_succeeds(f"ss -{family}Hnulp 'sport = :{tail_port}' | grep -F {process}", timeout=20)

            def start_echo(mark):
                machine.succeed(f"systemd-run --unit=tail-echo {fixture} transport-server {tail_port} {mark}")
                wait_transport("python3")

            mode("direct")
            observe("lf_tailscale_ingress", "drop")
            local_range = machine.succeed("sysctl -n net.ipv4.ip_local_port_range").strip()
            try:
                machine.succeed(f"sysctl -w 'net.ipv4.ip_local_port_range={tail_port} {tail_port + 1}'")
                for slice in ("user", "hermes", "untrusted"):
                    machine.succeed(f"systemd-run --wait --pipe --slice={slice}.slice --uid=lfour {fixture} check-bind {tail_port}")
            finally:
                machine.succeed(f"sysctl -w 'net.ipv4.ip_local_port_range={local_range}'")

            # The real daemon starts both magicsock families without enrolling
            # in a tailnet. Inbound UDP may reach it without an application echo.
            launch = ("systemd-run --collect --unit=tail-real --property=RuntimeDirectory=tail-real "
                      "${tailscale}/bin/tailscaled --state=mem: --statedir=/run/tail-real "
                      "--socket=/run/tail-real/tailscaled.sock --tun=tail-probe "
                      f"--port={tail_port} --no-logs-no-support")
            machine.succeed(launch)
            wait_transport("tailscaled")
            for state in ("direct", "proxy"):
                mode(state)
                observe("lf_tailscale_ingress", "accept")
            mode("blocked")
            observe("lf_tailnet_guard", "drop")
            mode("proxy")
            machine.succeed("nft 'flush set inet lf_filter b_authorization; add element inet lf_filter b_authorization { ipv4 timeout 5s, ipv6 timeout 5s }'")
            observe("lf_tailscale_ingress", "accept")
            machine.wait_until_succeeds("! nft get element inet lf_filter b_authorization '{ ipv4 }'")
            observe("lf_lease_input", "drop")
            mode("proxy")
            machine.succeed("nft -f /etc/tail-strict.nft")
            observe("lf_routing_input", "drop")
            machine.succeed("nft delete table inet lf_routing")
            machine.succeed("systemctl stop tail-real")
            observe("lf_tailscale_ingress", "drop")
            machine.succeed(launch)
            wait_transport("tailscaled")
            observe("lf_tailscale_ingress", "accept")
            machine.succeed("systemctl stop tail-real")

            # The marked echo fixture proves the return path. Replacing it
            # with an unmarked socket cannot inherit the old conntrack flow.
            start_echo(0x80000)
            for state in ("direct", "proxy"):
                mode(state)
                for address in destinations:
                    machine.succeed(outside(address, 43011))
            mode("blocked")
            observe("lf_tailnet_guard", "drop", start=43011)
            mode("direct")
            machine.succeed("systemctl stop tail-echo")
            start_echo(0)
            observe("lf_tailscale_ingress", "drop", start=43011)
            observe("lf_tailscale_ingress", "drop", start=43012)
            machine.succeed("systemctl stop tail-echo")

    for interface, v4, v6 in (("ens1", "198.51.100", "2001:db8:dead"),
                               ("wlo1", "192.0.2", "2001:db8:cafe")):
        machine.succeed(f"ip route replace default via {v4}.1 dev {interface} metric 1; ip -6 route replace default via {v6}::1 dev {interface} metric 1")
        mode("direct")
        for address in ("203.0.113.10", "2001:db8:ffff::10"):
            machine.succeed(probe(address))
            machine.succeed(probe(address, uid=1000))
        mode("proxy")
        for address in ("203.0.113.10", "2001:db8:ffff::10"):
            for port in (3478, 42424):
                machine.succeed(probe(address, port, mark=0x80000))
                machine.fail(probe(address, port))
            machine.fail(probe(address, mark=0x40000))
            machine.fail(probe(address, uid=1000))
            machine.fail(probe(address, mark=0x80000, uid=1000))

        # The real production CLI creates its own marked UDP4/UDP6 sockets.
        # Only the DERP map and STUN peer are fixtures; no fabricated CLI report
        # or Python-applied SO_MARK can satisfy this compatibility check.
        report = json.loads(machine.succeed("timeout 20 tailscale --socket=/run/netsec-tailapi.sock netcheck --format=json"))
        assert report["UDP"] and report["IPv4"] and report["IPv6"], report
        fallback = json.loads(machine.succeed("TS_FORCE_LINUX_BIND_TO_DEVICE=true timeout 20 tailscale --socket=/run/netsec-tailapi.sock netcheck --format=json"))
        assert not fallback["UDP"], fallback

        # A packet mark alone must not authorize an unmarked root socket.
        machine.succeed("nft add table inet tail_packet_mark; nft 'add chain inet tail_packet_mark output { type route hook output priority -5; policy accept; }'; nft add rule inet tail_packet_mark output udp sport 25002 meta mark set 0x80000")
        machine.fail(probe("203.0.113.10") + " --sport 25002")
        machine.succeed("nft delete table inet tail_packet_mark")

        stem = f"/run/tail-{interface}"
        machine.succeed(f"systemd-run --unit=tail-session-{interface} {probe('203.0.113.10', mark=0x80000)} --persistent {stem}")
        machine.wait_for_file(stem + ".ready")
        mode("blocked")
        machine.succeed(f"touch {stem}.blocked")
        machine.wait_for_file(stem + ".denied")
        for address in ("203.0.113.10", "2001:db8:ffff::10"):
            machine.fail(probe(address, mark=0x80000))
        mode("proxy")
        machine.succeed(f"touch {stem}.restore")
        machine.wait_for_file(stem + ".recovered")

    # Model decrypted overlay ingress. Listeners cover the denied ports too,
    # so a refused probe demonstrates policy rather than a missing service.
    mode("direct")
    machine.succeed("ip link add tailscale0 type veth peer name tail-peer; ip link set tail-peer netns outside; ip addr add 100.64.0.2/30 dev tailscale0; ip link set tailscale0 up; ip -n outside addr add 100.64.0.1/30 dev tail-peer; ip -n outside link set tail-peer up")
    machine.succeed("systemd-run --unit=tail-host ${pkgs.python3}/bin/python3 /etc/tail-fixture.py host-server")
    machine.wait_until_succeeds("ss -ltn | grep -q ':47990 '")
    for state in ("direct", "proxy"):
        mode(state)
        for port in (47984, 47989, 48010):
            machine.succeed("ip netns exec outside " + probe("100.64.0.2", port) + " --tcp")
        for port in (47985, 47990):
            machine.fail("ip netns exec outside " + probe("100.64.0.2", port) + " --tcp")
        for port in range(47998, 48011):
            command = "ip netns exec outside " + probe("100.64.0.2", port)
            if port in (47998, 47999, 48000, 48002, 48010):
                machine.succeed(command)
            else:
                machine.fail(command)
        machine.fail("ip netns exec outside " + probe("192.0.2.2", 47989) + " --tcp")
    # Reuse an established decrypted overlay socket: C must cut input itself,
    # before established accepts, and B must restore the streaming policy.
    mode("proxy")
    machine.succeed("systemd-run --unit=tail-ingress ip netns exec outside " + probe("100.64.0.2", 47998) + " --persistent /run/tail-ingress")
    machine.wait_for_file("/run/tail-ingress.ready", timeout=15)
    mode("blocked")
    machine.succeed("touch /run/tail-ingress.blocked")
    machine.wait_for_file("/run/tail-ingress.denied")
    ingress = json.loads(machine.succeed("nft -j list chain inet lf_filter lf_tailnet_guard"))
    assert sum(expr["counter"]["packets"] for item in ingress["nftables"] for expr in item.get("rule", {}).get("expr", []) if "counter" in expr) > 0
    machine.fail("ip netns exec outside " + probe("100.64.0.2", 47989) + " --tcp")
    machine.fail("ip netns exec outside " + probe("100.64.0.2", 47998))
    mode("proxy")
    machine.succeed("touch /run/tail-ingress.restore")
    machine.wait_for_file("/run/tail-ingress.recovered")

    mode("direct")
    machine.succeed("systemctl stop tail-host.service")
    machine.succeed("ip addr add 10.44.0.2/24 dev ens1; ip -n outside addr add 10.44.0.1/24 dev peer0; ip -n outside addr add 10.44.0.3/32 dev peer0")
    machine.succeed("ip -6 addr add fd42:44::2/64 dev ens1 nodad; ip -n outside -6 addr add fd42:44::1/64 dev peer0 nodad; ip -n outside -6 addr add fd42:44::3/128 dev peer0 nodad")
    machine.succeed("ip -n outside addr add 203.0.113.11/32 dev peer0; ip -n outside addr add 101.226.4.6/32 dev peer0; ip -n outside -6 addr add 2001:db8:ffff::11/128 dev peer0 nodad")
    machine.succeed("ip -n outside addr add 100.100.100.100/32 dev tail-peer; ip route add 100.100.100.100/32 dev tailscale0")
    machine.succeed("ip route replace 239.255.42.1/32 dev ens1")
    machine.succeed("systemd-run --unit=egress-peer ip netns exec outside ${pkgs.python3}/bin/python3 /etc/egress-fixture.py peer; systemd-run --unit=egress-gateway ${pkgs.python3}/bin/python3 /etc/egress-fixture.py gateway; systemd-run --unit=egress-multicast ip netns exec outside ${pkgs.python3}/bin/python3 /etc/egress-fixture.py multicast")
    machine.wait_for_file("/run/egress-multicast.ready")
    machine.wait_until_succeeds("ip netns exec outside ss -ltn | grep -q ':853 '")
    machine.wait_until_succeeds("ss -ltn | grep -q ':1054 '")
    chrony = int(machine.succeed("id -u chrony"))
    resolve = int(machine.succeed("id -u systemd-resolve"))

    for state in ("direct", "proxy", "blocked"):
        mode(state)
        # Unbound is constrained to its reviewed IPs/TCP, even in direct A.
        dot = probe("101.226.4.6", 853, uid=983) + " --tcp"
        (machine.succeed if state == "direct" else machine.fail)(dot)
        machine.fail(probe("203.0.113.11", 853, uid=983) + " --tcp")
        machine.fail(probe("101.226.4.6", 853, uid=983))
        machine.fail(probe("101.226.4.6", 853, uid=1000) + " --tcp")
        for good, bad in (("203.0.113.10", "203.0.113.11"),
                          ("2001:db8:ffff::10", "2001:db8:ffff::11")):
            for port, suffix in ((123, ""), (4460, " --tcp")):
                (machine.fail if state == "blocked" else machine.succeed)(probe(good, port, uid=chrony) + suffix)
                (machine.fail if state == "blocked" or restricted else machine.succeed)(probe(bad, port, uid=chrony) + suffix)
        # The strict B option never changes A's portal/private access.
        for address, other in (("10.44.0.1", "10.44.0.3"), ("fd42:44::1", "fd42:44::3")):
            for suffix in ("", " --tcp"):
                (machine.fail if state == "blocked" else machine.succeed)(probe(address, uid=1000) + suffix)
                (machine.fail if state == "blocked" or (state == "proxy" and restricted) else machine.succeed)(probe(other, uid=1000) + suffix)
                (machine.fail if state == "blocked" or (state == "proxy" and restricted) else machine.succeed)(probe(address, 42425, uid=1000) + suffix)
                (machine.fail if state == "blocked" or (state == "proxy" and restricted) else machine.succeed)(probe(address, uid=1001) + suffix)

    mode("proxy")
    # A core mark also needs a permitted socket UID, in B and C.
    for state in ("proxy", "blocked"):
        mode(state)
        machine.succeed(probe("203.0.113.10", mark=6666, uid=982))
        machine.fail(probe("203.0.113.10", mark=6666, uid=1000))
        machine.succeed("nft add table inet core_packet_mark; nft 'add chain inet core_packet_mark output { type route hook output priority -202; policy accept; }'; nft add rule inet core_packet_mark output udp sport 25003 meta mark set 6666")
        machine.fail(probe("203.0.113.10") + " --sport 25003")
        machine.succeed("nft delete table inet core_packet_mark")

    mode("proxy")
    # Strict grants bind the route interface too; the peer is reachable over
    # both links, so refusal is not an unreachable-address surrogate.
    machine.succeed("ip route add 10.44.0.1/32 via 192.0.2.1 dev wlo1")
    (machine.fail if restricted else machine.succeed)(probe("10.44.0.1", uid=1000))
    machine.succeed("ip route del 10.44.0.1/32")
    for address in ("10.44.0.1", "fd42:44::1"):
        for suffix in ("", " --tcp"):
            machine.succeed(probe(address, 53, uid=1000) + suffix + " --expect-prefix managed:")

    # The resolver stays on the tailnet interface in both egress policies.
    machine.succeed(probe("100.100.100.100", 53, uid=resolve))
    (machine.fail if restricted else machine.succeed)(probe("100.100.100.100", 53, uid=1000))
    machine.succeed("ip route replace 100.100.100.100/32 via 198.51.100.1 dev ens1")
    for suffix in ("", " --tcp"):
        machine.fail(probe("100.100.100.100", 53, uid=resolve) + suffix)
    machine.succeed("ip route replace 100.100.100.100/32 dev tailscale0")

    # An approved lease is still required for dnsmasq's direct split DNS.
    machine.fail(probe("10.44.0.1", 53, uid=985))
    machine.succeed("nft add element inet lf_filter dns_split4 '{ \"ens1\" . 10.44.0.1 timeout 60s }'")
    for suffix in ("", " --tcp"):
        machine.succeed(probe("10.44.0.1", 53, uid=985) + suffix)
    machine.succeed("nft delete element inet lf_filter dns_split4 '{ \"ens1\" . 10.44.0.1 }'")
    machine.fail(probe("10.44.0.1", 53, uid=985))

    for state in ("direct", "proxy", "blocked"):
        mode(state)
        for family, address in ((4, "239.255.42.1"), (6, "ff02::4242")):
            for uid, port in ((1000, 53530), (1001, 53530), (1000, 53531)):
                token = f"{state}-{family}-{uid}-{port}"
                command = probe(address, port, uid=uid) + f" --interface ens1 --send-only --token {token}"
                allowed = state == "direct" or (state == "proxy" and (not restricted or (uid == 1000 and port == 53530)))
                (machine.succeed if allowed else machine.fail)(command)
                if allowed:
                    machine.wait_until_succeeds(f"grep -q '{token}' /run/egress-multicast.jsonl")

    mode("proxy")
    # Sunshine's negotiated media source ports stay usable only in B; C also
    # refuses these explicitly permitted desktop-owned sockets.
    machine.succeed(probe("100.64.0.1", uid=1000) + " --sport 47998")
    if restricted:
        machine.fail(probe("100.64.0.1", uid=1000) + " --sport 47997")
        machine.fail(probe("100.64.0.1") + " --sport 47998")
        targets = {4: "10.44.0.1", 6: "fd42:44::1"}
        for family, address in targets.items():
            stem = f"/tmp/egress-session-{family}"
            machine.succeed(f"systemd-run --unit=egress-session-{family} {probe(address, uid=1000)} --persistent {stem}")
            machine.wait_for_file(stem + ".ready")
        machine.succeed("nft -f /etc/tail-revoked-b.nft")
        for family, address in targets.items():
            stem = f"/tmp/egress-session-{family}"
            machine.succeed(f"touch {stem}.blocked")
            machine.wait_for_file(stem + ".denied")
            machine.fail(probe(address, uid=1000))
            machine.fail(probe(address, uid=1000) + " --tcp")
        machine.succeed(probe("203.0.113.10", port=123, uid=chrony))
        mode("proxy")
        for family in targets:
            stem = f"/tmp/egress-session-{family}"
            machine.succeed(f"touch {stem}.restore")
            machine.wait_for_file(stem + ".recovered")
    mode("blocked")
    machine.fail(probe("100.64.0.1", uid=1000) + " --sport 47998")
    machine.fail(probe("10.44.0.1", uid=1000))
  '';
}
