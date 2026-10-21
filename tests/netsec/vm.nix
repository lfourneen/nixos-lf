{ pkgs, ... }:

pkgs.testers.runNixOSTest {
  name = "proxy-policy-recovery";
  node.pkgs = pkgs.lib.mkForce (pkgs.extend (_final: prev: { unstable = prev; }));
  nodes.machine = { config, lib, pkgs, ... }: {
    imports = [
      ../../system/config/machine.nix
      ../../system/config/hardening-options.nix
      ../../system/config/proxy-options.nix
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
    boot.kernel.sysctl."net.ipv4.ip_forward" = 1;
    my.proxy.transparentGuestInterfaces = [ "virbr0" ];
    networking.networkmanager.unmanaged = [ "interface-name:eth*" ];
    # Keep the real unit dependency graph without needing a DHCP server. All
    # packet tests use namespaces and explicitly assigned addresses inside VM.
    systemd.services.dhcpcd.serviceConfig = {
      Type = lib.mkForce "simple";
      ExecStart = lib.mkForce "${pkgs.writeShellScript "test-dhcpcd" ''exec ${pkgs.coreutils}/bin/sleep infinity''} --config ${pkgs.writeText "test-dhcpcd.conf" ""}";
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
    environment.systemPackages = [ pkgs.curl pkgs.python3 pkgs.jq pkgs.iproute2 pkgs.nftables ];
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
    machine.succeed("systemctl stop nftables-verify-pending.path nftables-verify.timer nftables-verify.service proxy-mode.service; systemctl mask --runtime proxy-mode.service")
    machine.succeed("ip netns exec outside sh -c 'python3 -m http.server 8080 --bind 0.0.0.0 >/tmp/outside-http.log 2>&1 &'")
    machine.wait_until_succeeds("curl --noproxy '*' -fsS --connect-timeout 1 http://203.0.113.10:8080 >/dev/null")
    machine.succeed("ip link add virbr0 type bridge; ip addr add 192.168.122.1/24 dev virbr0; ip link set virbr0 up")
    machine.succeed("ip netns add guest; ip link add guest-host type veth peer name guest0; ip link set guest-host master virbr0; ip link set guest-host up; ip link set guest0 netns guest")
    machine.succeed("ip -n guest addr add 192.168.122.2/24 dev guest0; ip -n guest link set guest0 up; ip -n guest link set lo up; ip -n guest route add default via 192.168.122.1")
    machine.wait_until_succeeds("ip netns exec guest curl --noproxy '*' -fsS --connect-timeout 1 http://203.0.113.10:8080 >/dev/null")
    machine.succeed("nft -f /etc/netsec-mode-b.nft")
    machine.succeed("systemd-run --unit=netsec-tproxy /run/current-system/sw/bin/python3 /etc/netsec-tproxy.py server")
    machine.wait_until_succeeds("ip netns exec guest curl --noproxy '*' -fsS --connect-timeout 1 http://203.0.113.10:8080 | grep -qx OK")
    machine.succeed("systemd-run --unit=netsec-guest ip netns exec guest /run/current-system/sw/bin/python3 /etc/netsec-tproxy.py client")
    machine.wait_until_succeeds("test -e /tmp/guest-ready")
    machine.succeed("nft -f /etc/netsec-mode-c.nft")
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

