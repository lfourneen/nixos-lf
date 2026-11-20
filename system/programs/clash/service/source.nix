{ config, lib, pkgs, ... }:

let
  p = config.my.proxy;
  python = pkgs.python3.withPackages (p: [ p.pyyaml p.dnspython ]);
  stageSettings = config.programs.clash-verge.package.stageSettings;
  refreshCommand = "${pkgs.python3}/bin/python3 ${./source_refresh.py}";
  guiDirectory = "${config.users.users.${config.my.machine.desktopUser}.home}/.local/share/io.github.clash-verge-rev.clash-verge-rev";

  rendererSettings = {
    forced_domains = p.tunProbeNames ++ map (url:
      builtins.head (builtins.match "https?://([^/:]+).*" url)) (p.nodeProbeUrls ++ [ p.delayTestUrl ]);
    probe_domains = p.probeDomains;
    remote_only_catchall = p.remoteOnlyCatchAll;
  };

  settings = pkgs.writeText "clash-source-settings.json" (builtins.toJSON (rendererSettings // {
    root = "/var/lib/clash-source";
    inherit (config.programs.clash-verge.package) stageSettings;
    policy = p.activeManagedPolicy;
    routing_state = if p.runtimeRouting then "/run/proxy-mode/routing.json" else null;
    insecure_sources = "/run/secrets/insecure_sources";
    python = "${python}/bin/python3";
    worker = ./source_worker.py;
    broker = ./source_broker.py;
    review = ./source_review.py;
    manual_refresh = ./source_refresh.py;
    launcher = "${config.programs.clash-verge.package}/bin/verge-mihomo";
    catalog = config.sops.secrets.proxy_source.path or "/run/secrets/proxy_source";
    setpriv = "${pkgs.util-linux}/bin/setpriv";
    systemctl = "${pkgs.systemd}/bin/systemctl";
    desktop_uid = config.users.users.${config.my.machine.desktopUser}.uid;
    gui_index = "${guiDirectory}/profiles.yaml";
    ca = config.security.pki.caBundle;
    resolver = { address = "127.0.0.1"; port = config.my.machine.ports.socketGuestDns; };
    assets = {
      "GeoIP.dat" = "${pkgs.v2ray-geoip}/share/v2ray/geoip.dat";
      "GeoSite.dat" = "${pkgs.v2ray-domain-list-community}/share/v2ray/geosite.dat";
    };
  }));

  command = pkgs.writeShellScriptBin "clash-source" ''
    export LF_MANAGED_PROXY_POLICY=${lib.escapeShellArg p.activeManagedPolicy}
    exec ${python}/bin/python3 -I ${./source_broker.py} ${settings} "$@"
  '';

  downloadService = request: {
    description = "Fetch registered subscription and immutable provider generation";
    after = [ "proxy-mode.service" "network-online.target" "socket-guest-dns.service" ];
    wants = [ "network-online.target" "socket-guest-dns.service" ];
    environment = {
      LF_MANAGED_PROXY_POLICY = p.activeManagedPolicy;
      LF_SOURCE_GUI_INDEX = "/run/clash-source-gui/profiles.yaml";
    };
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${command}/bin/clash-source automatic${lib.optionalString request " --request"}";
      UMask = "0077";
      NoNewPrivileges = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      BindReadOnlyPaths = [ "-${guiDirectory}:/run/clash-source-gui" ];
      ReadWritePaths = [ "/var/lib/clash-source" "/run/proxy-mode" ];

      # Only the broker can read the fixed GUI index and query core status.
      # The download worker drops all capabilities and supplementary groups.
      SupplementaryGroups = [ "clash-core" ];
      CapabilityBoundingSet = [ "CAP_DAC_READ_SEARCH" "CAP_SETUID" "CAP_SETGID" "CAP_SETPCAP" ];
      RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" ];
      TimeoutStartSec = "210s";
      MemoryMax = "768M";
      TasksMax = 32;
    };
  };

in
{
  options.my.proxy.sourceRendererSettings = lib.mkOption { type = lib.types.attrs; internal = true; };
  config = lib.mkMerge [ {
    my.proxy.sourceSettings = settings;
    my.proxy.sourceRendererSettings = rendererSettings;
  } (lib.mkIf p.controlledSources {
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
      export LF_MANAGED_PROXY_POLICY=${lib.escapeShellArg p.activeManagedPolicy}
      exec ${python}/bin/python3 -I ${./source_review.py} ${settings} "$@"
    '') ];

    systemd.tmpfiles.rules = [ "d /var/lib/clash-source 0700 root root -" ];

    systemd.paths.clash-source-select = {
      wantedBy = [ "multi-user.target" ];
      pathConfig.PathChanged = "${guiDirectory}/profiles.yaml";
    };

    systemd.services.clash-source-select = {
      description = "Select only root-approved GUI source mappings";
      after = [ "proxy-mode.service" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${command}/bin/clash-source gui-select";
        UMask = "0077";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = "read-only";
        PrivateTmp = true;
        ReadWritePaths = [ "/var/lib/clash-source" "/run/proxy-mode" ];

        # Read the fixed desktop-owned index through a private home directory.
        CapabilityBoundingSet = [ "CAP_DAC_READ_SEARCH" ];
        RestrictAddressFamilies = [ "AF_UNIX" ];
        TimeoutStartSec = "25s";
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

    systemd.services.clash-source-refresh = downloadService true;
    systemd.services.clash-source-download = downloadService false;

    systemd.paths.clash-source-download = {
      wantedBy = [ "multi-user.target" ];
      pathConfig.PathExists = "/run/proxy-mode/source-refresh-ready";
    };
    systemd.paths.clash-source-apply = {
      wantedBy = [ "multi-user.target" ];
      pathConfig.PathExists = "/run/proxy-mode/source-apply.json";
    };

    systemd.services.clash-source-apply = {
      description = "Apply a downloaded subscription generation";
      environment.LF_MANAGED_PROXY_POLICY = p.activeManagedPolicy;
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${refreshCommand} apply ${stageSettings} ${config.programs.clash-verge.package}/bin/verge-mihomo clash-source-guard.service /run/clash-source-check";
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

        SupplementaryGroups = [ "clash-core" ];
        CapabilityBoundingSet = [ "CAP_SETUID" "CAP_SETGID" "CAP_SETPCAP" ];
        RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" "AF_NETLINK" ];
        TimeoutStartSec = "infinity";
        MemoryMax = "768M";
        TasksMax = 32;
      };
    };

    systemd.services.clash-source-request = {
      description = "Queue a subscription refresh";
      after = [ "proxy-mode.service" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${refreshCommand} request ${stageSettings}";
        UMask = "0077";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        ReadWritePaths = [ "/run/proxy-mode" ];
        RestrictAddressFamilies = [ "AF_UNIX" ];
      };
    };

    systemd.timers.clash-source-refresh = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        Unit = "clash-source-request.service";
        OnBootSec = "2min";
        OnUnitActiveSec = "30min";
        RandomizedDelaySec = "30s";
      };
    };
  }) ];
}
