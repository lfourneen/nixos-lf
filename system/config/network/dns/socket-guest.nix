{ config, lib, pkgs, ... }:

let
  m = config.my.machine;
  p = config.my.proxy;
  enabled = config.my.hardening.socketGuestUsers != [ ] || p.controlledSources;
  unbound = config.services.unbound;
  stateDir = "/var/lib/unbound-infrastructure";
  renderOptions = attrs: lib.concatStrings (lib.mapAttrsToList (name: values:
    lib.concatMapStrings (value: "  ${name}: ${if builtins.isBool value then lib.boolToYesNo value else toString value}\n")
      (lib.toList values)) attrs);
  infrastructure = pkgs.writeText "unbound-infrastructure.conf" ''
    server:
    ${renderOptions ((removeAttrs unbound.settings.server [ "define-tag" ]) // {
      interface = "127.0.0.1@${toString m.ports.unboundInfrastructure}";
      directory = stateDir;
      auto-trust-anchor-file = "${stateDir}/root.key";
    })}
    ${lib.concatMapStrings (zone: "forward-zone:\n" + renderOptions zone)
      (unbound.settings.forward-zone or [ ])}
  '';

  settings = pkgs.writeText "socket-guest-dns.conf" ''
    port=${toString m.ports.socketGuestDns}
    listen-address=127.0.0.1${lib.optionalString config.networking.enableIPv6 ",::1"}
    bind-interfaces
    no-resolv
    no-hosts
    cache-size=1024
    server=127.0.0.1#${toString (if p.runtimeRouting then m.ports.coreInfrastructureDns else m.ports.unboundInfrastructure)}
    ${lib.concatMapStrings (directory: "conf-dir=${directory}\n") (config.services.dnsmasq.settings.conf-dir or [ ])}
    ${lib.concatMapStrings (d: "address=/${d}/\n") p.blockedDomains}
    ${lib.concatMapStrings (d: "server=/${d}/#\n") p.blockedDomainExceptions}
  '';

in
{
  users.groups.socket-guest-dns = lib.mkIf enabled { };
  users.users.socket-guest-dns = lib.mkIf enabled {
    isSystemUser = true;
    group = "socket-guest-dns";
    uid = m.uids.socketGuestDns;
  };
  users.groups.unbound-infrastructure = lib.mkIf enabled { };
  users.users.unbound-infrastructure = lib.mkIf enabled {
    isSystemUser = true;
    group = "unbound-infrastructure";
    uid = m.uids.unboundInfrastructure;
  };

  assertions = lib.optionals enabled [
    {
      assertion = lib.all (uid: uid > 0
        && lib.count (user: user.uid == uid) (lib.attrValues config.users.users) == 1)
        [ m.uids.socketGuestDns m.uids.unboundInfrastructure ];
      message = "Infrastructure DNS requires separate, dedicated non-root resolver and gateway UIDs.";
    }
    {
      assertion = m.ports.unboundInfrastructure >= 1024 && m.ports.coreInfrastructureDns >= 1024;
      message = "The infrastructure DNS listeners require unprivileged ports.";
    }
  ];

  # This instance has its own pending queries, DoT sockets and cache. Queries
  # accepted by the ordinary resolver in A cannot inherit the B DoT grant.
  systemd.services.unbound-infrastructure = lib.mkIf enabled {
    description = "Authenticated DoT resolver for infrastructure DNS";
    after = [ "nftables.service" ];
    requires = [ "nftables.service" ];
    preStart = ''
      if [ ! -s ${stateDir}/root.key ]; then
        ${pkgs.coreutils}/bin/install -m 0600 ${pkgs.dns-root-data}/root.key ${stateDir}/root.key
      fi
      ${unbound.package}/bin/unbound-checkconf ${infrastructure}
    '';
    serviceConfig = {
      ExecStart = "${unbound.package}/bin/unbound -p -d -c ${infrastructure}";
      Type = "notify";
      User = "unbound-infrastructure";
      Group = "unbound-infrastructure";
      StateDirectory = "unbound-infrastructure";
      StateDirectoryMode = "0700";
      NoNewPrivileges = true;
      CapabilityBoundingSet = "";
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateDevices = true;
      PrivateTmp = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" "AF_NETLINK" ];
      MemoryMax = "128M";
      TasksMax = 16;
      Restart = "on-failure";
      RestartSec = 2;
    };
  };

  # The main B resolver synthesizes addresses. Restricted host-socket backends
  # instead need checked real addresses so the UID guard sees the final target.
  systemd.services.socket-guest-dns = lib.mkIf enabled {
    description = "Filtered real-address DNS for restricted backends and source downloads";
    wantedBy = [ "multi-user.target" ];
    after = [ "nftables.service" "unbound-infrastructure.service" ] ++ lib.optional p.runtimeRouting "proxy-real-dns.service";
    requires = [ "nftables.service" ];
    wants = [ "unbound-infrastructure.service" ] ++ lib.optional p.runtimeRouting "proxy-real-dns.service";
    serviceConfig = {
      User = "socket-guest-dns";
      Group = "socket-guest-dns";
      ExecStart = "${config.services.dnsmasq.package}/bin/dnsmasq --keep-in-foreground --pid-file= --conf-file=${settings}";
      Restart = "on-failure";
      RestartSec = "2s";
      NoNewPrivileges = true;
      CapabilityBoundingSet = "";
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateDevices = true;
      PrivateTmp = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" "AF_NETLINK" ];
      LockPersonality = true;
    };
  };
}
