{ config, lib, pkgs, ... }:

let
  inherit (lib) mkIf mkOption types;
  cfg = config.my.hardening.arp;
  inherit (config.my.machine) wired wireless;
  uplinks = [ wired.name wireless.name ];
  managed = lib.unique (uplinks ++ config.my.proxy.guestInterfaces);
  enabled = cfg.wired != null || cfg.wifi != { } || cfg.managedInterfaces != { };
  filterMode = types.enum [ "observe" "enforce" ];

  binding = types.submodule {
    options = {
      subnet = mkOption { type = types.str; description = "Canonical IPv4 subnet of the uplink."; };
      gateway = mkOption { type = types.str; description = "Verified on-link IPv4 gateway."; };
      mac = mkOption { type = types.str; description = "Verified unicast Ethernet address of the gateway."; };
      filter = mkOption {
        type = filterMode;
        default = "observe";
        description = "Count conflicting and invalid ARP claims or also drop them.";
      };
    };
  };

  settings = pkgs.writeText "network-arp.json" (builtins.toJSON {
    inherit enabled;
    inherit (cfg) wifi managedInterfaces;
    interfaces = { wired = wired.name; wifi = wireless.name; };
    wired = cfg.wired;
    ip = "${pkgs.iproute2}/bin/ip";
    nft = "${pkgs.nftables}/bin/nft";
    nmcli = "${pkgs.networkmanager}/bin/nmcli";
    systemctl = "${pkgs.systemd}/bin/systemctl";
  });

  command = pkgs.writeShellScript "network-arp-control" ''
    exec ${pkgs.python3}/bin/python3 ${./arp.py} ${settings} "$@"
  '';

  status = pkgs.writeShellScriptBin "network-arp" ''
    if [ "$#" -ne 0 ] && [ "$1" != status ]; then
      echo 'Usage: network-arp [status]' >&2
      exit 2
    fi
    exec ${command} status
  '';

  checks = verdict: lib.concatMapStrings (match: ''
    ${match} counter ${verdict}
  '') [
    "arp htype != 1"
    "arp ptype != ip"
    "arp hlen != 6"
    "arp plen != 4"
    "arp operation != { request, reply }"
    "iifname . arp saddr ip @gateways iifname . arp saddr ip . arp saddr ether != @bindings"
    "iifname . arp saddr ip @gateways iifname . arp saddr ip . ether saddr != @bindings"
  ];

in
{
  options.my.hardening.arp = {
    wired = mkOption {
      type = types.nullOr binding;
      default = null;
      description = "Verified gateway for the dedicated dhcpcd-managed wired uplink.";
    };

    wifi = mkOption {
      type = types.attrsOf binding;
      default = { };
      description = "Verified gateways keyed by NetworkManager Wi-Fi connection UUID.";
    };

    managedInterfaces = mkOption {
      type = types.attrsOf filterMode;
      default = { };
      description = "ARP frame validation on locally managed guest interfaces.";
    };

    sameSubnetReplies = mkOption {
      type = types.listOf types.str;
      default = [ ];
      description = "Interfaces that answer ARP only within the sender's configured IPv4 subnet.";
    };

    command = mkOption { type = types.nullOr types.path; internal = true; };
  };

  config = {
    assertions = [
      {
        assertion = lib.all (i: lib.elem i managed) cfg.sameSubnetReplies;
        message = "ARP reply restrictions require a declared uplink or guest interface.";
      }
      {
        assertion = lib.all (i: lib.elem i config.my.proxy.guestInterfaces && !lib.elem i uplinks)
          (lib.attrNames cfg.managedInterfaces);
        message = "ARP managedInterfaces requires declared guest interfaces.";
      }
    ];

    my.hardening.arp.command = if enabled then command else null;
    environment.systemPackages = [ status ];
    boot.kernel.sysctl = lib.listToAttrs (map (i: lib.nameValuePair "net/ipv4/conf/${i}/arp_ignore"
      (if lib.elem i cfg.sameSubnetReplies then 2 else 1)) managed);

    system.checks = lib.optional enabled (pkgs.runCommand "network-arp-config" { } ''
      ${command} validate
      touch "$out"
    '');

    networking.nftables.ruleset = lib.mkAfter (''
      destroy table arp lf_arp
    '' + lib.optionalString enabled ''
      table arp lf_arp {
        set observe { type ifname; }
        set enforce { type ifname; }
        set gateways { type ifname . ipv4_addr; }
        set bindings { type ifname . ipv4_addr . ether_addr; }
        chain input {
          type filter hook input priority 0; policy accept;
          iifname @observe jump audit
          iifname @enforce jump validate
        }
        chain audit {
          ${checks "return"}
        }
        chain validate {
          ${checks "drop"}
        }
      }
    '');

    systemd.services.network-arp-watch = mkIf enabled {
      description = "Wake network policy after managed ARP link changes";
      wantedBy = [ "multi-user.target" ];
      after = [ "nftables.service" ];
      serviceConfig = {
        ExecStart = "${command} watch";
        ExecStopPost = pkgs.writeShellScript "network-arp-stop" ''
          if [ "$SERVICE_RESULT" = success ]; then
            exec ${command} cleanup
          fi
        '';
        Restart = "always";
        RestartSec = "2s";
        NoNewPrivileges = true;
        CapabilityBoundingSet = [ "CAP_NET_ADMIN" ];
        ProtectSystem = "strict";
        ReadWritePaths = [ "-/run/proxy-mode" ];
        ProtectHome = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_NETLINK" ];
      };
    };
  };
}
