{ pkgs, clashPackage, restricted ? false, strictDns ? false, ech ? null, targetPolicy ? false }:

let
  echFixture = pkgs.runCommand "ech-origin-fixture" { nativeBuildInputs = [ pkgs.go ]; } ''
    export GOCACHE="$TMPDIR/go-cache"
    export GO111MODULE=off
    export CGO_ENABLED=0
    mkdir -p "$out/bin"
    go build -trimpath -o "$out/bin/ech-fixture" ${./ech_server.go}
  '';
in

pkgs.testers.runNixOSTest {
  name = if targetPolicy then "proxy-target-authorization" else if ech != null then "browser-ech" else if strictDns then "strict-dns-policy" else if restricted then "restricted-egress-core" else "proxy-control-privileges";
  node.pkgs = pkgs.lib.mkForce (pkgs.extend (_final: prev: { unstable = prev; }));
  nodes.machine = { config, lib, pkgs, ... }: {
    imports = [
      ../../system/config/machine.nix
      ../../system/config/hardening-options.nix
      ../../system/config/proxy-options.nix
      ({ config, lib, ... }: let
        profile = import ../../system/config/proxy-profile.nix { inherit config lib; };
      in profile // {
        options.my.proxy = profile.options.my.proxy // {
          managedDns = profile.options.my.proxy.managedDns // {
            default = profile.options.my.proxy.managedDns.default // {
              nameserver = [ "udp://100.67.0.1:15354" ];
              default-nameserver = [ "100.67.0.1" ];
              proxy-server-nameserver = [ "udp://100.67.0.1:15354" ];
              nameserver-policy = { };
              respect-rules = false;
            };
          };
        };
      })
      ../../system/config/network.nix
      ../../system/config/dns-blocklist.nix
      ../../system/programs/systemd/proxy-mode.nix
      ../../system/programs/systemd/dns-upstream.nix
      ../../system/programs/systemd/gost-relay.nix
      ../../system/programs/clash/runtime.nix
    ];
    users.users.lfour = { isNormalUser = true; uid = 1000; };
    users.groups.dhcpcd = { };
    users.users.dhcpcd = { isSystemUser = true; group = "dhcpcd"; };
    programs.clash-verge = {
      enable = true;
      serviceMode = true;
      group = "clash-verge-control";
      package = clashPackage;
    };
    my.proxy.nodeProbeUrls = [ "http://node.fixture.example:8080/" ];
    my.proxy.tunProbeName = "tun.fixture.example";
    my.proxy.dnsEncryption = if strictDns then "strict" else "opportunistic";
    my.proxy.blockedDomains = [ "ads.fixture.example" "blocked.fixture.test" "metrics.home.arpa" ];
    my.proxy.blockedDomainExceptions = [ "ok.blocked.fixture.test" ];
    my.proxy.remoteTargetGrants = lib.optionals targetPolicy [{
      proxy = "Fixture"; cidr = "127.0.0.1/32";
      inlets = [ "SOCKS5" ]; network = "tcp"; ports = [ 18082 ];
    }];
    systemd.services.dns-upstream.environment = lib.mkIf strictDns {
      NETSEC_DOT_PRIME_ROUNDS = "0";
      NETSEC_PROBE_TIMEOUT = "0.5";
      NETSEC_PROBE_TRANSPORT_TIMEOUT = "0.5";
    };
    services.tailscale.enable = lib.mkForce false;
    my.hardening.allowPrivateEgress = !restricted;
    my.hardening.directEgress = lib.mkIf restricted {
      mode = "restricted";
      unboundDotInProxy = false;
      timeServerCidrs = [ "203.0.113.10/32" "2001:db8:ffff::10/128" ];
      exceptions = [{
        interface = "ens1";
        cidr = "100.67.0.1/32";
        uids = [ 1000 ];
        tcpPorts = [ 8080 ];
      }];
    };
    services.avahi.enable = lib.mkForce false;
    networking.networkmanager.unmanaged = [ "interface-name:eth*" "interface-name:ens1" "interface-name:wlo1" ];
    networking.interfaces.ens1.useDHCP = lib.mkForce false;
    # No external network is used by this test. Keep daemon startup independent
    # of a real DHCP lease and use one local peer for every DNS/HTTP/SOCKS flow.
    systemd.services.dhcpcd.enable = lib.mkForce false;
    environment.etc."netsec-integration.py".source = ./integration_fixture.py;
    environment.etc."netsec-dns.py".source = ./dns_fixture.py;
    environment.etc."netsec-tail.py".source = ./tailscale_fixture.py;
    environment.etc."netsec-integration-policy.json".text = builtins.toJSON config.my.proxy.managedProfile;
    environment.etc."netsec-control.py".source = ./control_fixture.py;
    environment.etc."netsec-target.py" = lib.mkIf targetPolicy { source = ./target_fixture.py; };
    environment.etc."netsec-launcher".text = "${clashPackage}/bin/verge-mihomo";
    environment.etc."netsec-core".text = "${clashPackage.managedCore}/bin/mihomo";
    environment.etc."netsec-firefox-driver.py" = lib.mkIf (ech != null) { source = ./firefox_driver.py; };
    environment.etc."netsec-ech.py" = lib.mkIf (ech != null) { source = ./test_ech.py; };
    environment.etc."netsec-ech.json" = lib.mkIf (ech != null) {
      text = builtins.toJSON (lib.mapAttrs (_: firefox: {
        package = firefox;
        settings = firefox.netsec.settings;
        guard = ../../home/programs/firefox/guard.py;
      }) ech);
    };
    environment.systemPackages = [
      (pkgs.python3.withPackages (p: [ p.pyyaml ]))
      pkgs.curl pkgs.jq pkgs.dnsutils pkgs.iproute2 pkgs.nftables pkgs.util-linux
    ] ++ lib.optionals (ech != null) [ pkgs.nssTools ];
    virtualisation.memorySize = if ech != null then 3072 else 1024;
  };
  testScript = ''
    def wait_proxy():
        try:
            machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = proxy && test $(jq -r .mode /run/dns-upstream/state.json) = proxy && python3 /etc/netsec-control.py check", timeout=90)
        except Exception:
            print(machine.execute("cat /run/proxy-mode/state.json /run/proxy-mode/health.json; journalctl -u clash-verge -u proxy-mode --no-pager -n 40; cat /run/clash-verge-rev/validator.log; find /var/lib/clash-verge-service -name '*.log' -exec tail -n 40 {} +"))
            raise

    start_all()
    machine.wait_for_unit("nftables.service")
    machine.wait_for_unit("proxy-mode.service")
    machine.succeed("ip netns add outside; ip link add ens1 type veth peer name peer0; ip link set peer0 netns outside")
    machine.succeed("ip addr add 100.67.0.2/24 dev ens1; ip link set ens1 up; ip -n outside addr add 100.67.0.1/24 dev peer0; ip -n outside addr add 203.0.113.10/32 dev peer0; ip -n outside link set peer0 up; ip -n outside link set lo up; ip route add default via 100.67.0.1 dev ens1 metric 1")
    machine.succeed("ip -6 addr add 2001:db8:dead::2/64 dev ens1 nodad; ip -n outside -6 addr add 2001:db8:dead::1/64 dev peer0 nodad; ip -6 route add default via 2001:db8:dead::1 dev ens1 metric 1")
    machine.succeed("python3 /etc/netsec-integration.py prepare; python3 /etc/netsec-control.py prepare")
    ${pkgs.lib.optionalString targetPolicy ''
    machine.succeed("python3 /etc/netsec-target.py prepare; python3 /etc/netsec-control.py prepare")
    machine.succeed("systemd-run --unit=target-local python3 /etc/netsec-target.py server local 18081; systemd-run --unit=target-remote ip netns exec outside ${pkgs.python3}/bin/python3 /etc/netsec-target.py server remote 18081; systemd-run --unit=target-granted ip netns exec outside ${pkgs.python3}/bin/python3 /etc/netsec-target.py server granted 18082")
    machine.wait_until_succeeds("curl --noproxy '*' -fsS http://127.0.0.1:18081/ | grep -qx local")
    machine.wait_until_succeeds("ip netns exec outside curl --noproxy '*' -fsS http://127.0.0.1:18081/ | grep -qx remote")
    machine.wait_until_succeeds("ip netns exec outside curl --noproxy '*' -fsS http://[::1]:18081/ | grep -qx remote")
    machine.wait_until_succeeds("ip netns exec outside curl --noproxy '*' -fsS http://127.0.0.1:18082/ | grep -qx granted")
    machine.wait_until_succeeds("ss -lnt | grep -q :33332")
    machine.succeed("curl --noproxy \"\" -x http://127.0.0.1:33332 -fsS http://127.0.0.1:18081/ | grep -qx local")
    ''}
    ${pkgs.lib.optionalString (ech != null) ''
    machine.succeed("${echFixture}/bin/ech-fixture prepare /run/netsec-ech; ip -n outside addr add 203.0.113.11/32 dev peer0; systemd-run --unit=ech-origin ip netns exec outside ${echFixture}/bin/ech-fixture serve /run/netsec-ech")
    machine.wait_for_file("/run/netsec-ech/ready")
    ''}
    machine.succeed("systemd-run --unit=control-dns ${pkgs.lib.optionalString (ech != null) "--property=StandardOutput=append:/run/netsec-ech/dns.log"} ip netns exec outside ${pkgs.python3}/bin/python3 /etc/netsec-dns.py server 15354 --dns-answer 203.0.113.10 ${pkgs.lib.optionalString (ech != null) "--ech-config /run/netsec-ech/dns.json"}")
    for port in (80, 8080):
        machine.succeed(f"systemd-run --unit=control-http-{port} ip netns exec outside ${pkgs.python3}/bin/python3 -m http.server {port} --bind 0.0.0.0")
    core = machine.succeed("cat /etc/netsec-core").strip()
    machine.succeed(f"systemd-run --unit=control-node ip netns exec outside {core} -d /run/netsec-node -f /run/netsec-node.json")
    machine.wait_until_succeeds("ip netns exec outside ss -lnt | grep -q :18080")
    machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = direct")
    ${pkgs.lib.optionalString strictDns ''
    machine.wait_until_succeeds("jq -e '.mode == \"degraded-dot-only\" and .encryption_policy == \"strict\" and .window_until == 0' /run/dns-upstream/state.json", timeout=90)
    # A root override and a forged, long-lived request cannot mint public DNS
    # permission. Even direct insertion into the old set has no accept rule.
    machine.succeed("touch /run/dns-upstream/force-plaintext; systemctl restart dns-upstream.service")
    machine.wait_until_succeeds("jq -e '.mode == \"degraded-dot-only\" and .plaintext_override == false and .window_until == 0' /run/dns-upstream/state.json", timeout=90)
    machine.succeed("systemctl stop dns-upstream.service; python3 /etc/netsec-dns.py lease 100.67.0.1 120; systemctl start dns-policy.service")
    machine.fail("nft get element inet lf_filter dns_plaintext4 '{ 100.67.0.1 }'")
    machine.succeed("systemd-run --unit=control-plain-dns ip netns exec outside ${pkgs.python3}/bin/python3 /etc/netsec-dns.py server 53")
    machine.wait_until_succeeds("ip netns exec outside ss -uln | grep -q ':53 '")
    machine.succeed("nft add element inet lf_filter dns_plaintext4 '{ 100.67.0.1 timeout 120s }'")
    machine.fail("python3 /etc/netsec-dns.py query 100.67.0.1 --uid 985")
    machine.fail("python3 /etc/netsec-dns.py query 100.67.0.1 --mark 6666")
    machine.succeed("nft add element inet lf_filter dns_split6 '{ ens1 . 2001:db8:dead::1 timeout 120s }'; python3 /etc/netsec-dns.py query 2001:db8:dead::1 --uid 985; nft flush set inet lf_filter dns_split6")
    drops = int(machine.succeed("nft -j list chain inet lf_filter lf_dns_guard | jq '[.nftables[].rule.expr[]? | .counter.packets // 0] | add'"))
    machine.fail("python3 /etc/netsec-dns.py query 2001:db8:dead::1 --mark 6666")
    assert int(machine.succeed("nft -j list chain inet lf_filter lf_dns_guard | jq '[.nftables[].rule.expr[]? | .counter.packets // 0] | add'")) > drops
    machine.succeed("ip link add tailscale0 type veth peer name tail-peer; ip link set tail-peer netns outside; ip addr add 100.100.100.99/24 dev tailscale0; ip link set tailscale0 up; ip -n outside addr add 100.100.100.100/24 dev tail-peer; ip -n outside link set tail-peer up")
    machine.succeed("python3 /etc/netsec-dns.py query 100.100.100.100 --mark 6666")
    # The MagicDNS address alone cannot exempt a physical-uplink query.
    machine.succeed("ip route add 100.100.100.100/32 via 100.67.0.1 dev ens1")
    machine.fail("python3 /etc/netsec-dns.py query 100.100.100.100 --mark 6666")
    machine.succeed("ip route del 100.100.100.100/32; ip link del tailscale0")
    # Explicit split permission remains usable in strict mode.
    machine.succeed("nft add element inet lf_filter dns_split4 '{ ens1 . 100.67.0.1 timeout 120s }'; python3 /etc/netsec-dns.py query 100.67.0.1 --uid 985")
    machine.succeed("nft flush set inet lf_filter dns_split4; nft flush set inet lf_filter dns_plaintext4; rm /run/dns-upstream/force-plaintext; systemctl start dns-upstream.service")
    # Literal HTTP portal access in A needs no public DNS.
    machine.succeed("setpriv --reuid=1000 --clear-groups curl --noproxy '*' -fsS --max-time 3 http://100.67.0.1:8080/ >/dev/null")
    ''}

    machine.succeed("python3 /etc/netsec-control.py refuse")
    machine.succeed("proxy-control on")
    machine.wait_for_file("/run/clash-verge-rev/service.sock")
    machine.succeed("python3 /etc/netsec-control.py start")
    wait_proxy()
    ${pkgs.lib.optionalString targetPolicy ''
    machine.succeed("python3 /etc/netsec-target.py check")
    machine.succeed("systemctl stop clash-verge.service")
    machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = blocked")
    machine.fail("setpriv --reuid=1000 --clear-groups curl --noproxy \"\" -x http://127.0.0.1:33332 -fsS --max-time 2 http://127.0.0.1:18081/")
    machine.succeed("systemctl start clash-verge.service")
    wait_proxy()
    ''}
    # Same suffix/exception rules at dnsmasq, direct core DNS and HTTP proxy
    # dialing, including a name otherwise exempt from the private-IP guard.
    for transport in ("+notcp", "+tcp"):
        for port in (1053, 1054):
            for name in ("ads.fixture.example", "pixel.ads.fixture.example", "metrics.home.arpa", "doubleclick.net"):
                response = machine.succeed(f"dig {transport} +time=2 +tries=1 @127.0.0.1 -p {port} {name} A")
                assert "status: NXDOMAIN" in response, (port, name, response)
            response = machine.succeed(f"dig {transport} +time=2 +tries=1 @127.0.0.1 -p {port} a.ok.blocked.fixture.test A")
            assert "status: NOERROR" in response and "198.18." in response, response
    for proxy in ("-x http://127.0.0.1:33332", "--proxytunnel -x http://127.0.0.1:33332", "--socks5-hostname 127.0.0.1:7897"):
        for name in ("ads.fixture.example", "pixel.ads.fixture.example", "metrics.home.arpa", "doubleclick.net"):
            machine.fail(f"curl --noproxy \"\" {proxy} -fsS --max-time 3 http://{name}:8080/")
        machine.succeed(f"curl --noproxy \"\" {proxy} -fsS --max-time 5 http://a.ok.blocked.fixture.test:8080/ >/dev/null")
    ${pkgs.lib.optionalString restricted ''
    # Both ports have real peers. The permitted native private connection
    # stays direct; the other cannot escape via the running proxy/TUN.
    machine.succeed("setpriv --reuid=1000 --clear-groups curl --noproxy '*' -fsS --max-time 3 http://100.67.0.1:8080/ >/dev/null")
    machine.fail("setpriv --reuid=1000 --clear-groups curl --noproxy '*' -fsS --max-time 2 http://100.67.0.1:80/")
    machine.fail("curl --noproxy '*' -fsS --max-time 2 http://100.67.0.1:8080/")
    ''}
    ${pkgs.lib.optionalString (ech != null) ''
    for port in (1053, 1054):
        for transport in ("+notcp", "+tcp"):
            machine.succeed(f"dig {transport} +time=2 +tries=1 @127.0.0.1 -p {port} accepted.ech.fixture.example HTTPS | grep -q 'ech='")
    try:
        machine.succeed("setpriv --reuid=lfour --regid=users --init-groups env HOME=/home/lfour XDG_RUNTIME_DIR=/run/user/1000 python3 /etc/netsec-ech.py")
    except Exception:
        print(machine.execute("cat /run/netsec-ech/events.jsonl; tail -n 40 /run/netsec-ech/dns.log; tail -n 40 /tmp/ech-firefox-*.log"))
        raise
    machine.succeed("systemctl stop clash-verge.service")
    machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = blocked")
    machine.fail("setpriv --reuid=lfour --regid=users --init-groups ${ech.transparent}/bin/firefox --check")
    machine.succeed("systemctl start clash-verge.service")
    wait_proxy()
    ''}
    # The production TUN must preserve Tailscale's pre-existing bypass route
    # and SO_MARK. Keep this short check beside the actual helper/core test.
    machine.succeed("ip rule add pref 5210 fwmark 0x80000/0xff0000 lookup main; ip -6 rule add pref 5210 fwmark 0x80000/0xff0000 lookup main; ip -n outside -6 addr add 2001:db8:ffff::10/128 dev peer0 nodad")
    # Bind each destination explicitly: a wildcard UDP fixture replies from
    # the peer's primary address, which is a different NEW input flow here.
    machine.succeed("systemd-run --unit=control-tail-peer ip netns exec outside ${pkgs.python3}/bin/python3 /etc/netsec-tail.py server")
    machine.wait_until_succeeds("ip netns exec outside ss -uln | grep -q ':42424 '")
    machine.succeed("nft add table inet tail_trace; nft 'add chain inet tail_trace output { type filter hook output priority -203; policy accept; }'; nft add rule inet tail_trace output udp dport 42424 meta nftrace set 1")
    machine.succeed("systemd-run --unit=tail-trace --property=StandardOutput=append:/tmp/tail-trace.log ${pkgs.nftables}/bin/nft monitor trace")
    try:
        for address in ("203.0.113.10", "2001:db8:ffff::10"):
            machine.succeed(f"python3 /etc/netsec-tail.py client {address} 42424 --mark 524288")
    except Exception:
        print(machine.execute("ip rule show; ip -6 rule show; ip route get 203.0.113.10 mark 0x80000; nft list chain inet lf_filter proxymode_drops; cat /tmp/tail-trace.log; ip netns exec outside ss -uln"))
        raise
    machine.succeed("systemctl stop tail-trace.service; nft delete table inet tail_trace")
    # Force a guarded recovery; the DNS/TUN probe and response sockets are now
    # owned by the actual service UID, never an independently tested setpriv.
    machine.succeed("${clashPackage.managedCore}/bin/mihomo -v; systemctl reload nftables.service")
    wait_proxy()

    machine.succeed("systemctl restart clash-verge.service")
    wait_proxy()
    machine.succeed("proxy-control off")
    machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = direct", timeout=45)
    ${pkgs.lib.optionalString targetPolicy ''
    machine.wait_until_succeeds("curl --noproxy \"\" -x http://127.0.0.1:33332 -fsS --max-time 5 http://127.0.0.1:18081/ | grep -qx local")
    ''}
    ${pkgs.lib.optionalString restricted ''
    # Incremental rules_off must remove B's guard, not require a full reload.
    machine.succeed("setpriv --reuid=1000 --clear-groups curl --noproxy '*' -fsS --max-time 3 http://100.67.0.1:80/ >/dev/null")
    machine.succeed("curl --noproxy '*' -fsS --max-time 3 http://100.67.0.1:8080/ >/dev/null")
    ''}
    machine.succeed("python3 /etc/netsec-control.py refuse")
    ${pkgs.lib.optionalString (ech != null) ''
    machine.succeed("setpriv --reuid=lfour --regid=users --init-groups env HOME=/home/lfour XDG_RUNTIME_DIR=/run/user/1000 python3 /etc/netsec-ech.py portal")
    machine.copy_from_vm("/tmp/ech-browser-results.json")
    machine.copy_from_vm("/run/netsec-ech/events.jsonl")
    machine.copy_from_vm("/run/netsec-ech/dns.log")
    ''}
  '';
}
