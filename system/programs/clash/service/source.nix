{ config, lib, pkgs, ... }:

let
  p = config.my.proxy;
  python = pkgs.python3.withPackages (p: [ p.pyyaml p.dnspython ]);

  settings = pkgs.writeText "clash-source-settings.json" (builtins.toJSON {
    root = "/var/lib/clash-source";
    inherit (config.programs.clash-verge.package) stageSettings;
    policy = p.activeManagedPolicy;
    routing_state = if p.runtimeRouting then "/run/proxy-mode/routing.json" else null;
    forced_domains = p.tunProbeNames ++ map (url:
      builtins.head (builtins.match "https?://([^/:]+).*" url)) (p.nodeProbeUrls ++ [ p.delayTestUrl ]);
    probe_domains = p.probeDomains;
    python = "${python}/bin/python3";
    worker = ./source_worker.py;
    broker = ./source_broker.py;
    review = ./source_review.py;
    setpriv = "${pkgs.util-linux}/bin/setpriv";
    systemctl = "${pkgs.systemd}/bin/systemctl";
    desktop_uid = config.users.users.${config.my.machine.desktopUser}.uid;
    gui_index = "${config.users.users.${config.my.machine.desktopUser}.home}/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles.yaml";
    ca = config.security.pki.caBundle;
    resolver = { address = "127.0.0.1"; port = config.my.machine.ports.socketGuestDns; };
    remote_only_catchall = p.remoteOnlyCatchAll;
    assets = {
      "GeoIP.dat" = "${pkgs.v2ray-geoip}/share/v2ray/geoip.dat";
      "GeoSite.dat" = "${pkgs.v2ray-domain-list-community}/share/v2ray/geosite.dat";
    };
  });

  command = pkgs.writeShellScriptBin "clash-source" ''
    exec ${python}/bin/python3 -I ${./source_broker.py} ${settings} "$@"
  '';

in
{
  config = lib.mkMerge [ { my.proxy.sourceSettings = settings; } (lib.mkIf p.controlledSources {
    users.groups.clash-fetch = {};
    users.users.clash-fetch = {
      isSystemUser = true;
      group = "clash-fetch";
      uid = config.my.machine.uids.clashFetch;
    };

    assertions = [{
      assertion = config.my.machine.uids.clashFetch > 0
        && lib.count (u: u.uid == config.my.machine.uids.clashFetch) (lib.attrValues config.users.users) == 1;
      message = "Controlled source downloads require a dedicated fixed clash-fetch UID.";
    }];

    environment.systemPackages = [ command (pkgs.writeShellScriptBin "clash-diff" ''
      exec ${python}/bin/python3 -I ${./source_review.py} ${settings} "$@"
    '') ];

    systemd.tmpfiles.rules = [ "d /var/lib/clash-source 0700 root root -" ];

    systemd.paths.clash-source-select = {
      wantedBy = [ "multi-user.target" ];
      pathConfig.PathChanged = "${config.users.users.${config.my.machine.desktopUser}.home}/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles.yaml";
    };

    systemd.services.clash-source-select = {
      description = "Select only root-approved GUI source mappings";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${command}/bin/clash-source gui-select";
        UMask = "0077";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = "read-only";
        PrivateTmp = true;
        ReadWritePaths = [ "/var/lib/clash-source" ];

        # Read the fixed desktop-owned index through a private home directory.
        CapabilityBoundingSet = [ "CAP_SETUID" "CAP_SETGID" "CAP_SETPCAP" "CAP_DAC_READ_SEARCH" ];
        RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" ];
        TimeoutStartSec = "70s";
        MemoryMax = "768M";
        TasksMax = 32;
      };
    };

    # Keep network policy capabilities outside the download/parser service.
    # The caller holds operator.lock and waits for this fixed C transaction
    # before it can stop the currently authorized core.
    systemd.services.clash-source-guard = {
      description = "Revoke B before activating a checked source";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${config.my.proxy.modeScript} --request-proxy";
        NoNewPrivileges = true;
        CapabilityBoundingSet = [ "CAP_NET_ADMIN" ];
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        ReadWritePaths = [ "/run/proxy-mode" "/run/netsec" ];
        RestrictAddressFamilies = [ "AF_UNIX" "AF_NETLINK" ];
        TimeoutStartSec = "20s";
      };
    };

    systemd.services.clash-source-refresh = {
      description = "Fetch registered subscription and immutable provider generation";
      after = [ "network-online.target" "socket-guest-dns.service" ];
      wants = [ "network-online.target" "socket-guest-dns.service" ];
      environment.LF_MANAGED_PROXY_POLICY = config.systemd.services.clash-verge.environment.LF_MANAGED_PROXY_POLICY;

      script = ''
        # Bound transient download retries within this job, without rate
        # limiting a later explicit start or retrying a rejected import.
        for attempt in 1 2 3; do
          if ! ${pkgs.python3}/bin/python3 ${./source_refresh.py} download-ready \
            ${config.programs.clash-verge.package.stageSettings}; then
            exit 0
          fi
          if ${command}/bin/clash-source refresh; then break; fi
          if ! ${pkgs.python3}/bin/python3 ${./source_refresh.py} download-ready \
            ${config.programs.clash-verge.package.stageSettings}; then
            exit 0
          fi
          if [ "$attempt" = 3 ]; then exit 1; fi
          sleep 15
        done
        if [ "$(cat /run/proxy-mode/intent 2>/dev/null)" = proxy ]; then
          ${pkgs.python3}/bin/python3 ${./source_refresh.py} \
            ${config.programs.clash-verge.package.stageSettings} \
            ${config.programs.clash-verge.package}/bin/verge-mihomo \
            clash-source-guard.service /run/clash-source-check
        fi
      '';

      serviceConfig = {
        Type = "oneshot";
        UMask = "0077";
        RuntimeDirectory = "clash-source-check";
        RuntimeDirectoryMode = "0755";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        ReadWritePaths = [
          "/var/lib/clash-source"
          "/run/proxy-mode"
        ];

        # Check the core socket after restart without granting DAC override.
        # The download worker clears supplementary groups before execution.
        SupplementaryGroups = [ "clash-core" ];
        CapabilityBoundingSet = [ "CAP_SETUID" "CAP_SETGID" "CAP_SETPCAP" ];
        RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" "AF_NETLINK" ];
        TimeoutStartSec = "180s";
        MemoryMax = "768M";
        TasksMax = 32;
      };
    };

    systemd.timers.clash-source-refresh = {
      wantedBy = [ "timers.target" ];
      timerConfig = { OnBootSec = "2min"; OnUnitActiveSec = "30min"; RandomizedDelaySec = "30s"; };
    };
  }) ];
}
