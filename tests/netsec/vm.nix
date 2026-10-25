{ pkgs, ... }:

pkgs.testers.runNixOSTest {
  name = "proxy-policy-recovery";
  node.pkgs = pkgs.lib.mkForce (pkgs.extend (_final: prev: { unstable = prev; }));
  nodes.machine = { config, lib, pkgs, ... }: {
    imports = [
      ../../system/config/machine.nix
      ../../system/config/hardening-options.nix
      ../../system/config/proxy-options.nix
      ../../system/config/proxy-profile.nix
      ../../system/config/network.nix
      ../../system/programs/systemd/proxy-mode.nix
      ../../system/programs/systemd/dns-upstream.nix
      ../../system/programs/systemd/gost-relay.nix
      ../../system/programs/systemd/nftables-verify.nix
      ../../system/programs/systemd/netsec-alert.nix
      ../../system/programs/systemd/network-bootstrap.nix
    ];
    users.users.lfour = { isNormalUser = true; uid = 1000; };
    services.tailscale.enable = lib.mkForce false;
    services.avahi.enable = lib.mkForce false;
    services.timesyncd.enable = lib.mkForce true;
    services.unbound.settings.server = {
      local-zone = [ ''"fixture.test." static'' ];
      local-data = [ ''"fixture.test. 0 IN A 192.0.2.42"'' ];
    };
    boot.kernel.sysctl."net.ipv4.ip_forward" = 1;
    my.proxy.transparentGuestInterfaces = [ "virbr0" ];
    networking.networkmanager.unmanaged = [
      "interface-name:eth*" "interface-name:guest*"
      "interface-name:virbr0" "interface-name:wlo1"
    ];
    # Keep the real unit dependency graph without needing a DHCP server. All
    # packet tests use namespaces and explicitly assigned addresses inside VM.
    systemd.services.dhcpcd.serviceConfig = {
      Type = lib.mkForce "simple";
      ExecStart = lib.mkForce "${pkgs.writeShellScript "test-dhcpcd" ''exec ${pkgs.coreutils}/bin/sleep infinity''} --config ${pkgs.writeText "test-dhcpcd.conf" ""}";
      # sleep.target's network hook reloads this mocked service. A real reload
      # command would start a host dhcpcd behind the sleeping placeholder.
      ExecReload = lib.mkForce "${pkgs.coreutils}/bin/true";
      PIDFile = lib.mkForce "";
    };
    systemd.services.nftables.serviceConfig.ExecStart = lib.mkForce [
      (pkgs.writeShellScript "first-load-fault" ''
        test -e /run/allow-full-policy || exit 1
        exec ${config.my.proxy.modeScript} --reload
      '')
    ];
    environment.etc."netsec-mode-b.nft".text = config.my.proxy.proxyModeRules;
    environment.etc."netsec-mode-c.nft".text = config.my.proxy.blockedModeRules;
    environment.etc."netsec-mode-script".source = config.my.proxy.modeScript;
    environment.etc."netsec-tproxy.py".source = ./tproxy_fixture.py;
    environment.etc."netsec-dns.py".source = ./dns_fixture.py;
    environment.etc."netsec-events.py".source = ../../system/programs/systemd/netsec/events.py;
    environment.etc."netsec-events-test.py".source = ./events_fixture.py;
    environment.etc."netsec-listeners-test.py".source = ./listeners_fixture.py;
    environment.etc."netsec-listener-check".source = config.my.proxy.listenerCheck;
    systemd.services.clash-verge.serviceConfig.ExecStart = pkgs.writeShellScript "test-core-listeners" ''
      exec ${pkgs.python3}/bin/python3 /etc/netsec-listeners-test.py serve "$(cat /run/listener-good-protocol)" --mixed
    '';
    systemd.services.netsec-listener-impostor.serviceConfig = {
      User = "lfour";
      ExecStart = pkgs.writeShellScript "test-impostor-listeners" ''
        exec ${pkgs.python3}/bin/python3 /etc/netsec-listeners-test.py serve "$(cat /run/listener-bad-protocol)"
      '';
    };
    environment.etc."netsec-split-test.py".source = ./split_fixture.py;
    environment.etc."netsec-policy.py".source = ../../system/programs/systemd/netsec/dns_policy.py;
    environment.etc."netsec-rules-check".source = config.my.proxy.rulesCheck;
    environment.etc."netsec-expected".source = config.my.proxy.expectedRules;
    environment.etc."netsec-split.json".text = builtins.toJSON {
      nft = "${pkgs.nftables}/bin/nft";
      nmcli = pkgs.writeShellScript "test-split-nmcli" ''
        case "$*" in
          *GENERAL.CON-UUID*)
            printf 'approved\n/org/freedesktop/NetworkManager/ActiveConnection/%s\n' "$(cat /run/split-active)"
            ;;
          *) printf '10.1.2.53\n\ncorp.test\n\n' ;;
        esac
      '';
      wireless = "wlo1";
      split_profiles = [ "approved" ];
      split_cidrs = [ "10.0.0.0/8" ];
      runtime = "/run/dns-upstream";
    };
    environment.etc."test-dns-sleep.json".text = builtins.toJSON {
      nft = pkgs.writeShellScript "fault-inject-sleep-nft" ''
        test ! -e /run/fail-sleep-nft || exit 1
        exec ${pkgs.nftables}/bin/nft "$@"
      '';
      systemctl = "${pkgs.systemd}/bin/systemctl";
    };
    systemd.services.dns-policy-sleep.serviceConfig.ExecStart = lib.mkForce
      "${pkgs.python3}/bin/python3 ${../../system/programs/systemd/netsec/dns_sleep.py} /etc/test-dns-sleep.json before";
    systemd.services.dns-policy-resume.serviceConfig.ExecStart = lib.mkForce
      "${pkgs.python3}/bin/python3 ${../../system/programs/systemd/netsec/dns_sleep.py} /etc/test-dns-sleep.json resume";
    # Hold sleep.target without invoking suspend or freezing the test driver.
    systemd.services.netsec-test-sleep-hold = {
      requires = [ "sleep.target" ];
      after = [ "sleep.target" ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; ExecStart = "${pkgs.coreutils}/bin/true"; };
    };
    environment.etc."guest-dhcpcd.conf".text = ''
      nohook resolv.conf
      nohook ntp.conf
      nohook hostname
      noipv4ll
    '';
    environment.systemPackages = [ pkgs.curl pkgs.python3 pkgs.jq pkgs.iproute2 pkgs.nftables
      pkgs.dnsmasq pkgs.dhcpcd pkgs.dnsutils pkgs.util-linux ];
    virtualisation.memorySize = 1536;
  };
  testScript = ''
    start_all()
    machine.wait_for_unit("network-firewall-bootstrap.service")
    machine.wait_until_fails("systemctl is-active --quiet nftables.service")
    machine.succeed("nft list table inet lf_bootstrap")
    machine.fail("nft list table inet lf_filter")

    # The peer is outside the host network stack, despite living in the VM.
    machine.succeed("ip netns add outside; ip link add ens1 type veth peer name peer0; ip link set peer0 netns outside")
    machine.succeed("ip addr add 100.67.0.2/24 dev ens1; ip link set ens1 up")
    machine.succeed("ip -n outside addr add 100.67.0.1/24 dev peer0; ip -n outside addr add 203.0.113.10/32 dev peer0; ip -n outside link set peer0 up; ip -n outside link set lo up")
    machine.succeed("ip route add 203.0.113.10/32 via 100.67.0.1 dev ens1")
    machine.succeed("python3 -m http.server 8080 --bind 0.0.0.0 >/tmp/host-http.log 2>&1 &")
    machine.fail("ip netns exec outside curl --noproxy '*' -fsS --connect-timeout 1 http://100.67.0.2:8080")

    machine.succeed("touch /run/allow-full-policy; systemctl reset-failed nftables.service; systemctl start nftables.service; systemctl start proxy-mode.service")
    machine.wait_for_unit("proxy-mode.service")
    machine.fail("nft list table inet lf_bootstrap")
    machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = direct")
    machine.succeed("nft add rule inet lf_filter input tcp dport 9999 accept")
    machine.wait_until_fails("nft list chain inet lf_filter input | grep -q 9999", timeout=90)
    machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = direct")

    # Freeze the supervisor only inside the VM to test packet policy fragments.
    machine.succeed("systemctl stop nftables-verify-pending.path nftables-verify.timer nftables-verify.service proxy-mode.service dns-upstream.service; systemctl mask --runtime proxy-mode.service")
    machine.succeed("python3 /etc/netsec-events-test.py /etc/netsec-events.py")
    machine.succeed("python3 /etc/netsec-listeners-test.py check")
    machine.succeed("/etc/netsec-listener-check dns")
    # Existing dedicated gateway listeners survive; resolved has no wildcard
    # port 53. These checks observe both transports, not just the TCP half.
    machine.fail("ss -Hlntu 'sport = :53' | grep -Eq '0.0.0.0:53|\\[::\\]:53|\\*:53'")
    machine.succeed("printf 'server=127.0.0.1#1055\\n' >/run/dns-upstream/servers.conf; systemctl reload dnsmasq.service")
    for transport in ["", "+tcp"]:
        machine.succeed(f"dig {transport} +time=1 +tries=1 +short @127.0.0.1 -p 1055 fixture.test A | grep -qx 192.0.2.42")
        machine.succeed(f"setpriv --reuid=985 --clear-groups dig {transport} +time=1 +tries=1 +short @127.0.0.1 -p 1055 fixture.test A | grep -qx 192.0.2.42")
        machine.fail(f"setpriv --reuid=1000 --clear-groups dig {transport} +time=1 +tries=1 @127.0.0.1 -p 1055 fixture.test A")
        machine.succeed(f"setpriv --reuid=1000 --clear-groups dig {transport} +time=1 +tries=1 +short @203.0.113.10 fixture.test A | grep -qx 192.0.2.42")

    # A failed required pre-sleep hook cancels the transaction. ExecStopPost
    # still queues recovery; a failed revoke keeps retrying without regranting.
    machine.succeed("touch /run/fail-sleep-nft")
    machine.fail("systemctl start netsec-test-sleep-hold.service")
    machine.succeed("test -e /run/dns-policy/sleeping")
    machine.fail("systemctl is-active --quiet sleep.target")
    machine.wait_until_succeeds("systemctl show -p NRestarts --value dns-policy-resume.service | grep -Eq '^[1-9][0-9]*$'")
    machine.succeed("test -e /run/dns-policy/sleeping")
    machine.succeed("rm /run/fail-sleep-nft")
    machine.wait_until_succeeds("test ! -e /run/dns-policy/sleeping")
    machine.wait_until_succeeds("test ! -e /run/netsec/alerts/dns-policy-sleep.service.json; test ! -e /run/netsec/alerts/dns-policy-resume.service.json")
    machine.succeed("systemctl start netsec-test-sleep-hold.service")
    machine.succeed("test -e /run/dns-policy/sleeping; systemctl start dns-policy.service; test $(jq -r .status /run/dns-policy/state.json) = suspended")
    machine.succeed("systemctl stop netsec-test-sleep-hold.service")
    machine.wait_until_succeeds("test ! -e /run/dns-policy/sleeping")
    machine.succeed("ip netns exec outside sh -c 'python3 -m http.server 8080 --bind 0.0.0.0 >/tmp/outside-http.log 2>&1 &'")
    machine.wait_until_succeeds("curl --noproxy '*' -fsS --connect-timeout 1 http://203.0.113.10:8080 >/dev/null")
    machine.succeed("ip link add virbr0 type bridge; ip addr add 192.168.122.1/24 dev virbr0; ip link set virbr0 up")
    machine.succeed("ip netns add guest; ip link add guest-host type veth peer name guest0; ip link set guest-host master virbr0; ip link set guest-host up; ip link set guest0 netns guest")
    machine.succeed("ip -n guest link set guest0 up; ip -n guest link set lo up")
    machine.succeed("systemd-run --unit=guest-dhcp-fixture /run/current-system/sw/bin/dnsmasq --keep-in-foreground --conf-file=/dev/null --pid-file=/run/guest-fixture.pid --bind-dynamic --interface=virbr0 --except-interface=lo --listen-address=192.168.122.1 --no-resolv --no-hosts --dhcp-range=192.168.122.10,192.168.122.20,1h --address=/fixture.test/192.0.2.10")
    machine.wait_until_succeeds("ss -uln | grep -q 192.168.122.1:53")
    machine.succeed("ip netns exec guest dhcpcd -B -1 -4 -t 10 --config /etc/guest-dhcpcd.conf guest0")
    machine.succeed("ip -n guest -4 addr show dev guest0 | grep -q 192.168.122.")
    machine.succeed("ip netns exec guest dig +time=1 +tries=1 @192.168.122.1 fixture.test A +short | grep -qx 192.0.2.10")
    machine.succeed("ip netns exec guest dig +tcp +time=1 +tries=1 @192.168.122.1 fixture.test A +short | grep -qx 192.0.2.10")
    machine.succeed("ip -n guest addr flush dev guest0; ip -n guest addr add 192.168.122.2/24 dev guest0; ip -n guest route replace default via 192.168.122.1")

    # A DNS permission expires even while its controller is stopped. Both the
    # socket UID and the private destination are real kernel facts in this VM.
    machine.succeed("ip netns exec outside sh -c 'python3 /etc/netsec-dns.py server 53 --dns-answer 192.0.2.99 >/tmp/dns-peer.log 2>&1 &'")
    machine.wait_until_succeeds("ip netns exec outside ss -uln | grep -q ':53 '")
    machine.fail("python3 /etc/netsec-dns.py query 100.67.0.1 --uid 985")
    machine.succeed("python3 /etc/netsec-dns.py lease 100.67.0.1 3; systemctl start dns-policy.service")
    machine.succeed("python3 /etc/netsec-dns.py query 100.67.0.1 --uid 985")
    machine.sleep(4)
    machine.fail("python3 /etc/netsec-dns.py query 100.67.0.1 --uid 985")
    machine.succeed("python3 /etc/netsec-dns.py lease 100.67.0.1 120; systemctl start dns-policy.service")

    # Wi-Fi is an uplink, including for global v4/v6 replies. A marked fixture
    # models the core's transport without depending on an internet node. The v6
    # prefix must not overlap the test driver's own vlan network (2001:db8:1::/64).
    machine.succeed("ip link add wlo1 type veth peer name wifi-peer; ip link set wifi-peer netns outside; ip link set wlo1 up")
    machine.succeed("ip addr add 198.51.100.2/24 dev wlo1; ip -6 addr add 2001:db8:dead::2/64 dev wlo1 nodad")
    machine.succeed("ip -n outside link set wifi-peer up; ip -n outside addr add 198.51.100.1/24 dev wifi-peer; ip -n outside -6 addr add 2001:db8:dead::1/64 dev wifi-peer nodad")
    machine.succeed("ip addr add 10.1.2.2/24 dev wlo1; ip -n outside addr add 10.1.2.53/24 dev wifi-peer")
    machine.succeed("python3 /etc/netsec-split-test.py")
    machine.succeed("ip netns exec outside sh -c 'python3 /etc/netsec-dns.py server 15353 >/tmp/wifi-peer.log 2>&1 &'")
    machine.wait_until_succeeds("python3 /etc/netsec-dns.py query 198.51.100.1 15353 --mark 6666")
    machine.succeed("python3 /etc/netsec-dns.py query 2001:db8:dead::1 15353 --mark 6666")
    machine.wait_until_succeeds("ip netns exec guest curl --noproxy '*' -fsS --connect-timeout 1 http://203.0.113.10:8080 >/dev/null")
    machine.succeed("nft -f /etc/netsec-mode-b.nft")
    machine.succeed("ip netns exec guest dig +time=1 +tries=1 @192.168.122.1 fixture.test A +short | grep -qx 192.0.2.10")
    machine.succeed("ip netns exec guest dig +tcp +time=1 +tries=1 @192.168.122.1 fixture.test A +short | grep -qx 192.0.2.10")
    machine.fail("python3 /etc/netsec-dns.py query 100.67.0.1 --uid 985")
    machine.succeed("python3 /etc/netsec-dns.py query 198.51.100.1 15353 --mark 6666")
    machine.succeed("python3 /etc/netsec-dns.py query 2001:db8:dead::1 15353 --mark 6666")
    machine.succeed("systemd-run --unit=netsec-tproxy /run/current-system/sw/bin/python3 /etc/netsec-tproxy.py server")
    machine.wait_until_succeeds("ip netns exec guest curl --noproxy '*' -fsS --connect-timeout 1 http://203.0.113.10:8080 | grep -qx OK")
    machine.succeed("systemd-run --unit=netsec-guest ip netns exec guest /run/current-system/sw/bin/python3 /etc/netsec-tproxy.py client")
    machine.wait_until_succeeds("test -e /tmp/guest-ready")
    machine.succeed("nft -f /etc/netsec-mode-c.nft")
    machine.succeed("ip netns exec guest dig +time=1 +tries=1 @192.168.122.1 fixture.test A +short | grep -qx 192.0.2.10")
    # TCP DNS replies come from the accepted socket, whose sk_uid is the
    # dropped dnsmasq uid: the reply rules must not be uid-scoped.
    machine.succeed("ip netns exec guest dig +tcp +time=1 +tries=1 @192.168.122.1 fixture.test A +short | grep -qx 192.0.2.10")
    machine.succeed("python3 /etc/netsec-dns.py query 2001:db8:dead::1 15353 --mark 6666")
    machine.succeed("touch /tmp/guest-next")
    machine.wait_until_succeeds("grep -qx blocked /tmp/guest-result")
    machine.fail("ip netns exec guest curl --noproxy '*' -fsS --connect-timeout 1 http://203.0.113.10:8080 >/dev/null")
    machine.succeed("ip netns exec guest curl --noproxy '*' -fsS --connect-timeout 1 http://100.67.0.1:8080 >/dev/null")
    machine.fail("su -s /bin/sh lfour -c \"curl --noproxy '*' -fsS --connect-timeout 1 http://203.0.113.10:8080\"")
    machine.fail("su -s /bin/sh lfour -c \"curl --noproxy '*' -fsS --connect-timeout 1 http://127.0.0.1:7897\"")
    machine.succeed("echo proxy >/run/proxy-mode/intent; /etc/netsec-mode-script --reload; systemctl unmask --runtime proxy-mode.service; systemctl start proxy-mode.service nftables-verify-pending.path nftables-verify.timer")
    machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = blocked")
    machine.wait_until_succeeds("test $(jq -r .result /run/netsec/state.json) = ok", timeout=90)
    # A commit and a second request during liveness checks must leave a rerun,
    # rather than being swallowed by an already-running oneshot service.
    machine.succeed("systemctl stop unbound.service; touch /run/netsec/requests/request.first")
    machine.wait_until_succeeds("find /run/netsec -maxdepth 1 -name 'rules.*.json' | grep -q .")
    machine.succeed("touch /run/netsec/requests/request.second; /etc/netsec-mode-script --reload; systemctl start unbound.service")
    machine.wait_until_succeeds("test -z \"$(ls -A /run/netsec/requests)\"", timeout=90)
    machine.succeed("test $(jq -r .version /run/netsec/state.json) = $(jq -r .version /run/proxy-mode/state.json)")
  '';
}

