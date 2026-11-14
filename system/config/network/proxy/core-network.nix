{ config, lib, pkgs, ... }:

let
  p = config.my.proxy;
  m = config.my.machine;
  topology = p.coreTopology;

  settings = pkgs.writeText "clash-network.json" (builtins.toJSON (topology // {
    interface = p.tunDev;
    table = p.routeTable;
    rule = p.tunRouting.iproute2-rule-index;
    mtu = p.tunMtu;
    ipv6 = config.networking.enableIPv6;
    tun = p.tunMode;
    strictDns = p.dnsEncryption == "strict";
    localTargets = p.localTargetCidrs;
    nat64 = p.nat64Prefixes;
    fake = p.fakeIpNet;
    private4 = m.privateV4 ++ [ m.vmSubnet m.waydroidSubnet ];
    private6 = m.linkLocalV6 ++ m.ulaV6;

    routeExclude = lib.optionals config.my.hardening.directEgress.privateRoutes
      (m.privateV4 ++ m.ulaV6 ++ m.linkLocalV6)
      ++ m.multicastV4 ++ [ "${m.limitedBroadcastV4}/32" ] ++ m.multicastV6
      ++ [ topology.subnet4 topology.subnet6 ];

    ports = m.ports;
    uplinks = [ m.wired.name m.wireless.name ];

    magicDns = if config.services.tailscale.enable && config.my.hardening.tailscale.magicDns
      then { address = m.magicDns; interface = config.services.tailscale.interfaceName; } else null;

    bypassUids = map (name: config.users.users.${name}.uid) [ "dnsmasq" "unbound" "chrony" ];
    coreMark = m.mihomoMark;
    tailscaleMark = config.my.hardening.tailscale.transportMark;
    coreUid = m.uids.clashCore;
    loopbackUid = m.uids.clashLoopback;
    loopbackDirectory = "/run/clash-loopback";
    loopbackReplyMark = 1279721470;
    desktopUid = config.users.users.${m.desktopUser}.uid;

    guests = map (iface: {
      interface = iface;
      sources = config.my.hardening.guestNetworks.${iface}.sourceCidrs;
    }) p.transparentGuestInterfaces;
    ip = "${pkgs.iproute2}/bin/ip";
    ss = "${pkgs.iproute2}/bin/ss";
    nft = "${pkgs.nftables}/bin/nft";
    sysctl = "${pkgs.procps}/bin/sysctl";
    systemctl = "${pkgs.systemd}/bin/systemctl";
    listenersModule = ./listeners.py;
    tunModule = ./tun.py;
  }));

  policy = pkgs.runCommand "clash-network-policy" { nativeBuildInputs = [ pkgs.python3 ]; } ''
    python3 ${./core_network.py} ${settings} render "$out"
  '';

  command = pkgs.writeShellScript "clash-network" ''
    exec ${pkgs.python3}/bin/python3 ${./core_network.py} ${settings} "$@"
  '';

