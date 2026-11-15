{ pkgs, clashPackage, restricted ? false, strictDns ? false, ech ? null, targetPolicy ? false, guests ? false, uniformProxy ? false, refresh ? false, hermesContainers ? false, managedSources ? false, assurance ? false }:

let
  dataplane = !(refresh || restricted || strictDns || targetPolicy || guests || uniformProxy || managedSources || assurance || ech != null);
  maskSupervisor = "mkdir -p /run/systemd/system.control; ln -s /dev/null /run/systemd/system.control/proxy-mode.service; systemctl daemon-reload; systemctl stop proxy-mode.service; test $(systemctl show -p LoadState --value proxy-mode.service) = masked";
  unmaskSupervisor = "unlink /run/systemd/system.control/proxy-mode.service; systemctl daemon-reload";
  sourceTls = import ../../home/firefox/tls-fixture.nix {
    inherit pkgs;
    extraDnsNames = [ "source.fixture.example" "dot.fixture.test" ];
    extraIpAddresses = pkgs.lib.optional assurance "203.0.113.53";
  };
  echFixture = pkgs.runCommand "ech-origin-fixture" { nativeBuildInputs = [ pkgs.go ]; } ''
    export GOCACHE="$TMPDIR/go-cache"
    export GO111MODULE=off
    export CGO_ENABLED=0
    mkdir -p "$out/bin"
    go build -trimpath -o "$out/bin/ech-fixture" ${../../home/firefox/ech_server.go}
  '';

