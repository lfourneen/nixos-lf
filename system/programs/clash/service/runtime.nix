{ config, lib, pkgs, ... }:

let
  desktopUser = config.my.machine.desktopUser;
  frontDir = "/run/user/${toString config.users.users.${desktopUser}.uid}/clash-verge-rev";

in
{
  imports = [
    ./identity.nix
    ./source.nix
    ./strict-browser.nix
  ];

  users.groups.clash-verge-control.members = [ desktopUser ];

  # Persist the helper's requested core state under ProtectSystem=strict. The
  # core's data and staged configuration live outside the desktop user's home;
  # the service identity needs only the runtime directories the launcher creates.
  # Mode A is the boot default: the core only starts from an explicit
  # `clash-on`, so the helper service is started on demand rather than at
  # multi-user.target. Otherwise a failed launcher would restart forever at
  # boot while the intent is still direct.
  systemd.services.clash-verge.wantedBy = lib.mkForce [ ];
  # proxy-mode publishes /run/proxy-mode/boot-id; the helper's
  # ProtectProc=invisible view cannot read the kernel boot id itself.
  systemd.services.clash-verge.requires = [ "clash-core-network.service" "clash-network-observer.service" "clash-core-loopback.service" "clash-core-localhost.service" ];
  systemd.services.clash-verge.after = lib.mkAfter [ "proxy-mode.service" "clash-core-network.service" "clash-network-observer.service" "clash-core-loopback.service" "clash-core-localhost.service" ];
  systemd.services.clash-verge.bindsTo = [ "clash-core-network.service" ];

  systemd.services.clash-verge.serviceConfig = {
    NetworkNamespacePath = config.my.proxy.coreTopology.namespacePath;
    StateDirectory = [ "clash-verge-service" ];
    StateDirectoryMode = "0700";
    RuntimeDirectoryPreserve = "yes";
    UMask = "0077";

    # Do not let the root helper's StateDirectory recursively take ownership
    # of the core's cache at each restart. tmpfiles creates this path at boot.
    ReadWritePaths = [ "/var/lib/clash-core" ] ++ lib.optional config.my.proxy.controlledSources "/var/lib/clash-source";

    # No runtime ReadWritePaths: /run is writable under ProtectSystem=strict,
    # and naming /run/user/<uid> would fail the unit at boot before the user
    # session (and its runtime directory) exists.
    # The helper runs the launcher as root, which then drops to clash-core and
    # keeps only the capabilities the TUN and routing mark need. SETPCAP is
    # required for the capability-set and bounding-set transition.
    CapabilityBoundingSet = lib.mkForce [
      "CAP_SETUID"
      "CAP_SETGID"
      "CAP_SETPCAP"
      "CAP_CHOWN"

      # Reconcile core-owned runtime/state directories on subsequent starts.
      "CAP_FOWNER"
      "CAP_KILL"
      "CAP_DAC_OVERRIDE"
      "CAP_NET_ADMIN"
      "CAP_NET_RAW"
    ];

    # setpriv needs setresuid/setgroups/chown/capset; the helper still cannot
    # mount, load modules, reboot or use raw I/O.
    SystemCallFilter = lib.mkForce [
      "~@aio @clock @cpu-emulation @debug @keyring @memlock @module @mount @obsolete @pkey @raw-io @reboot @sandbox @swap @timer"
    ];
  };

  systemd.tmpfiles.rules = [ "d /var/lib/clash-core 0750 clash-core clash-core -" ];

  systemd.services.clash-gui-forwarder = {
    description = "Host Unix byte stream from the desktop to the core controller";
    partOf = [ "clash-verge.service" ];
    serviceConfig = {
      User = desktopUser;
      Group = "clash-core";
      ExecStart = "${pkgs.socat}/bin/socat UNIX-LISTEN:${frontDir}/verge-mihomo.sock,mode=0600,fork,unlink-early UNIX-CONNECT:/run/clash-verge-rev/core/mihomo.sock";
      # socat reports the stop signal as an exit status; a requested stop is not
      # a failed unit and must not be reported next to real failures.
      SuccessExitStatus = [ 143 ];
      NoNewPrivileges = true;
      CapabilityBoundingSet = "";
      RestrictAddressFamilies = [ "AF_UNIX" ];
      RestrictNamespaces = true;
      ProtectSystem = "strict";
      ProtectHome = "read-only";
      ReadWritePaths = [ frontDir ];
      PrivateTmp = true;
      PrivateDevices = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      MemoryMax = "64M";
      TasksMax = 128;
    };
  };

  systemd.services.profile-refresh-verify = {
    description = "Settle validated profile refreshes or restore the previous snapshot";
    after = [ "proxy-mode.service" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.python3}/bin/python3 ${./clash-verge-stage.py} verify-refresh ${config.programs.clash-verge.package.stageSettings} ${config.programs.clash-verge.package}/bin/verge-mihomo";
      UMask = "0077";
      NoNewPrivileges = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      ReadWritePaths = lib.optional config.my.proxy.controlledSources "/var/lib/clash-source";
      RestrictAddressFamilies = [ "AF_UNIX" ];
      TimeoutStartSec = "90s";
    };
  };

  systemd.timers.profile-refresh-verify = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "10s";
      OnUnitActiveSec = "10s";
      AccuracySec = "1s";
    };
  };

  # Keep the managed dual-stack TUN present even when the core starts on an
  # IPv4-only uplink. The helper's spawned core inherits this environment.
  systemd.services.clash-verge.environment = lib.optionalAttrs
    (config.networking.enableIPv6 && config.my.proxy.tunMode) { SKIP_SYSTEM_IPV6_CHECK = "1"; }
    // {
      LF_MANAGED_PROXY_POLICY = pkgs.writeText "mihomo-managed-policy.json"
        (builtins.toJSON config.my.proxy.managedProfile);
      # The helper may only exec this launcher, never a stale package path.
      LF_CLASH_CORE_PATH = "${config.programs.clash-verge.package}/bin/verge-mihomo";
      LF_CLASH_GUI_CONFIG = "${config.users.users.${desktopUser}.home}/.local/share/io.github.clash-verge-rev.clash-verge-rev/clash-verge.yaml";
      LF_CLASH_CONTROLLED_SOURCE = if config.my.proxy.controlledSources then "1" else "0";
    };
}
