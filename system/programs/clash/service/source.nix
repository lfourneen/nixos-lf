{ config, lib, pkgs, ... }:

let
  p = config.my.proxy;
  python = pkgs.python3.withPackages (p: [ p.pyyaml ]);

  settings = pkgs.writeText "clash-source-settings.json" (builtins.toJSON {
    root = "/var/lib/clash-source";
    inherit (config.programs.clash-verge.package) stageSettings;
    policy = pkgs.writeText "clash-source-policy.json" (builtins.toJSON p.managedProfile);
    forced_domains = p.tunProbeNames ++ map (url:
      builtins.head (builtins.match "https?://([^/:]+).*" url)) (p.nodeProbeUrls ++ [ p.delayTestUrl ]);
    probe_domains = p.probeDomains;
    python = "${python}/bin/python3";
    worker = ./source_worker.py;
    setpriv = "${pkgs.util-linux}/bin/setpriv";
    systemctl = "${pkgs.systemd}/bin/systemctl";
    desktop_uid = config.users.users.${config.my.machine.desktopUser}.uid;
    gui_index = "${config.users.users.${config.my.machine.desktopUser}.home}/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles.yaml";
    ca = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
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
  config = lib.mkIf p.controlledSources {
    users.groups.clash-fetch = {};
    users.users.clash-fetch = { isSystemUser = true; group = "clash-fetch"; };
    environment.systemPackages = [ command ];
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

    systemd.services.clash-source-refresh = {
      description = "Fetch registered subscription and immutable provider generation";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];

      script = ''
        ${command}/bin/clash-source refresh
        if [ "$(cat /run/proxy-mode/intent 2>/dev/null)" = proxy ]; then
          ${pkgs.python3}/bin/python3 ${./clash-verge-stage.py} restart-source \
            ${config.programs.clash-verge.package.stageSettings} \
            ${config.programs.clash-verge.package}/bin/verge-mihomo
        fi
      '';

      serviceConfig = {
        Type = "oneshot";
        UMask = "0077";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        ReadWritePaths = [ "/var/lib/clash-source" "/run/proxy-mode" ];
        CapabilityBoundingSet = [ "CAP_SETUID" "CAP_SETGID" "CAP_SETPCAP" ];
        RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" "AF_NETLINK" ];
        TimeoutStartSec = "100s";
        MemoryMax = "768M";
        TasksMax = 32;
      };
    };

    systemd.timers.clash-source-refresh = {
      wantedBy = [ "timers.target" ];
      timerConfig = { OnBootSec = "2min"; OnUnitActiveSec = "30min"; RandomizedDelaySec = "30s"; };
    };
  };
}