in
pkgs.testers.runNixOSTest {
  name = if assurance then "proxy-authorization-and-dnssec" else if managedSources then "managed-core-namespace" else if hermesContainers then "hermes-container-network" else if refresh then "managed-profile-refresh" else if uniformProxy then (if ech != null then "browser-ech-uniform" else "proxy-uniform-policy") else if guests then "guest-trust-layers" else if targetPolicy then "proxy-target-authorization" else if ech != null then "browser-ech" else if strictDns then "strict-dns-policy" else if restricted then "restricted-egress-core" else "proxy-control-privileges";
  node.pkgs = pkgs.lib.mkForce (pkgs.extend (_final: prev: { unstable = prev; }));
  nodes.machine = { config, lib, pkgs, ... }: {
    imports = [
      ../../../system/config/machine.nix
      ../../../system/config/network/options.nix
      ../../../system/config/network/proxy/options.nix

      ({ config, lib, ... }: let
        profile = import ../../../system/config/network/proxy/profile.nix { inherit config lib; };
      in profile // {
        options.my.proxy = profile.options.my.proxy // {
          managedDnsTransport = profile.options.my.proxy.managedDnsTransport // {
            default = let original = profile.options.my.proxy.managedDnsTransport.default;
            in original // lib.optionalAttrs assurance {
              dns = original.dns // {
                nameserver = [ "https://203.0.113.53/dns-query#Fixture" ];
                default-nameserver = [ "tls://203.0.113.53:853" ];
                proxy-server-nameserver = [ "https://203.0.113.53/dns-query#DIRECT" ];
              };
            };
          };
          managedDns = profile.options.my.proxy.managedDns // {
            default = profile.options.my.proxy.managedDns.default // lib.optionalAttrs (!assurance) {
              nameserver = [ "udp://100.67.0.1:15354" ];
              default-nameserver = [ "100.67.0.1" ];
              proxy-server-nameserver = [ "udp://100.67.0.1:15354" ];
              nameserver-policy = { };
              respect-rules = false;
            };
          };
        };
      })

      ../../../system/config/network/network.nix
      ../../../system/config/network/dns/blocklist.nix
      ../../../system/config/network/proxy/coordinator.nix
      ../../../system/config/network/dns/upstream.nix
      ../../../system/config/network/firewall/alert.nix
      ../../../system/config/network/firewall/verify.nix
      ../../../system/programs/gost/default.nix
      ../../../system/config/network/diagnostics/default.nix
      ../../../system/programs/clash/service/runtime.nix
    ] ++ lib.optional assurance (import ../dns/assurance-config.nix { inherit sourceTls; })
      ++ lib.optional guests ../guests/guest-config.nix
      ++ lib.optional hermesContainers ../guests/hermes-container-config.nix
      ++ lib.optional (ech != null) ../../home/firefox/firefox-desktop.nix;
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

    my.proxy.profileRefreshPolicy = if refresh || managedSources then "auto" else "strict";
    my.proxy.authorizationLeaseSeconds = lib.mkIf managedSources 60;
    my.proxy.subscriptionAudit = if refresh || managedSources then "enforce" else "warn";
    my.proxy.controlledSources = managedSources;
    my.proxy.strictBrowser = managedSources;
    my.proxy.subscriptionTrust = lib.mkIf managedSources "fixed";
    my.proxy.remoteOnlyCatchAll = managedSources;
    my.proxy.nodeProbeUrls = [ "http://node.fixture.example:8080/" ];
    my.proxy.tunProbeName = "tun.fixture.example";
    my.proxy.dnsEncryption = if strictDns || managedSources || assurance then "strict" else "opportunistic";
    security.pki.certificateFiles = lib.optional (managedSources || assurance) "${sourceTls}/ca.pem";
    services.unbound.settings.server.local-zone = lib.mkIf managedSources [
      ''"source.fixture.example." static''
      ''"source-node.fixture.example." static''
      ''"source-node-updated.fixture.example." static''
    ];
    services.unbound.settings.server.local-data = lib.mkIf managedSources [
      ''"source.fixture.example. 60 IN A 1.1.1.1"''
      ''"source-node.fixture.example. 60 IN A 1.1.1.1"''
      ''"source-node-updated.fixture.example. 60 IN A 1.1.1.1"''
    ];
    systemd.timers.clash-source-refresh.wantedBy = lib.mkIf managedSources (lib.mkForce [ ]);
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
    systemd.services.namespace-sleep-hold = lib.mkIf dataplane {
      requires = [ "sleep.target" ];
      after = [ "sleep.target" ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; ExecStart = "${pkgs.coreutils}/bin/true"; };
    };

    environment.etc."netsec-integration.py".source = ../integration/integration_fixture.py;
    environment.etc."netsec-dns.py".source = ../dns/dns_fixture.py;
    environment.etc."netsec-tail.py".source = ../tailscale/tailscale_fixture.py;
    environment.etc."netsec-integration-policy.json".text = builtins.toJSON config.my.proxy.managedProfile;
    environment.etc."netsec-control.py".source = ./control_fixture.py;
    environment.etc."netsec-core-network.py".source = ./core_network_fixture.py;
    environment.etc."netsec-managed.py" = lib.mkIf managedSources { source = ./managed_netns_fixture.py; };

    environment.etc."netsec-managed.json" = lib.mkIf managedSources {
      text = builtins.toJSON { tls = sourceTls; stage = ../../../system/programs/clash/service/clash-verge-stage.py;
        stageSettings = clashPackage.stageSettings; launcher = "${clashPackage}/bin/verge-mihomo"; };
    };

    environment.etc."netsec-refresh.py".source = ./refresh_fixture.py;
    environment.etc."netsec-dataplane.py".source = ./dataplane_fixture.py;
    environment.etc."netsec-resilience.py" = lib.mkIf dataplane { source = ./resilience_fixture.py; };
    environment.etc."netsec-health.py" = lib.mkIf dataplane { source = ../../../system/config/network/proxy/health.py; };
    environment.etc."netsec-health.json" = lib.mkIf dataplane { source = config.my.proxy.healthSettings; };
    environment.etc."netsec-blocked.nft" = lib.mkIf dataplane { text = config.my.proxy.blockedModeRules; };
    environment.etc."netsec-lifecycle.py" = lib.mkIf dataplane { source = ../integration/lifecycle_fixture.py; };

    environment.etc."netsec-nm-wake" = lib.mkIf dataplane {
      source = (lib.findFirst (hook: lib.hasSuffix "-nm-proxy-net-wake" (toString hook.source))
        (throw "production NM wake hook not found") config.networking.networkmanager.dispatcherScripts).source;
    };

    environment.etc."network-benchmark.py".source = ../../../scripts/network-benchmark.py;
    environment.etc."netsec-target.py" = lib.mkIf targetPolicy { source = ./target_fixture.py; };
    environment.etc."netsec-launcher".text = "${clashPackage}/bin/verge-mihomo";
    environment.etc."netsec-core".text = "${clashPackage.managedCore}/bin/mihomo";
    environment.etc."netsec-firefox-driver.py" = lib.mkIf (ech != null) { source = ../../home/firefox/firefox_driver.py; };
    environment.etc."netsec-ech.py" = lib.mkIf (ech != null) { source = ../../home/firefox/test_ech.py; };

    environment.etc."netsec-ech.json" = lib.mkIf (ech != null) {
      text = builtins.toJSON (lib.mapAttrs (_: firefox: {
        package = firefox;
        settings = firefox.netsec.settings;
        guard = ../../../home/programs/firefox/guard.py;
      }) ech);
    };

    environment.systemPackages = [
      (pkgs.python3.withPackages (p: [ p.pyyaml ] ++ lib.optional assurance p.dnspython))
      pkgs.curl pkgs.jq pkgs.dnsutils pkgs.iproute2 pkgs.nftables pkgs.util-linux
    ] ++ lib.optionals (ech != null) [ pkgs.nssTools ]
      ++ lib.optionals dataplane [ pkgs.conntrack-tools ];
    virtualisation.memorySize = if ech != null then 3072 else if managedSources then 2048 else 1024;
  };

  testScript = ''
    import json

    def drop_view():
        value = json.loads(machine.succeed("proxy-inspect --drops"))
        assert value["available"] and value["state_consistent"], value
        return value

    def drop_packets(value, reason):
        groups = [group for group in value["groups"] if group["reason"] == reason]
        assert len(groups) == 1 and groups[0]["installed"], groups
        return groups[0]["packets"]

    def wait_proxy():
        try:
            machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = proxy && test $(jq -r .mode /run/dns-upstream/state.json) = proxy && python3 /etc/netsec-control.py check && proxy-control status proxy --check", timeout=90)
        except Exception:
            print(machine.execute("proxy-control status proxy; cat /run/netsec/state.json /run/gost-relay/state.json /run/dns-policy/state.json; journalctl -u nftables-verify --no-pager -n 30"))
            print(machine.execute("python3 /etc/netsec-core-network.py diagnose"))
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
    ${pkgs.lib.optionalString assurance ''
    machine.succeed("ip -n outside -6 addr add fd42:96::1/64 dev peer0 nodad; ip -6 addr add fd42:96::2/64 dev ens1 nodad; ip -n outside addr add 203.0.113.42/32 dev peer0; ip -n outside addr add 203.0.113.53/32 dev peer0")
    machine.succeed("systemctl start netsec-assurance-dns.service")
    ''}
    machine.succeed("python3 /etc/netsec-integration.py prepare")
    ${pkgs.lib.optionalString assurance ''
    machine.succeed("python3 /etc/netsec-assurance.py configure")
    ''}
    machine.succeed("python3 /etc/netsec-control.py prepare")
    ${pkgs.lib.optionalString managedSources ''
    machine.succeed("ip -n outside addr add 1.1.1.1/32 dev peer0")
    machine.succeed("systemd-run --unit=managed-source-origin ip netns exec outside ${pkgs.python3}/bin/python3 /etc/netsec-managed.py source")
    machine.wait_for_file("/run/managed-source-ready", timeout=30)
    machine.succeed("python3 /etc/netsec-managed.py setup", timeout=90)
    ''}
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
    ${pkgs.lib.optionalString assurance ''
    machine.wait_for_unit("netsec-assurance-dns.service")
    try:
        print(machine.succeed("python3 -u /etc/netsec-assurance.py direct", timeout=30))
    except Exception:
        print(machine.execute("journalctl -u unbound -u netsec-assurance-dns --no-pager -n 30; nft list chain inet lf_filter lf_dns_egress_guard"))
        raise
    ''}
    ${pkgs.lib.optionalString (guests && !hermesContainers) ''
    machine.wait_for_unit("socket-guest-dns.service")
    try:
        machine.wait_until_succeeds("$(jq -r .listenerCheck /etc/netsec-guests.json) dns socket-guest-dns", timeout=60)
        machine.succeed("python3 /etc/netsec-guests.py direct")
    except Exception:
        print(machine.execute("nft list table inet lf_filter; nft list table ip lf_nat; nft list table ip6 lf_nat; ss -lneut; cat /run/dns-upstream/state.json; journalctl -u dns-upstream -u unbound -u socket-guest-dns -u guest-dns-0 --no-pager -n 10"))
        raise
    ''}
    ${pkgs.lib.optionalString hermesContainers ''
    with subtest("retire the old Hermes Docker and use a separately authorized backend"):
        try:
            machine.succeed("python3 /etc/hermes-container.py setup", timeout=150)
            machine.succeed("python3 /etc/hermes-container.py A", timeout=90)
        except Exception:
            print(machine.execute("journalctl -u hermes-containers -u hermes-container-retire --no-pager -n 100"))
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
        identity_before = drop_packets(drop_view(), "proxy-listener-identity")
        # A VM-only redirect exercises the transparent reply before reverse NAT.
        machine.succeed("nft add table inet test_redirect; nft 'add chain inet test_redirect output { type nat hook output priority -100; policy accept; }'; nft 'add rule inet test_redirect output oifname lo meta l4proto { tcp, udp } th dport 18092 redirect to :33333'")
        for address in ("127.0.0.1", "::1"):
            for port in (33332, 7897, 7896, 18092):
                for protocol in ("tcp", "udp"):
                    machine.fail(f"setpriv --reuid=1000 --clear-groups python3 /etc/netsec-dataplane.py exchange {address} {port} {protocol}")
        assert drop_packets(drop_view(), "proxy-listener-identity") > identity_before
        machine.succeed("proxy-inspect --drops >/tmp/drop-identity.json")
        machine.copy_from_vm("/tmp/drop-identity.json")
        machine.succeed("test ! -e /tmp/impostor-received; systemctl stop port-impostor; nft delete table inet test_redirect; systemctl start gost-relay.service proxy-mode.service")
    ''}

    ${pkgs.lib.optionalString dataplane ''
    with subtest("NM callbacks return while their wake target waits for network-online"):
        machine.succeed("python3 /etc/netsec-lifecycle.py /etc/netsec-nm-wake", timeout=90)
        machine.succeed("jq -e '.mode == \"direct\"' /run/proxy-mode/state.json")
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
    ${pkgs.lib.optionalString dataplane ''
    with subtest("the production core cannot obtain host network administration"):
        machine.succeed("python3 /etc/netsec-core-network.py confinement", timeout=60)
        wait_proxy()
    with subtest("authorized localhost TCP/UDP retain IPv4 and IPv6 host behavior"):
        machine.succeed("systemd-run --unit=namespace-localhost python3 /etc/netsec-core-network.py localhost-server")
        machine.wait_until_succeeds("ss -4uln | grep -q :18100; ss -6uln | grep -q :18100")
        try:
            machine.succeed("ip netns exec lf-clash-core python3 /etc/netsec-core-network.py localhost-probe")
            machine.succeed("python3 /etc/netsec-core-network.py broker-denials", timeout=60)
        except Exception:
            print(machine.execute("journalctl -u clash-core-loopback -u clash-core-localhost --no-pager -n 40; nft list chain inet lf_filter lf_core_input; nft list chain ip lf_nat lf_core_loopback; ip netns exec lf-clash-core nft list table ip lf_core_inlets; ip netns exec lf-clash-core nft list table ip6 lf_core_inlets"))
            print(machine.execute("nft add table inet localtrace; nft 'add chain inet localtrace pre { type filter hook prerouting priority -311; policy accept; }'; nft 'add rule inet localtrace pre iifname Mihomo meta nftrace set 1'; timeout 5 nft monitor trace >/tmp/localtrace & ip netns exec lf-clash-core python3 /etc/netsec-core-network.py localhost-probe; sleep 2; cat /tmp/localtrace"))
            raise
        machine.succeed("systemctl stop proxy-mode.service; nft -f /etc/netsec-blocked.nft")
        machine.succeed("ip netns exec lf-clash-core python3 /etc/netsec-core-network.py localhost-probe")
        machine.fail("setpriv --reuid=1000 --clear-groups curl --noproxy \"\" -x http://127.0.0.1:33332 --max-time 2 -fsS http://127.0.0.1:18098/")
        machine.succeed("systemctl start proxy-mode.service")
        wait_proxy()
        machine.succeed("systemctl stop namespace-localhost.service")
    with subtest("a new LAN or VPN route overlap refuses readiness and recovers after removal"):
        for family, prefix in ((4, "10.255.253.0/29"), (6, "fd46:6c66:636f::/63")):
            machine.succeed(f"ip -{family} route add {prefix} dev ens1")
            machine.wait_until_succeeds("jq -e '.mode == \"blocked\"' /run/proxy-mode/state.json", timeout=35)
            machine.succeed(f"ip -{family} route del {prefix} dev ens1")
            wait_proxy()
    with subtest("physical address renewal refreshes namespace transports without a new import grant"):
        previous_core = machine.succeed("pgrep -u clash-core -f '/[.]verge-mihomo-wrapped'").strip()
        # Removing the last address also removes the gateway route. Model the
        # complete DHCP renewal, including its replacement default route.
        machine.succeed("ip addr del 100.67.0.2/24 dev ens1; ip addr add 100.67.0.3/24 dev ens1; ip route replace default via 100.67.0.1 dev ens1 metric 1")
        machine.wait_until_succeeds(f"test $(pgrep -u clash-core -f '/[.]verge-mihomo-wrapped') != {previous_core}", timeout=35)
        wait_proxy()
        changed_core = machine.succeed("pgrep -u clash-core -f '/[.]verge-mihomo-wrapped'").strip()
        machine.succeed("ip addr del 100.67.0.3/24 dev ens1; ip addr add 100.67.0.2/24 dev ens1; ip route replace default via 100.67.0.1 dev ens1 metric 1")
        machine.wait_until_succeeds(f"test $(pgrep -u clash-core -f '/[.]verge-mihomo-wrapped') != {changed_core}", timeout=35)
        wait_proxy()
    with subtest("missing physical default routes cannot reflect core transports back into the namespace"):
        # Isolate the routing/fallback contract from the separately tested
        # uplink restart hook while deliberately withdrawing a default route.
        machine.succeed("systemctl stop clash-core-uplink-watch.service")
        for family, address, source, gateway, metric in (
            (4, "203.0.113.10", "10.255.253.2", "100.67.0.1", 1),
            (6, "2001:db8:ffff::10", "fd46:6c66:636f::2", "2001:db8:dead::1", 1024),
        ):
            machine.succeed(f"ip -{family} route del default via {gateway} dev ens1")
            machine.fail(f"ip -{family} route get {address} from {source} iif Mihomo")
            if family == 4:
                # The primary fake-IP target selects DIRECT and now fails.
                # The fallback uses the still-reachable private SOCKS node;
                # every request must retain its exact source-port log proof.
                for _ in range(5):
                    machine.succeed("python3 /etc/netsec-health.py /etc/netsec-health.json", timeout=16)
            machine.succeed(f"ip -{family} route replace default via {gateway} dev ens1 metric {metric}")
            selected = json.loads(machine.succeed(f"ip -j -{family} route get {address} from {source} iif Mihomo"))
            assert selected[0]["dev"] == "ens1", selected
        machine.succeed("systemctl start clash-core-uplink-watch.service")
        wait_proxy()
    with subtest("private endpoint loss cannot move the core inlet onto a LAN"):
        # nsfs may reuse an inode after the last reference is released. A
        # private object must disappear on reconstruction regardless of reuse.
        machine.succeed("ip netns exec lf-clash-core nft add table inet namespace_sentinel")
        machine.succeed("python3 /etc/netsec-core-network.py lost-peer", timeout=40)
        machine.wait_until_succeeds("jq -e '.mode == \"blocked\"' /run/proxy-mode/state.json", timeout=45)
        machine.succeed("proxy-control on")
        machine.wait_for_file("/run/clash-verge-rev/service.sock")
        machine.succeed("python3 /etc/netsec-control.py start")
        wait_proxy()
        machine.fail("ip netns exec lf-clash-core nft list table inet namespace_sentinel")
    with subtest("the real resume hook renews core transports without importing a new profile"):
        before_sleep = machine.succeed("pgrep -u clash-core -f '/[.]verge-mihomo-wrapped'").strip()
        machine.succeed("systemctl start namespace-sleep-hold.service; test -e /run/dns-policy/sleeping")
        machine.succeed("systemctl stop namespace-sleep-hold.service")
        machine.wait_until_succeeds("test ! -e /run/dns-policy/sleeping")
        machine.wait_until_succeeds(f"test $(pgrep -u clash-core -f '/[.]verge-mihomo-wrapped') != {before_sleep}", timeout=35)
        wait_proxy()
    ''}
    machine.succeed("setpriv --reuid=1000 --clear-groups proxy-status --json | jq -e '(.ready | type) == \"boolean\" and .mode == \"proxy\"'")
    ${pkgs.lib.optionalString managedSources ''
    with subtest("source refresh uses real filtered DNS while B returns fake-IP"):
        print(machine.succeed("python3 -u /etc/netsec-managed.py refresh", timeout=90))
        wait_proxy()
    with subtest("registered source, actual strict inlet and kernel C revocation share the production namespace"):
        try:
            print(machine.succeed("python3 -u /etc/netsec-managed.py recover", timeout=180))
            wait_proxy()
            print(machine.succeed("python3 -u /etc/netsec-managed.py strict", timeout=120))
            wait_proxy()
            print(machine.succeed("python3 -u /etc/netsec-managed.py switch", timeout=120))
            wait_proxy()
        except Exception:
            print(machine.execute("journalctl -u clash-strict -u clash-source-select --no-pager -n 45"))
            raise
    ''}
    ${pkgs.lib.optionalString hermesContainers ''
    machine.succeed("python3 /etc/hermes-container.py B", timeout=90)
    machine.succeed("systemctl stop clash-verge.service")
    machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = blocked", timeout=45)
    machine.succeed("python3 /etc/hermes-container.py C", timeout=90)
    machine.succeed("systemctl start clash-verge.service")
    machine.wait_for_file("/run/clash-verge-rev/service.sock")
    machine.succeed("python3 /etc/netsec-control.py start")
    wait_proxy()
    machine.succeed("python3 /etc/hermes-container.py B", timeout=90)
    ''}
    ${pkgs.lib.optionalString assurance ''
    with subtest("local DNSSEC, isolated DNS budgets and expiring kernel authorization"):
        try:
            print(machine.succeed("python3 -u /etc/netsec-assurance.py dns", timeout=90))
            print(machine.succeed("ip netns exec lf-clash-core python3 -u /etc/netsec-assurance.py pressure", timeout=60))
            wait_proxy()
            print(machine.succeed("python3 -u /etc/netsec-assurance.py lease", timeout=120))
        except Exception:
            print(machine.execute("journalctl -u clash-dns-application -u clash-dns-infrastructure -u netsec-assurance-dns -u proxy-mode --no-pager -n 50; cat /run/proxy-mode/health.json /run/proxy-mode/authorization.json; nft list set inet lf_filter b_authorization"))
            raise
        wait_proxy()
        machine.succeed("proxy-control off")
        machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = direct")
    ''}
    ${pkgs.lib.optionalString (!hermesContainers && !managedSources && !assurance) ''
    machine.succeed("setpriv --reuid=1000 --clear-groups proxy-status --no-color | grep -q 'Network status'")
    ${pkgs.lib.optionalString (refresh || dataplane) ''
    with subtest("managed profile refresh and core recovery"):
        try:
            print(machine.succeed("python3 -u /etc/netsec-refresh.py ${if refresh then "auto" else "strict"}", timeout=450))
        except Exception:
            # All data in this VM is synthetic. Retain authority, process and
            # private launcher evidence when recovery fails, before teardown.
            print(machine.execute("cat /run/proxy-mode/state.json /run/proxy-mode/health.json /run/proxy-mode/profile-refresh.json /run/proxy-mode/profile-refresh-restart.json"))
            print(machine.execute("tail -n 100 /var/lib/clash-verge-service/logs/service_latest.log"))
            print(machine.execute("ps -eo pid,ppid,stat,wchan:32,args"))
            raise
        wait_proxy()
    ''}
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
        machine.succeed("python3 /etc/network-benchmark.py snapshot --tool ${../../../system/programs/clash/service/clash-verge-stage.py} >/tmp/baseline-snapshot.json")
        machine.copy_from_vm("/tmp/baseline-snapshot.json")
        machine.succeed("proxy-inspect --sample-seconds 5 >/tmp/baseline-services.json")
        machine.copy_from_vm("/tmp/baseline-services.json")
    with subtest("bounded single-UID pressure and conntrack exhaustion never select direct"):
        try:
            print(machine.succeed("python3 -u /etc/netsec-resilience.py pressure", timeout=240))
        except Exception:
            print(machine.execute("cat /tmp/load-*.json; cat /run/proxy-mode/state.json /run/proxy-mode/health.json; journalctl -u pressure-syn -u pressure-slow -u pressure-udp -u pressure-dns -u pressure-conntrack --no-pager -n 40"))
            raise
        wait_proxy()
        machine.copy_from_vm("/tmp/pressure-report.json")
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
        with subtest("TCP remains healthy during UDP, IPv6-only and SOCKS-reply outages"):
            print(machine.succeed("python3 -u /etc/netsec-resilience.py outages", timeout=120))
            machine.copy_from_vm("/tmp/udp-diagnostics.json")
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
    machine.copy_from_vm("/run/netsec-guests/drop-observations.json")
    machine.succeed("${unmaskSupervisor}; systemctl start proxy-mode.service")
    wait_proxy()
    with subtest("DNS client refusal has a bounded passive diagnostic"):
        dns_before = drop_view()
        machine.fail("python3 /etc/netsec-dns.py query 127.0.0.1 1053 --uid 1000")
        machine.succeed("setpriv --reuid=1000 --clear-groups python3 /etc/netsec-guests.py dns-refusal-burst")
        dns_after = drop_view()
        assert dns_after["policy"]["mode"] == "proxy", dns_after
        assert drop_packets(dns_after, "dns-client-policy") >= drop_packets(dns_before, "dns-client-policy") + 2048
        assert len(dns_after["groups"]) == len(dns_before["groups"])
        assert len(json.dumps(dns_after)) < len(json.dumps(dns_before)) + 256
        machine.succeed("proxy-inspect --drops >/tmp/drop-dns.json")
        machine.copy_from_vm("/tmp/drop-dns.json")
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
    ${pkgs.lib.optionalString dataplane ''
    with subtest("missing managed TUN blocks a live core and recovers without changing intent"):
        # Keep the real core and its TUN FD alive. Hiding the configured name
        # isolates missing-interface handling from the existing core-gone case.
        # SIGSTOP prevents the core racing our deliberate link manipulation.
        core_pid = machine.succeed("pgrep -u clash-core -f '/[.]verge-mihomo-wrapped'").strip()
        assert core_pid.isdigit(), core_pid
        machine.succeed("systemd-run --unit=tun-loss-http6 ip netns exec outside ${pkgs.python3}/bin/python3 /etc/netsec-integration.py http6")
        try:
            machine.wait_until_succeeds("ip netns exec outside ss -6lnt | grep -q ':8080 '", timeout=15)
        except Exception:
            print(machine.execute("systemctl status tun-loss-http6.service --no-pager; journalctl -u tun-loss-http6.service --no-pager -n 30; ip netns exec outside ss -6lnt"))
            raise
        def tun_loss_requests():
            fake = machine.succeed("dig +short @127.0.0.1 -p 1053 node.fixture.example A").strip()
            assert fake.startswith("198.18."), fake
            requests = [f"setpriv --reuid=1000 --clear-groups curl --noproxy '*' -fsS --max-time 3 http://{address}:8080/"
                        for address in ("203.0.113.10", "[2001:db8:dead::1]", fake, "100.67.0.1")]
            requests.append("setpriv --reuid=1000 --clear-groups curl --noproxy \"\" -x http://127.0.0.1:33332 -fsS --max-time 3 http://node.fixture.example:8080/")
            return requests
        requests = tun_loss_requests()
        # Every denied probe must first reach a live origin in healthy B.
        for command in requests:
            machine.succeed(command + " >/dev/null")
        machine.succeed(f"kill -STOP {core_pid}; ip -n lf-clash-core link set dev Mihomo down; ip -n lf-clash-core link set dev Mihomo name lost-tun")
        try:
            machine.fail("ip -n lf-clash-core link show dev Mihomo")
            machine.wait_until_succeeds("jq -e '.mode == \"blocked\" and .reason == \"no-tun\"' /run/proxy-mode/state.json", timeout=45)
            machine.succeed(f"kill -0 {core_pid}; grep -qx proxy /run/proxy-mode/intent")
            for command in requests:
                machine.fail(command)
        finally:
            machine.succeed(f"ip -n lf-clash-core link set dev lost-tun name Mihomo; ip -n lf-clash-core link set dev Mihomo up; kill -CONT {core_pid}")
        # Link-down removes kernel routes. Renaming/up alone is insufficient;
        # retain C until the production helper rebuilds the core's TUN/routes.
        machine.succeed("jq -e '.mode == \"blocked\"' /run/proxy-mode/state.json; systemctl restart clash-verge.service", timeout=45)
        wait_proxy()
        machine.succeed("grep -qx proxy /run/proxy-mode/intent")
        assert machine.succeed("pgrep -u clash-core -f '/[.]verge-mihomo-wrapped'").strip() != core_pid
        # A restarted core may allocate a fresh fake-IP for the same domain.
        for command in tun_loss_requests():
            machine.succeed(command + " >/dev/null")
        machine.succeed("systemctl stop tun-loss-http6.service")
    ''}
    # Force a guarded recovery; the DNS/TUN probe and response sockets are now
    # owned by the actual service UID, never an independently tested setpriv.
    machine.succeed("${clashPackage.managedCore}/bin/mihomo -v; systemctl reload nftables.service")
    wait_proxy()

    # Migration starts supervision again. Hold its guarded C policy explicitly
    # while observing counters; normal health may otherwise reopen B midway
    # through the diagnostic assertions. The core and its TUN stay alive.
    machine.succeed("systemctl stop proxy-mode.service; ip netns exec lf-clash-core nft -s -j list table inet mihomo >/tmp/mihomo-before.json; nft add table inet filter; nft add table ip proxymode_nat; nft add table ip6 proxymode_nat; proxy-control migrate-firewall; systemctl stop proxy-mode.service${pkgs.lib.optionalString (dataplane || guests) "; ${maskSupervisor}"}")
    machine.succeed("jq -e '.mode == \"blocked\"' /run/proxy-mode/state.json; nft list chain inet lf_filter lf_blocked_guard | grep -q drop; ip netns exec lf-clash-core nft -s -j list table inet mihomo >/tmp/mihomo-after.json; cmp /tmp/mihomo-before.json /tmp/mihomo-after.json; python3 /etc/netsec-control.py check")

    for family, name in [("inet", "filter"), ("ip", "proxymode_nat"), ("ip6", "proxymode_nat")]:
        machine.fail(f"nft list table {family} {name}")
    ${pkgs.lib.optionalString (dataplane || guests) ''
    machine.fail("systemctl start proxy-mode.service")
    blocked_before = drop_view()
    assert blocked_before["policy"]["mode"] == "blocked", blocked_before
    ''}
    machine.fail("setpriv --reuid=1000 --clear-groups curl --noproxy \"\" -x http://127.0.0.1:7897 -fsS --max-time 2 http://a.ok.blocked.fixture.test:8080/")
    ${pkgs.lib.optionalString (dataplane || guests) ''
    with subtest("C diagnostics and denied inspection never change firewall decisions"):
        blocked_after = drop_view()
        assert blocked_after["policy"]["mode"] == "blocked", blocked_after
        assert drop_packets(blocked_after, "mode-c-output") > drop_packets(blocked_before, "mode-c-output")
        rules_before = json.loads(machine.succeed("nft -s -j list table inet lf_filter"))
        code, output = machine.execute("setpriv --reuid=1000 --clear-groups proxy-inspect --drops")
        denied = json.loads(output)
        assert code == 1 and not denied["available"] and denied["error"] == "permission-denied", denied
        assert denied["groups"] == [], denied
        assert json.loads(machine.succeed("nft -s -j list table inet lf_filter")) == rules_before
        machine.fail("setpriv --reuid=1000 --clear-groups curl --noproxy '*' -fsS --max-time 2 http://203.0.113.10:8080/")
        machine.succeed("proxy-inspect --drops >/tmp/drop-blocked.json")
        machine.copy_from_vm("/tmp/drop-blocked.json")
    machine.succeed("${unmaskSupervisor}")
    ''}
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
    ${pkgs.lib.optionalString dataplane ''
    with subtest("stopping the namespace releases owned objects and preserves a legacy TUN"):
        machine.succeed("systemctl stop clash-core-network.service; test ! -e /run/netns/lf-clash-core; test ! -e /run/clash-network-owner")
        machine.fail("ip link show dev Mihomo")
        for family in (4, 6):
            rules = json.loads(machine.succeed(f"ip -j -{family} rule show"))
            assert not any(9000 <= rule.get("priority", -1) < 9010 for rule in rules), rules
        # A stale ownership marker cannot authorize deleting an old-generation
        # TUN. All objects and sentinels here belong only to this disposable VM.
        machine.succeed("ip tuntap add dev Mihomo mode tun; touch /run/clash-network-owner")
        machine.fail("systemctl start clash-core-network.service")
        machine.succeed("ip link show dev Mihomo; ip link delete dev Mihomo; rm /run/clash-network-owner; systemctl reset-failed clash-core-network.service; systemctl start clash-core-network.service")
        machine.succeed("test -e /run/netns/lf-clash-core; ip -d link show dev Mihomo | grep -q veth")
    ''}
    ${pkgs.lib.optionalString (ech != null) ''
    machine.succeed("setpriv --reuid=lfour --regid=users --init-groups env HOME=/home/lfour XDG_RUNTIME_DIR=/run/user/1000 python3 /etc/netsec-ech.py portal")
    machine.copy_from_vm("/tmp/ech-browser-results.json")
    machine.copy_from_vm("/run/netsec-ech/events.jsonl")
    machine.copy_from_vm("/run/netsec-ech/dns.log")
    ''}
    ''}
  '';
}
