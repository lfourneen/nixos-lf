{ pkgs, clashPackage, restricted ? false, strictDns ? false, ech ? null, targetPolicy ? false, guests ? false, uniformProxy ? false }:

let
  dataplane = !(restricted || strictDns || targetPolicy || guests || uniformProxy || ech != null);
  echFixture = pkgs.runCommand "ech-origin-fixture" { nativeBuildInputs = [ pkgs.go ]; } ''
    export GOCACHE="$TMPDIR/go-cache"
    export GO111MODULE=off
    export CGO_ENABLED=0
    mkdir -p "$out/bin"
    go build -trimpath -o "$out/bin/ech-fixture" ${./ech_server.go}
  '';

in
pkgs.testers.runNixOSTest {
  name = if uniformProxy then (if ech != null then "browser-ech-uniform" else "proxy-uniform-policy") else if guests then "guest-trust-layers" else if targetPolicy then "proxy-target-authorization" else if ech != null then "browser-ech" else if strictDns then "strict-dns-policy" else if restricted then "restricted-egress-core" else "proxy-control-privileges";
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
      ../../system/programs/systemd/network-observe.nix
      ../../system/programs/clash/runtime.nix
    ] ++ lib.optional guests ./guest-config.nix
      ++ lib.optional (ech != null) ./firefox-desktop.nix;
    users.users.lfour = { isNormalUser = true; uid = 1000; };
    users.groups.impostor = { };
    users.users.impostor = { isSystemUser = true; uid = 1001; group = "impostor"; };
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
    my.proxy.proxyOnly = uniformProxy;
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
    environment.etc."netsec-dataplane.py".source = ./dataplane_fixture.py;
    environment.etc."network-benchmark.py".source = ../../scripts/network-benchmark.py;
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
    ${pkgs.lib.optionalString (ech != null) ''
    machine.wait_until_succeeds("test -S /run/user/1000/wayland-0 && test -S /run/user/1000/pulse/native", timeout=40)
    ''}
    machine.succeed("ip netns add outside; ip link add ens1 type veth peer name peer0; ip link set peer0 netns outside")
    machine.succeed("ip addr add 100.67.0.2/24 dev ens1; ip link set ens1 up; ip -n outside addr add 100.67.0.1/24 dev peer0; ip -n outside addr add 203.0.113.10/32 dev peer0; ip -n outside link set peer0 up; ip -n outside link set lo up; ip route add default via 100.67.0.1 dev ens1 metric 1")
    machine.succeed("ip -6 addr add 2001:db8:dead::2/64 dev ens1 nodad; ip -n outside -6 addr add 2001:db8:dead::1/64 dev peer0 nodad; ip -6 route add default via 2001:db8:dead::1 dev ens1 metric 1")
    machine.succeed("python3 /etc/netsec-integration.py prepare; python3 /etc/netsec-control.py prepare")
    ${pkgs.lib.optionalString guests ''
    machine.succeed("python3 /etc/netsec-guests.py prepare")
    ''}
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
    ${pkgs.lib.optionalString uniformProxy ''
    machine.succeed("systemd-run --unit=uniform-http6 ip netns exec outside ${pkgs.python3}/bin/python3 /etc/netsec-integration.py http6; systemd-run --unit=uniform-udp ip netns exec outside ${pkgs.python3}/bin/python3 /etc/netsec-dns.py server 15353")
    machine.wait_until_succeeds("ip netns exec outside ss -uln | grep -q :15353", timeout=20)
    ''}
    core = machine.succeed("cat /etc/netsec-core").strip()
    machine.succeed(f"systemd-run --unit=control-node ip netns exec outside {core} -d /run/netsec-node -f /run/netsec-node.json")
    machine.wait_until_succeeds("ip netns exec outside ss -lnt | grep -q :18080")
    machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = direct")
    ${pkgs.lib.optionalString guests ''
    machine.wait_for_unit("socket-guest-dns.service")
    try:
        machine.wait_until_succeeds("$(jq -r .listenerCheck /etc/netsec-guests.json) dns socket-guest-dns", timeout=60)
        machine.succeed("python3 /etc/netsec-guests.py direct")
    except Exception:
        print(machine.execute("nft list table inet lf_filter; nft list table ip lf_nat; nft list table ip6 lf_nat; ss -lneut; cat /run/dns-upstream/state.json; journalctl -u dns-upstream -u unbound -u socket-guest-dns -u guest-dns-0 --no-pager -n 10"))
        raise
    ''}
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

    ${pkgs.lib.optionalString dataplane ''
    with subtest("reserved listener ports and pre-handshake identity enforcement"):
        # Hold observers still: the dataplane must protect clients independently
        # of whether the next polling round has noticed a wrong listener.
        machine.succeed("systemctl stop proxy-mode.service gost-relay.service")
        machine.succeed("systemd-run --wait --pipe --slice=user.slice --uid=1000 python3 /etc/netsec-dataplane.py bind")
        port_range = machine.succeed("sysctl -n net.ipv4.ip_local_port_range").strip()
        reserved = machine.succeed("sysctl -n net.ipv4.ip_local_reserved_ports").strip()
        machine.succeed("sysctl -w 'net.ipv4.ip_local_port_range=33332 33333' net.ipv4.ip_local_reserved_ports=")
        machine.succeed("systemd-run --wait --pipe --slice=user.slice --uid=1000 python3 /etc/netsec-dataplane.py autobind allowed")
        machine.succeed(f"sysctl -w net.ipv4.ip_local_reserved_ports={reserved}")
        machine.succeed("systemd-run --wait --pipe --slice=user.slice --uid=1000 python3 /etc/netsec-dataplane.py autobind denied")
        machine.succeed(f"sysctl -w 'net.ipv4.ip_local_port_range={port_range}'")
        machine.succeed("systemd-run --wait --pipe --slice=user.slice --uid=1000 python3 /etc/netsec-dataplane.py autobind normal")
        # An untrusted system service deliberately outside user.slice still
        # cannot answer managed proxy endpoints through the UID reply guard.
        machine.succeed("nft -s list chain inet lf_filter lf_local_proxy_guard >/tmp/proxy-guard.nft; nft flush chain inet lf_filter lf_local_proxy_guard")
        machine.succeed("systemd-run --unit=port-impostor --uid=1001 python3 /etc/netsec-dataplane.py impostor")
        machine.wait_for_file("/tmp/impostor-ready", timeout=10)
        machine.succeed("setpriv --reuid=1000 --clear-groups python3 /etc/netsec-dataplane.py exchange 127.0.0.1 33332 tcp; test -s /tmp/impostor-received")
        machine.succeed("nft -f /tmp/proxy-guard.nft; rm /tmp/impostor-received")
        # A VM-only redirect exercises the transparent reply before reverse NAT.
        machine.succeed("nft add table inet test_redirect; nft 'add chain inet test_redirect output { type nat hook output priority -100; policy accept; }'; nft 'add rule inet test_redirect output oifname lo meta l4proto { tcp, udp } th dport 18092 redirect to :33333'")
        for address in ("127.0.0.1", "::1"):
            for port in (33332, 7897, 7896, 18092):
                for protocol in ("tcp", "udp"):
                    machine.fail(f"setpriv --reuid=1000 --clear-groups python3 /etc/netsec-dataplane.py exchange {address} {port} {protocol}")
        machine.succeed("test ! -e /tmp/impostor-received; systemctl stop port-impostor; nft delete table inet test_redirect; systemctl start gost-relay.service proxy-mode.service")
    ''}

    ${pkgs.lib.optionalString dataplane ''
    with subtest("read-only preflight and controlled direct baseline"):
        machine.succeed("python3 /etc/netsec-control.py preflight-rejection")
        machine.succeed("mv /home/lfour/.local/share/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml /tmp/preflight-profile.yaml")
        machine.fail("proxy-control preflight")
        machine.succeed("test $(jq -r .mode /run/proxy-mode/state.json) = direct; mv /tmp/preflight-profile.yaml /home/lfour/.local/share/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml")
        machine.succeed("proxy-control preflight | jq -e .ready; systemd-run --unit=benchmark-origin ip netns exec outside ${pkgs.python3}/bin/python3 /etc/network-benchmark.py serve --bind 0.0.0.0")
        machine.wait_until_succeeds("ip netns exec outside ss -lnt | grep -q :18094")
        machine.succeed("setpriv --reuid=1000 --clear-groups python3 /etc/network-benchmark.py measure --url http://203.0.113.10:18094 --samples 10 >/tmp/baseline-direct.json")
        machine.copy_from_vm("/tmp/baseline-direct.json")
    ''}
    ${pkgs.lib.optionalString dataplane ''
    machine.succeed("python3 /etc/netsec-control.py provider-setup && systemd-run --unit=provider-origin ip netns exec outside ${pkgs.python3}/bin/python3 -m http.server 18095 --bind 100.67.0.1 --directory /tmp/netsec-provider")
    machine.wait_until_succeeds("ip netns exec outside ss -lnt | grep -q :18095")
    ''}
    machine.succeed("python3 /etc/netsec-control.py refuse")
    machine.succeed("proxy-control on || { cat /run/proxy-mode/preflight.log; exit 1; }")
    machine.wait_for_file("/run/clash-verge-rev/service.sock")
    machine.succeed("python3 /etc/netsec-control.py start")
    wait_proxy()
    machine.succeed("setpriv --reuid=1000 --clear-groups proxy-status --json | jq -e '(.ready | type) == \"boolean\" and .mode == \"proxy\"'")
    machine.succeed("setpriv --reuid=1000 --clear-groups proxy-status --no-color | grep -q 'Network status'")
    ${pkgs.lib.optionalString dataplane ''
    with subtest("provider cache writes and rejected refresh preserve working inline routing"):
        machine.succeed("python3 /etc/netsec-control.py provider-cache-boundary")
        machine.wait_until_succeeds("python3 /etc/netsec-control.py provider-check")
        machine.succeed("python3 /etc/netsec-control.py provider-reject-refresh")
        machine.succeed("python3 /etc/netsec-control.py provider-check")
        machine.succeed("proxy-control status auto | jq -e '.mode == \"proxy\"'")
        machine.succeed("python3 /etc/netsec-control.py provider-good")
    ''}
    ${pkgs.lib.optionalString dataplane ''
    with subtest("controlled native and explicit proxy baselines"):
        machine.succeed("setpriv --reuid=1000 --clear-groups python3 /etc/network-benchmark.py measure --url http://203.0.113.10:18094 --samples 10 >/tmp/baseline-tun.json")
        machine.succeed("setpriv --reuid=1000 --clear-groups python3 /etc/network-benchmark.py measure --url http://203.0.113.10:18094 --proxy http://127.0.0.1:33332 --samples 10 >/tmp/baseline-http-proxy.json")
        machine.copy_from_vm("/tmp/baseline-tun.json")
        machine.copy_from_vm("/tmp/baseline-http-proxy.json")
        machine.succeed("python3 /etc/network-benchmark.py snapshot --tool ${../../system/programs/clash/clash-verge-stage.py} >/tmp/baseline-snapshot.json")
        machine.copy_from_vm("/tmp/baseline-snapshot.json")
        machine.succeed("proxy-inspect --sample-seconds 5 >/tmp/baseline-services.json")
        machine.copy_from_vm("/tmp/baseline-services.json")
    with subtest("validated TCP candidates survive an unreachable first address"):
        import json, time
        machine.succeed("ip -n outside addr add 203.0.113.11/32 dev peer0; ip -n outside -6 addr add 2001:db8:ffff::10/128 dev peer0 nodad")
        machine.succeed("systemd-run --unit=benchmark-origin6 ip netns exec outside ${pkgs.python3}/bin/python3 /etc/network-benchmark.py serve --bind 2001:db8:ffff::10")
        machine.succeed("ip netns exec outside nft add table inet candidate_fault; ip netns exec outside nft 'add chain inet candidate_fault input { type filter hook input priority -10; policy accept; }'; ip netns exec outside nft add rule inet candidate_fault input ip daddr 203.0.113.11 tcp dport 18094 counter drop")
        machine.succeed("systemctl stop control-node.service; systemd-run --unit=candidate-node ip netns exec outside ${pkgs.python3}/bin/python3 /etc/netsec-dataplane.py strict-socks")
        machine.wait_until_succeeds("ip netns exec outside ss -lnt | grep -q :18080")
        latencies = []
        for name in ("fallback.fixture.example", "fallback6.fixture.example"):
            for _ in range(5):
                start = time.monotonic()
                machine.succeed(f"setpriv --reuid=1000 --clear-groups curl --noproxy \"\" -x http://127.0.0.1:33332 --max-time 8 -fsS http://{name}:18094/1 >/dev/null")
                latencies.append(time.monotonic() - start)
        machine.fail("setpriv --reuid=1000 --clear-groups curl --noproxy \"\" -x http://127.0.0.1:33332 --max-time 8 -f http://fallback-mixed.fixture.example:18094/1")
        assert max(latencies) < 8, latencies
        print("candidate fault latencies:", json.dumps(latencies))
        machine.succeed("ip netns exec outside nft delete table inet candidate_fault; ip -n outside addr del 203.0.113.11/32 dev peer0; systemctl stop benchmark-origin6 candidate-node; ip -n outside -6 addr del 2001:db8:ffff::10/128 dev peer0")
        machine.succeed(f"systemd-run --unit=control-node ip netns exec outside {core} -d /run/netsec-node -f /run/netsec-node.json")
        machine.wait_until_succeeds("ip netns exec outside ss -lnt | grep -q :18080")
        wait_proxy()
    with subtest("existing native TUN and SOCKS UDP receive paths are revoked in C"):
        import json
        machine.succeed("ip -n outside -6 addr add 2001:db8:ffff::10/128 dev peer0 nodad; systemd-run --unit=receive-push ip netns exec outside ${pkgs.python3}/bin/python3 /etc/netsec-dataplane.py push")
        machine.wait_until_succeeds("ip netns exec outside ss -4uln | grep -q :18090; ip netns exec outside ss -6uln | grep -q :18090", timeout=10)
        fake = machine.succeed("dig +short @127.0.0.1 -p 1053 node.fixture.example A").strip()
        assert fake.startswith("198.18."), fake
        streams = [("native4", "203.0.113.10", "no"), ("fake4", fake, "no"),
                   ("native6", "2001:db8:ffff::10", "no"),
                   ("socks4", "203.0.113.10", "yes"), ("socks6", "2001:db8:ffff::10", "yes")]
        for label, address, socks in streams:
            machine.succeed(f"systemd-run --unit=receive-{label} --uid=1000 python3 /etc/netsec-dataplane.py receive {label} {address} {socks}")
            machine.wait_until_succeeds(f"test -f /tmp/receive-{label}.json && test $(jq -r .count /tmp/receive-{label}.json) -ge 5", timeout=20)
        def counts():
            return {label: json.loads(machine.succeed(f"cat /tmp/receive-{label}.json"))["count"] for label, _, _ in streams}
        # Only the health HTTP target fails. The real proxy node and its UDP
        # paths remain alive; the coordinator must enter C on its own.
        machine.succeed("systemctl stop control-http-8080.service")
        machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = blocked", timeout=90)
        machine.succeed("systemctl stop proxy-mode.service; sleep 1")
        before = counts()
        machine.sleep(3)
        assert counts() == before, (before, counts())
        machine.succeed("systemd-run --unit=local-ipc --uid=1000 python3 -m http.server 18093 --bind 127.0.0.1")
        machine.wait_until_succeeds("setpriv --reuid=1000 --clear-groups curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:18093/ >/dev/null", timeout=10)
        machine.succeed("curl --noproxy \"\" -x http://127.0.0.1:7897 -fsS --max-time 3 http://node.fixture.example/ >/dev/null; systemctl stop local-ipc.service")
        # Reproduce the previous output-only boundary in this isolated VM by
        # removing just the new app-reply rules, leaving all C output cuts.
        for chain in ("lf_tailnet_guard", "lf_blocked_guard"):
            machine.succeed(f"nft -s list chain inet lf_filter {chain} >/tmp/{chain}.nft")
            rules = json.loads(machine.succeed(f"nft -a -j list chain inet lf_filter {chain}"))["nftables"]
            for entry in rules:
                rule = entry.get("rule", {})
                if any(e.get("match", {}).get("right") in (0x4c460001, "0x4c460001") for e in rule.get("expr", [])):
                    machine.succeed(f"nft delete rule inet lf_filter {chain} handle {rule['handle']}")
        machine.sleep(3)
        legacy = counts()
        assert all(legacy[label] > before[label] for label, _, _ in streams), (before, legacy)
        print("output-only C kept receiving application UDP:", before, legacy)
        for chain in ("lf_tailnet_guard", "lf_blocked_guard"):
            machine.succeed(f"nft flush chain inet lf_filter {chain}; nft -f /tmp/{chain}.nft")
        machine.sleep(1)
        before = counts()
        machine.sleep(3)
        assert counts() == before, (before, counts())
        # Local IPC and automatic root/core health recovery are preserved.
        machine.succeed("systemd-run --unit=control-http-8080 ip netns exec outside ${pkgs.python3}/bin/python3 -m http.server 8080 --bind 0.0.0.0; systemctl start proxy-mode.service")
        wait_proxy()
        machine.sleep(2)
        assert all(counts()[label] > before[label] for label, _, _ in streams), (before, counts())
        for label, _, _ in streams:
            machine.succeed(f"systemctl stop receive-{label}.service")
        machine.succeed("systemctl stop receive-push.service; ip -n outside -6 addr del 2001:db8:ffff::10/128 dev peer0")
    ''}
    ${pkgs.lib.optionalString uniformProxy ''
    machine.fail("curl --noproxy \"\" -x http://127.0.0.1:33332 -fsS --max-time 3 http://application-direct.fixture.example:8080/")
    machine.succeed("curl --noproxy \"\" -x http://127.0.0.1:33332 -fsS --max-time 5 http://node.fixture.example:8080/ >/dev/null")
    machine.succeed("setpriv --reuid=1000 --clear-groups curl --noproxy '*' -fsS --max-time 5 http://[2001:db8:dead::1]:8080/ >/dev/null && python3 /etc/netsec-integration.py uniform-udp-direct && python3 /etc/netsec-integration.py uniform-udp")
    ''}
    ${pkgs.lib.optionalString guests ''
    machine.succeed("python3 /etc/netsec-guests.py proxy")
    machine.succeed("systemctl unmask --runtime proxy-mode.service; systemctl start proxy-mode.service")
    wait_proxy()
    ''}
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

    # Explicit historical cleanup uses that same guarded transaction. Observe C
    # before health may reopen B, while the actual core and its TUN table survive.
    machine.succeed("systemctl stop proxy-mode.service; nft -s -j list table inet mihomo >/tmp/mihomo-before.json; nft add table inet filter; nft add table ip proxymode_nat; nft add table ip6 proxymode_nat; proxy-control migrate-firewall")
    machine.succeed("jq -e '.mode == \"blocked\"' /run/proxy-mode/state.json; nft list chain inet lf_filter lf_blocked_guard | grep -q drop; nft -s -j list table inet mihomo >/tmp/mihomo-after.json; cmp /tmp/mihomo-before.json /tmp/mihomo-after.json; python3 /etc/netsec-control.py check")
    for family, name in [("inet", "filter"), ("ip", "proxymode_nat"), ("ip6", "proxymode_nat")]:
        machine.fail(f"nft list table {family} {name}")
    machine.fail("setpriv --reuid=1000 --clear-groups curl --noproxy \"\" -x http://127.0.0.1:7897 -fsS --max-time 2 http://a.ok.blocked.fixture.test:8080/")
    machine.succeed("systemctl start proxy-mode.service")
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
