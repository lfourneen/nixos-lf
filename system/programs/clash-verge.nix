{ pkgs, config, lib, ... }:

let
  guiProcess = pkgs.writeShellApplication {
    name = "clash-verge-gui-prepare";
    runtimeInputs = [ pkgs.python3 ];
    text = ''exec python3 ${./clash-verge-gui.py} "$@"'';
  };
  # Set the writable GUI preference without rebuilding the Rust application.
  guiDefaults = pkgs.writeShellApplication {
    name = "clash-verge-defaults";
    runtimeInputs = [ pkgs.yq-go pkgs.coreutils pkgs.procps ];
    text = builtins.readFile ./clash-verge-defaults.sh;
  };
  guiDefaultsCheck = pkgs.runCommand "proxy-gui-defaults" { } ''
    ${pkgs.nodejs}/bin/node ${../../tests/netsec/test_gui_defaults.js} \
      ${guiDefaults}/bin/clash-verge-defaults ${pkgs.yq-go}/bin/yq \
      ${lib.escapeShellArg config.my.proxy.delayTestUrl}
    ${pkgs.python3}/bin/python3 ${../../tests/netsec/test_gui_process.py} ${./clash-verge-gui.py}
    touch "$out"
  '';

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
      ./systemd/netsec/target_guard.go
      ./systemd/netsec/target_guard_test.go
    ];
  };
  managedCore = pkgs.unstable.mihomo.overrideAttrs (old: {
    doCheck = true;
    checkPhase = ''
      runHook preCheck
      export LF_MANAGED_TEST_POLICY=${pkgs.writeText "mihomo-managed-test-policy.json" (builtins.toJSON config.my.proxy.managedProfile)}
      export LF_MANAGED_TEST_HTTP_POLICY=${pkgs.writeText "mihomo-managed-test-http-policy.json" (builtins.toJSON httpOnlyPolicy)}
      export LF_MANAGED_TEST_LEGACY_PROFILE=${../../tests/netsec/legacy-managed-profile.json}
      go test -mod=vendor ./config ./hub/route -run '^TestLFManaged' -count=1
      go test -mod=vendor ./adapter/inbound ./transport/socks5 -count=1
      go test -mod=vendor ./component/lfguard -count=1
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
      nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ pkgs.makeWrapper ];
      postFixup = (old.postFixup or "") + ''
        mv "$out/bin/clash-verge" "$out/bin/.clash-verge-launcher"
        # The inner wrapper must expose the public name to clash-on/off. A
        # nested shell otherwise changes argv[0] to .clash-verge-launcher.
        substituteInPlace "$out/bin/.clash-verge-launcher" \
          --replace-fail 'exec -a "$0"' "exec -a \"$out/bin/clash-verge\""
        makeWrapper "$out/bin/.clash-verge-launcher" "$out/bin/clash-verge" \
          --run "${guiProcess}/bin/clash-verge-gui-prepare \"$out/bin/clash-verge\" || exit 1" \
          --run ${lib.escapeShellArg ''${guiDefaults}/bin/clash-verge-defaults ${lib.escapeShellArg config.my.proxy.delayTestUrl} || echo "Clash Verge: could not set the default delay-test URL" >&2''}
      '';
      passthru = (old.passthru or { }) // { inherit managedCore guiDefaultsCheck; };
    });
    serviceMode = true;
    group = "clash-verge-control";
  };
  assertions = [{
    assertion = toString config.programs.clash-verge.package.unwrapped
      == toString pkgs.unstable.clash-verge-rev.unwrapped;
    message = "The GUI delay default must reuse the unmodified Clash Verge application.";
  }];
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

