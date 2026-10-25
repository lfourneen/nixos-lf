{ pkgs, config, lib, ... }:

let
  # DNS/state helper edits must not rebuild the root core or its vendored Go tree.
  managedSources = lib.fileset.toSource {
    root = ./systemd/netsec;
    fileset = lib.fileset.unions [
      ./systemd/netsec/patch_mihomo.py
      ./systemd/netsec/managed_profile.go
      ./systemd/netsec/managed_profile_test.go
      ./systemd/netsec/managed_route.go
      ./systemd/netsec/managed_route_test.go
    ];
  };
  managedCore = pkgs.unstable.mihomo.overrideAttrs (old: {
    doCheck = true;
    checkPhase = ''
      runHook preCheck
      export LF_MANAGED_TEST_POLICY=${pkgs.writeText "mihomo-managed-test-policy.json" (builtins.toJSON config.my.proxy.managedProfile)}
      go test -mod=vendor ./config ./hub/route -run '^TestLFManaged' -count=1
      runHook postCheck
    '';
    postConfigure = (old.postConfigure or "") + ''
      ${pkgs.python3}/bin/python3 ${managedSources}/patch_mihomo.py .
      chmod -R u+w vendor/github.com/metacubex/sing-tun
      patch -p1 -d vendor/github.com/metacubex/sing-tun \
        < ${pkgs.fetchurl {
          name = "sing-tun-nftables-interval-end.patch";
          url = "https://github.com/SagerNet/sing-tun/commit/7954dd6e20105bd48659dbb8bfc145c0a2e86a6d.patch";
          hash = "sha256-cLXIVVBHXzda1cQ+49/iQeSLfaXt9Ba7t1xhNAYlU/M=";
        }}
    '';
  });

in
{
  # Service Mode: core runs as root, which `proxyKillSwitch` in network.nix
  # relies on. After rebuild, turn on "Service Mode" in the Clash Verge GUI.
  #
  # Two source-level changes ride on the pinned core and both fail loudly:
  #   * patch_mihomo.py replaces exact anchors and refuses any anchor count != 1;
  #   * the sing-tun patch below fails if its context is missing or it is
  #     already applied. Delete it once the vendored sing-tun contains 7954dd6.
  # Re-check both on every mihomo/nixpkgs bump. The managed policy is ours
  # (upstream has no equivalent): keep it unless the core or Verge grows a final
  # config validator, or A06 enforcement is deliberately dropped -- in which
  # case also remove the managed-policy expectation in proxy-mode.nix.
  programs.clash-verge = {
    enable = true;
    package = (pkgs.unstable.clash-verge-rev.override {
      # Backport SagerNet/sing-tun 7954dd6 "Fix nftables interval end when
      # range hits max address" (missing in the metacubex fork): a route-exclude
      # range ending at the max address (255.255.255.255/32, ff00::/8) added the
      # same key twice, which the kernel rejects with EEXIST ("auto redirect:
      # file exists"), so TUN never comes up.
      mihomo = managedCore;
    }).overrideAttrs (old: {
      passthru = (old.passthru or { }) // { inherit managedCore; };
    });
    serviceMode = true;
    group = "clash-verge-control";
  };
  users.groups.clash-verge-control.members = [ config.my.machine.desktopUser ];

  # The service persists its "desired state" (core up + TUN) under
  # /var/lib/clash-verge-service; without this it logs "Failed to persist
  # desired state: failed to create desired state directory" on every core
  # start, and the GUI treats the start as failed and leaves TUN down. The
  # upstream module sets ProtectSystem=strict with only a RuntimeDirectory, so
  # the state dir is read-only; StateDirectory creates and whitelists it.
  systemd.services.clash-verge.serviceConfig.StateDirectory = [ "clash-verge-service" ];
  # Keep the managed dual-stack TUN present even when the core starts on an
  # IPv4-only uplink. The helper's spawned core inherits this environment.
  systemd.services.clash-verge.environment = lib.optionalAttrs
    (config.networking.enableIPv6 && config.my.proxy.tunMode) { SKIP_SYSTEM_IPV6_CHECK = "1"; }
    // { LF_MANAGED_PROXY_POLICY = pkgs.writeText "mihomo-managed-policy.json"
      (builtins.toJSON config.my.proxy.managedProfile); };
}

