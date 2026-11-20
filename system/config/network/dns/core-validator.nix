{ config, lib, pkgs, ... }:

let
  p = config.my.proxy;
  m = config.my.machine;

  roles = {
    application = {
      port = m.ports.unbound;
      transport = m.ports.coreDnsTransport;
      budget = p.dnsQueryBudget.application;
    };

    infrastructure = {
      port = m.ports.socketGuestDns;
      transport = m.ports.coreDnsBootstrapTransport;
      budget = p.dnsQueryBudget.infrastructure;
    };
  };

  settings = role: value: pkgs.writeText "clash-dns-${role}.conf" ''
    server:
      interface: 127.0.0.1@${toString value.port}
      username: ""
      chroot: ""
      directory: /var/lib/clash-dns-${role}
      pidfile: ""
      do-daemonize: no
      use-syslog: no
      logfile: ""
      module-config: "validator iterator"
      auto-trust-anchor-file: /var/lib/clash-dns-${role}/root.key
      tls-cert-bundle: /etc/ssl/certs/ca-certificates.crt
      val-permissive-mode: no
      ignore-cd-flag: yes
      harden-dnssec-stripped: yes
      val-clean-additional: yes
      ede: yes
      do-ip6: ${if config.networking.enableIPv6 then "yes" else "no"}
      num-threads: 1
      num-queries-per-thread: ${toString value.budget}
      outgoing-range: ${toString (value.budget * 2)}
      incoming-num-tcp: ${toString value.budget}
      outgoing-num-tcp: ${toString value.budget}
      msg-cache-size: ${if role == "application" then "16m" else "4m"}
      rrset-cache-size: ${if role == "application" then "32m" else "8m"}
      serve-expired: ${if role == "application" then "yes" else "no"}
      ${lib.optionalString (role == "application") (lib.concatStringsSep "\n      " [
        "prefetch: yes"
        "serve-expired-ttl: 300"
        "serve-expired-ttl-reset: no"
        "serve-expired-reply-ttl: 30"
        "serve-expired-client-timeout: ${toString p.dnsStaleWaitMilliseconds}"
        "ede-serve-expired: yes"
      ])}
      do-not-query-localhost: no
      ${lib.concatMapStrings (cidr: "private-address: ${cidr}\n")
        (config.services.unbound.settings.server.private-address or [ ])}
    forward-zone:
      name: "."
      forward-tcp-upstream: yes
      forward-first: no
      forward-addr: 127.0.0.1@${toString value.transport}
  '';

in
{
  options.my.proxy.coreValidatorConfigs = lib.mkOption {
    type = lib.types.attrsOf lib.types.path;
    internal = true;
  };
  config = lib.mkIf p.localDnssec {
    my.proxy.coreValidatorConfigs = lib.mapAttrs settings roles;
    systemd.services = lib.mapAttrs' (role: value:
      lib.nameValuePair "clash-dns-${role}" {
        description = "Local DNSSEC validator for core ${role} queries";
        requires = [ "clash-core-network.service" ];
        after = [ "clash-core-network.service" ];
        bindsTo = [ "clash-core-network.service" ];

        preStart = ''
          if [ ! -s /var/lib/clash-dns-${role}/root.key ]; then
            ${pkgs.coreutils}/bin/install -m 0600 ${pkgs.dns-root-data}/root.key /var/lib/clash-dns-${role}/root.key
          fi
          ${config.services.unbound.package}/bin/unbound-checkconf ${settings role value}
        '';

        serviceConfig = {
          ExecStart = "${config.services.unbound.package}/bin/unbound -d -c ${settings role value}";
          NetworkNamespacePath = p.coreTopology.namespacePath;
          User = "unbound";
          Group = "unbound";
          StateDirectory = "clash-dns-${role}";
          StateDirectoryMode = "0700";
          CapabilityBoundingSet = "";
          NoNewPrivileges = true;
          ProtectSystem = "strict";
          ProtectHome = true;
          PrivateDevices = true;
          PrivateTmp = true;
          ProtectKernelTunables = true;
          ProtectKernelModules = true;
          ProtectControlGroups = true;
          RestrictNamespaces = true;
          RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" "AF_NETLINK" ];
          MemoryMax = "256M";
          TasksMax = 16;
          LimitNOFILE = value.budget * 6 + 128;
          Restart = "on-failure";
          RestartSec = 2;
        };
      }) roles // {
        clash-verge = {
          requires = [ "clash-dns-application.service" "clash-dns-infrastructure.service" ];
          after = [ "clash-dns-application.service" "clash-dns-infrastructure.service" ];
        };
      };
  };
}
