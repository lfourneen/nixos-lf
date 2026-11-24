{ config, lib, ... }:

let
  cfg = config.virtualisation.virtualbox.host;
  machine = config.my.machine;
  interface = "vboxnet0";
  # Match the host-only address installed by the upstream NixOS module.
  gateway = "192.168.56.1";
  subnet = "192.168.56.0/24";
  resolver = "127.0.0.1#${toString machine.ports.dnsmasq}";
  uplinks = lib.concatMapStringsSep ", " (name: ''"${name}"'') [ machine.wired.name machine.wireless.name ];
  service = "vboxnet-dnsmasq";
  
in
lib.mkMerge [
  {
    virtualisation.virtualbox.host = {
      enable = true;
      enableHardening = true;
      enableKvm = false;
      addNetworkInterface = true;
    };
    users.users.${machine.desktopUser}.extraGroups = lib.mkIf cfg.enable [ "vboxusers" ];
  }

  (lib.mkIf (cfg.enable && cfg.addNetworkInterface && !cfg.enableKvm) {
    my.hardening.guestNetworks.${interface} = {
      trust = lib.mkDefault "restricted";
      sourceCidrs = [ subnet ];
      gateways = [ gateway ];
    };
    my.proxy.guestInterfaces = lib.mkOptionDefault (lib.mkAfter [ interface ]);
    my.proxy.transparentGuestInterfaces = lib.mkIf config.my.proxy.tunMode [ interface ];

    # The shared forward guard authorizes the flow before postrouting.
    networking.nftables.ruleset = lib.mkAfter ''
      add rule ip lf_nat postrouting iifname "${interface}" oifname { ${uplinks} } ip saddr ${subnet} masquerade
    '';

    systemd.services.${service} = {
      description = "VirtualBox host-only DHCP and DNS gateway";
      wantedBy = [ "${interface}.service" ];
      after = [ "${interface}.service" "nftables.service" "dnsmasq.service" ];
      requires = [ "${interface}.service" "nftables.service" ];
      partOf = [ "${interface}.service" ];

      serviceConfig = {
        Type = "exec";
        ExecStart = lib.escapeShellArgs [
          "${config.services.dnsmasq.package}/bin/dnsmasq"
          "--keep-in-foreground"
          "--conf-file=/dev/null"
          "--pid-file="
          "--bind-dynamic"
          "--interface=${interface}"
          "--except-interface=lo"
          "--listen-address=${gateway}"
          "--user=nobody"
          "--group=nogroup"
          "--no-hosts"
          "--no-resolv"
          "--cache-size=0"
          "--server=${resolver}"
          "--dhcp-authoritative"
          "--dhcp-range=192.168.56.100,192.168.56.199,255.255.255.0,12h"
          "--dhcp-option=option:router,${gateway}"
          "--dhcp-option=option:dns-server,${gateway}"
          "--dhcp-leasefile=/var/lib/${service}/leases"
        ];
        Restart = "on-failure";
        StateDirectory = service;
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        CapabilityBoundingSet = [
          "CAP_CHOWN" "CAP_DAC_OVERRIDE" "CAP_NET_ADMIN" "CAP_NET_BIND_SERVICE" "CAP_NET_RAW"
          "CAP_SETGID" "CAP_SETPCAP" "CAP_SETUID"
        ];
      };
    };
  })
]