in
{
  options.my.proxy = {
    coreNetworkPolicy = lib.mkOption { type = lib.types.path; internal = true; };
    coreNetworkCommand = lib.mkOption { type = lib.types.path; internal = true; };
  };

  config = {
    my.proxy.coreNetworkPolicy = policy;
    my.proxy.coreNetworkCommand = command;
    users.groups.clash-loopback = lib.mkIf p.coreNetns { };
    users.users.clash-loopback = lib.mkIf p.coreNetns {
      isSystemUser = true;
      uid = m.uids.clashLoopback;
      group = "clash-loopback";
    };

    assertions = lib.optionals p.coreNetns [{
      assertion = m.uids.clashLoopback > 0
        && lib.count (u: u.uid == m.uids.clashLoopback) (lib.attrValues config.users.users) == 1;
      message = "The host loopback relay requires its own fixed identity, separate from the core.";
    }];

    networking.networkmanager.unmanaged = lib.mkIf p.coreNetns [ "interface-name:${p.tunDev}" "interface-name:${topology.peer}" ];
    systemd.network.links."05-clash-core" = lib.mkIf p.coreNetns {
      matchConfig = { OriginalName = "${p.tunDev} ${topology.peer}"; Driver = "veth"; };
      linkConfig.MACAddressPolicy = "none";
    };

    systemd.services.clash-core-network = lib.mkIf p.coreNetns {
      description = "Fixed network namespace and host routes for the managed core";
      wantedBy = [ "multi-user.target" ];
      requires = [ "nftables.service" ];
      after = [ "nftables.service" ];
      before = [ "clash-verge.service" "clash-network-observer.service" ];
      restartTriggers = [ settings policy ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${command} setup ${policy}/namespace.nft";
        ExecStop = "${command} teardown";
        TimeoutStartSec = "30s";
        # ip netns creates the fixed bind mount in the host mount namespace.
        # No path or command supplied by an application reaches this unit.
      };
    };

    systemd.services.clash-core-uplink-watch = lib.mkIf p.coreNetns {
      description = "Refresh isolated core transports after physical uplink changes";
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" "proxy-mode.service" ];
      serviceConfig = {
        ExecStart = "${command} watch";
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
        RestrictNamespaces = true;
        RestrictAddressFamilies = [ "AF_NETLINK" "AF_UNIX" ];
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        MemoryMax = "64M";
        TasksMax = 8;
        Restart = "always";
        RestartSec = "3s";
      };
    };

    systemd.services.clash-network-observer = lib.mkIf p.coreNetns {
      description = "Read-only kernel observations inside the core namespace";
      wantedBy = [ "multi-user.target" ];
      requires = [ "clash-core-network.service" ];
      after = [ "clash-core-network.service" ];
      bindsTo = [ "clash-core-network.service" ];
      serviceConfig = {
        ExecStart = "${command} observe";
        NetworkNamespacePath = topology.namespacePath;
        RuntimeDirectory = "clash-network";
        RuntimeDirectoryMode = "0755";
        UMask = "0077";
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
        RestrictNamespaces = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_NETLINK" ];
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        MemoryMax = "96M";
        TasksMax = 16;
        Restart = "on-failure";
      };
    };

    systemd.services.clash-core-loopback = lib.mkIf p.coreNetns {
      description = "Preserve authorized host loopback targets for the isolated core";
      wantedBy = [ "multi-user.target" ];
      requires = [ "clash-core-network.service" ];
      after = [ "clash-core-network.service" ];
      bindsTo = [ "clash-core-network.service" ];
      serviceConfig = {
        ExecStart = "${pkgs.python3}/bin/python3 ${./core_loopback.py} ${settings}";
        User = "clash-loopback";
        Group = "clash-core";
        RuntimeDirectory = "clash-loopback";
        RuntimeDirectoryMode = "0750";
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
        RestrictNamespaces = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        MemoryMax = "128M";
        TasksMax = 160;
        LimitNOFILE = 1024;
        Restart = "on-failure";
      };
    };

    systemd.services.clash-core-localhost = lib.mkIf p.coreNetns {
      description = "Keep localhost packets local to the core namespace";
      requires = [ "clash-core-network.service" ];
      after = [ "clash-core-network.service" ];
      bindsTo = [ "clash-core-network.service" ];
      serviceConfig = {
        ExecStart = "${pkgs.python3}/bin/python3 ${./core_loopback.py} ${settings} namespace";
        NetworkNamespacePath = topology.namespacePath;
        User = "clash-core";
        Group = "clash-core";
        NoNewPrivileges = true;
        # Transparent UDP metadata and replies are confined to this netns.
        CapabilityBoundingSet = "CAP_NET_ADMIN";
        AmbientCapabilities = [ "CAP_NET_ADMIN" ];
        RestrictNamespaces = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        MemoryMax = "128M";
        TasksMax = 160;
        LimitNOFILE = 1024;
        Restart = "on-failure";
      };
    };
  };
}
