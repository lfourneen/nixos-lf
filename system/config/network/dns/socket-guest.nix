{ config, lib, pkgs, ... }:

let
  m = config.my.machine;
  p = config.my.proxy;
  enabled = config.my.hardening.socketGuestUsers != [ ];

  settings = pkgs.writeText "socket-guest-dns.conf" ''
    port=${toString m.ports.socketGuestDns}
    listen-address=127.0.0.1${lib.optionalString config.networking.enableIPv6 ",::1"}
    bind-interfaces
    no-resolv
    no-hosts
    cache-size=1024
    server=127.0.0.1#${toString m.ports.unbound}
    ${lib.concatMapStrings (directory: "conf-dir=${directory}\n") (config.services.dnsmasq.settings.conf-dir or [ ])}
    ${lib.concatMapStrings (d: "address=/${d}/\n") p.blockedDomains}
    ${lib.concatMapStrings (d: "server=/${d}/#\n") p.blockedDomainExceptions}
  '';

in
{
  # The main B resolver synthesizes addresses. Restricted host-socket backends
  # instead need checked real addresses so the UID guard sees the final target.
  # Both gateways share the immutable filter; only authenticated Unbound is an
  # upstream here. An unavailable/disabled DoT path fails closed without a lease.
  systemd.services.socket-guest-dns = lib.mkIf enabled {
    description = "Filtered real-address DNS for restricted socket guest accounts";
    wantedBy = [ "multi-user.target" ];
    after = [ "nftables.service" "unbound.service" ];
    requires = [ "nftables.service" ];
    wants = [ "unbound.service" ];
    serviceConfig = {
      User = "dnsmasq";
      Group = "dnsmasq";
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
