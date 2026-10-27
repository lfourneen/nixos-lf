{ pkgs, config, lib, ... }:

let
  # HTTP-only policy used by the core checks.
  httpOnlyConfig = config // {
    my = config.my // {
      proxy = config.my.proxy // {
        tunMode = false;
        managedDns = config.my.proxy.managedDns // { enhanced-mode = "redir-host"; };
      };
    };
  };
  httpOnlyPolicy = (import ../config/proxy-profile.nix {
    inherit lib;
    config = httpOnlyConfig;
  }).options.my.proxy.managedProfile.default;
  # Rebuild the core only when its source patches change.
  managedSources = lib.fileset.toSource {
    root = ./systemd/netsec;
    fileset = lib.fileset.unions [
      ./systemd/netsec/patch_mihomo.py
      ./systemd/netsec/patch_sing_tun.py
      ./systemd/netsec/patch_socks.py
      ./systemd/netsec/socks_addr_test.go
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
      export LF_MANAGED_TEST_HTTP_POLICY=${pkgs.writeText "mihomo-managed-test-http-policy.json" (builtins.toJSON httpOnlyPolicy)}
      go test -mod=vendor ./config ./hub/route -run '^TestLFManaged' -count=1
      go test -mod=vendor ./adapter/inbound ./transport/socks5 -count=1
      runHook postCheck
    '';
    postConfigure = (old.postConfigure or "") + ''
      chmod -R u+w vendor/github.com/metacubex/sing-tun
      ${pkgs.python3}/bin/python3 ${managedSources}/patch_mihomo.py .
      ${pkgs.python3}/bin/python3 ${managedSources}/patch_sing_tun.py .
      ${pkgs.python3}/bin/python3 ${managedSources}/patch_socks.py .
    '';
  });

in
{
  # Service mode runs the core as root; the firewall permits its routing mark.
  # Source patches enforce the managed profile and correct nftables interval
  # endpoints and SOCKS address parsing. Exact anchors reject incompatible
  # upstream sources at build time.
  programs.clash-verge = {
    enable = true;
    package = (pkgs.unstable.clash-verge-rev.override {
      mihomo = managedCore;
    }).overrideAttrs (old: {
      passthru = (old.passthru or { }) // { inherit managedCore; };
    });
    serviceMode = true;
    group = "clash-verge-control";
  };
  users.groups.clash-verge-control.members = [ config.my.machine.desktopUser ];

  # Persist the helper's requested core state under ProtectSystem=strict.
  systemd.services.clash-verge.serviceConfig.StateDirectory = [ "clash-verge-service" ];
  # Keep the managed dual-stack TUN present even when the core starts on an
  # IPv4-only uplink. The helper's spawned core inherits this environment.
  systemd.services.clash-verge.environment = lib.optionalAttrs
    (config.networking.enableIPv6 && config.my.proxy.tunMode) { SKIP_SYSTEM_IPV6_CHECK = "1"; }
    // { LF_MANAGED_PROXY_POLICY = pkgs.writeText "mihomo-managed-policy.json"
      (builtins.toJSON config.my.proxy.managedProfile); };
}

