{ pkgs, clashPackage, restricted ? false }:

pkgs.testers.runNixOSTest {
  name = if restricted then "restricted-egress-core" else "proxy-control-privileges";
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
      ../../system/programs/systemd/proxy-mode.nix
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
    environment.etc."netsec-launcher".text = "${clashPackage}/bin/verge-mihomo";
    environment.etc."netsec-core".text = "${clashPackage.managedCore}/bin/mihomo";
    environment.systemPackages = [
      (pkgs.python3.withPackages (p: [ p.pyyaml ]))
      pkgs.curl pkgs.jq pkgs.dnsutils pkgs.iproute2 pkgs.nftables pkgs.util-linux
    ];
    virtualisation.memorySize = 1024;
  };
  testScript = ''
    def wait_proxy():
        try:
            machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = proxy && python3 /etc/netsec-control.py check", timeout=90)
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
    machine.succeed("systemd-run --unit=control-dns ip netns exec outside ${pkgs.python3}/bin/python3 /etc/netsec-dns.py server 15354 --dns-answer 203.0.113.10")
    for port in (80, 8080):
        machine.succeed(f"systemd-run --unit=control-http-{port} ip netns exec outside ${pkgs.python3}/bin/python3 -m http.server {port} --bind 0.0.0.0")
    core = machine.succeed("cat /etc/netsec-core").strip()
    machine.succeed(f"systemd-run --unit=control-node ip netns exec outside {core} -d /run/netsec-node -f /run/netsec-node.json")
    machine.wait_until_succeeds("ip netns exec outside ss -lnt | grep -q :18080")
    machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = direct")

    machine.succeed("python3 /etc/netsec-control.py refuse")
    machine.succeed("proxy-control on")
    machine.wait_for_file("/run/clash-verge-rev/service.sock")
    machine.succeed("python3 /etc/netsec-control.py start")
    wait_proxy()
    ${pkgs.lib.optionalString restricted ''
    # Both ports have real peers. The permitted native private connection
    # stays direct; the other cannot escape via the running proxy/TUN.
    machine.succeed("setpriv --reuid=1000 --clear-groups curl --noproxy '*' -fsS --max-time 3 http://100.67.0.1:8080/ >/dev/null")
    machine.fail("setpriv --reuid=1000 --clear-groups curl --noproxy '*' -fsS --max-time 2 http://100.67.0.1:80/")
    machine.fail("curl --noproxy '*' -fsS --max-time 2 http://100.67.0.1:8080/")
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
    ${pkgs.lib.optionalString restricted ''
    # Incremental rules_off must remove B's guard, not require a full reload.
    machine.succeed("setpriv --reuid=1000 --clear-groups curl --noproxy '*' -fsS --max-time 3 http://100.67.0.1:80/ >/dev/null")
    machine.succeed("curl --noproxy '*' -fsS --max-time 3 http://100.67.0.1:8080/ >/dev/null")
    ''}
    machine.succeed("python3 /etc/netsec-control.py refuse")
  '';
}
