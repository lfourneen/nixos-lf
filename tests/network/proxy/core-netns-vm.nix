{ pkgs, core }:

let
  fixture = ./core_netns_fixture.py;

  # Deliberately separate from the production launch policy. The prototype
  # exposes only the veth inlets and uses fixed synthetic upstream nodes.
  profile = {
    mixed-port = 7897;
    allow-lan = true;
    bind-address = "*";
    ipv6 = true;
    mode = "rule";
    log-level = "info";
    routing-mark = 6666;
    external-controller-unix = "/run/core-netns/controller.sock";

    dns = {
      enable = true;
      listen = "[::]:1053";
      enhanced-mode = "fake-ip";
      fake-ip-range = "198.18.0.1/16";
      nameserver = [ "udp://203.0.113.20:15353" ];
      default-nameserver = [ "203.0.113.20" ];
    };

    tun = {
      enable = true;
      device = "Mihomo";
      stack = "mixed";
      auto-route = false;
      auto-redirect = false;
      dns-hijack = [ "any:53" ];
      inet4-address = [ "198.18.0.1/30" ];
      inet6-address = [ "fdfe:dcba:9876::1/126" ];
    };

    proxies = map (address: {
      name = if address == "203.0.113.20" then "node4" else "node6";
      type = "socks5";
      server = address;
      port = 18080;
      udp = true;
    }) [ "203.0.113.20" "2001:db8:42::20" ];
    proxy-groups = [{ name = "P"; type = "select"; proxies = [ "node4" "node6" ]; }];
    rules = [ "MATCH,P" ];
  };

  policy = pkgs.writeText "core-netns-policy.json" (builtins.toJSON {
    "tun.enable" = true;
    "tun.auto-route" = false;
    "tun.auto-redirect" = false;
    mixed-port = 7897;
    allow-lan = true;
    lf-private-targets = {
      proxy-only = true;
      prefixes = [ "10.0.0.0/8" "192.168.0.0/16" "127.0.0.0/8" "fc00::/7" "::1/128" ];
      subscription-trust = "fixed";
    };
  });

in
pkgs.testers.runNixOSTest {
  name = "core-netns-prototype";
  nodes.machine = { ... }: {
    users.groups.clash-core = { };
    users.users.clash-core = { isSystemUser = true; uid = 982; group = "clash-core"; };
    users.users.client = { isNormalUser = true; uid = 1000; };
    networking.firewall.enable = false;
    networking.useDHCP = false;
    networking.dhcpcd.enable = false;
    boot.kernel.sysctl = { "net.ipv4.ip_forward" = 1; "net.ipv6.conf.all.forwarding" = 1; };
    environment.systemPackages = [ pkgs.python3 pkgs.iproute2 pkgs.nftables pkgs.util-linux pkgs.curl pkgs.dnsutils pkgs.procps ];
    environment.etc."core-netns.py".source = fixture;
    environment.etc."core-netns.json".text = builtins.toJSON profile;
    environment.etc."core-netns-bin".text = "${core}/bin/mihomo";

    systemd.services.core-netns-setup = {
      wantedBy = [ "multi-user.target" ];
      before = [ "core-netns.service" ];
      path = [ pkgs.iproute2 pkgs.nftables pkgs.util-linux pkgs.procps ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${pkgs.python3}/bin/python3 ${fixture} setup";
      };
    };

    systemd.services.core-netns = {
      requires = [ "core-netns-setup.service" ];
      after = [ "core-netns-setup.service" ];
      environment = { LF_MANAGED_PROXY_POLICY = policy; SKIP_SYSTEM_IPV6_CHECK = "1"; };
      serviceConfig = {
        User = "clash-core";
        Group = "clash-core";
        NetworkNamespacePath = "/run/netns/lf-core";
        RuntimeDirectory = "core-netns";
        StateDirectory = "core-netns";
        ExecStart = "${core}/bin/mihomo -d /var/lib/core-netns -f /etc/core-netns.json";
        CapabilityBoundingSet = [ "CAP_NET_ADMIN" "CAP_NET_RAW" ];
        AmbientCapabilities = [ "CAP_NET_ADMIN" "CAP_NET_RAW" ];
        NoNewPrivileges = true;
        RestrictNamespaces = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" "AF_NETLINK" ];
        UMask = "0077";
      };
    };

    systemd.sockets.core-netns-gui = {
      wantedBy = [ "sockets.target" ];
      socketConfig = {
        ListenStream = "/run/core-netns-gui/controller.sock";
        SocketUser = "client";
        SocketMode = "0600";
        Accept = true;
      };
    };

    systemd.services."core-netns-gui@".serviceConfig = {
      User = "clash-core";
      ExecStart = "${pkgs.socat}/bin/socat STDIO UNIX-CONNECT:/run/core-netns/controller.sock";
      StandardInput = "socket";
      StandardOutput = "socket";
      CapabilityBoundingSet = "";
      NoNewPrivileges = true;
      RestrictAddressFamilies = [ "AF_UNIX" ];
      ProtectSystem = "strict";
      ProtectHome = true;
    };

    virtualisation.memorySize = 1536;
  };

  testScript = ''
    start_all()
    machine.wait_for_unit("core-netns-setup.service")
    try:
        machine.succeed("python3 /etc/core-netns.py check > /tmp/core-netns.log 2>&1", timeout=240)
    except Exception:
        print(machine.execute("cat /tmp/core-netns.log; journalctl -u core-netns -u netns-node0 -u netns-node1 --no-pager -n 80; nft list table inet lf_core_prototype"))
        raise
    finally:
        machine.copy_from_vm("/tmp/core-netns.log")
  '';
}
