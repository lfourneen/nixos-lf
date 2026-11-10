{ config, lib, pkgs, ... }:

let
  desktopUser = config.my.machine.desktopUser;

in
{
  imports = [ ./identity.nix ];
  users.groups.clash-verge-control.members = [ desktopUser ];

  # Persist the helper's requested core state under ProtectSystem=strict. The
  # core's data and staged configuration live outside the desktop user's home;
  # the service identity needs only the runtime directories the launcher creates.
  # Mode A is the boot default: the core only starts from an explicit
  # `clash-on`, so the helper service is started on demand rather than at
  # multi-user.target. Otherwise a failed launcher would restart forever at
  # boot while the intent is still direct.
  systemd.services.clash-verge.wantedBy = lib.mkForce [ ];

  systemd.services.clash-verge.serviceConfig = {
    StateDirectory = [ "clash-verge-service" ];
    StateDirectoryMode = "0700";
    RuntimeDirectoryPreserve = "yes";
    UMask = "0077";

    # Do not let the root helper's StateDirectory recursively take ownership
    # of the core's cache at each restart. tmpfiles creates this path at boot.
    ReadWritePaths = [ "/var/lib/clash-core" ];

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

  # Keep the managed dual-stack TUN present even when the core starts on an
  # IPv4-only uplink. The helper's spawned core inherits this environment.
  systemd.services.clash-verge.environment = lib.optionalAttrs
    (config.networking.enableIPv6 && config.my.proxy.tunMode) { SKIP_SYSTEM_IPV6_CHECK = "1"; }
    // {
      LF_MANAGED_PROXY_POLICY = pkgs.writeText "mihomo-managed-policy.json"
        (builtins.toJSON config.my.proxy.managedProfile);
      # The helper may only exec this launcher, never a stale package path.
      LF_CLASH_CORE_PATH = "${config.programs.clash-verge.package}/bin/verge-mihomo";
    };
}
