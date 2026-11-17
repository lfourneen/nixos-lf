{ pkgs, config, lib, ... }:

let
  guiProcess = pkgs.writeShellApplication {
    name = "clash-verge-gui-prepare";
    runtimeInputs = [ pkgs.python3 ];
    text = ''exec python3 ${./gui/clash-verge-gui.py} "$@"'';
  };

  # Set the writable GUI preference without rebuilding the Rust application.
  guiDefaults = pkgs.writeShellApplication {
    name = "clash-verge-defaults";
    runtimeInputs = [ pkgs.yq-go pkgs.coreutils pkgs.python3 pkgs.util-linux ];
    text = builtins.readFile ./gui/clash-verge-defaults.sh;
  };

  guiDefaultsCheck = pkgs.runCommand "proxy-gui-defaults" { } ''
    ${pkgs.nodejs}/bin/node ${../../../tests/home/clash/test_gui_defaults.js} \
      ${guiDefaults}/bin/clash-verge-defaults ${pkgs.yq-go}/bin/yq \
      ${lib.escapeShellArg config.my.proxy.delayTestUrl}
    ${pkgs.python3}/bin/python3 ${../../../tests/home/clash/test_gui_process.py} ${./gui/clash-verge-gui.py}
    touch "$out"
  '';

  desktopUser = config.my.machine.desktopUser;
  desktopUid = config.users.users.${desktopUser}.uid;
  guiData = "${config.users.users.${desktopUser}.home}/.local/share/io.github.clash-verge-rev.clash-verge-rev";

  stageSettings = pkgs.writeText "clash-stage-settings.json" (builtins.toJSON {
    gui_data = guiData;
    source_root = if config.my.proxy.controlledSources then "/var/lib/clash-source" else null;
    desktop_uid = desktopUid;
    runtime = "/run/clash-verge-rev";
    state = "/run/proxy-mode";
    refresh_policy = config.my.proxy.profileRefreshPolicy;
    refresh_failure_grace_seconds = config.my.proxy.profileRefreshFailureGraceSeconds;
    systemctl = "${pkgs.systemd}/bin/systemctl";
    audit_policy = config.my.proxy.subscriptionAudit;
    audit_auto_approve = config.my.proxy.subscriptionAuditAutoApprove;
    audit_python = "${pkgs.python3.withPackages (p: [ p.pyyaml ])}/bin/python3";
    audit_tool = "${./service/subscription_audit.py}";
    readiness_tool = "${../../config/network/proxy/state.py}";
    state_max_age = config.my.proxy.stateMaxAgeSeconds;
    verification_max_age = config.my.proxy.verificationMaxAgeSeconds;
    require_tun = config.my.proxy.tunMode;

    routing_state = if config.my.proxy.runtimeRouting then "/run/proxy-mode/routing.json" else null;
    audit_settings = if config.my.proxy.runtimeRouting then "/run/proxy-mode/routing-active/audit.json" else pkgs.writeText "subscription-audit-settings.json" (builtins.toJSON {
      strict_semantics = config.my.proxy.controlledSources;
      insecure_exceptions = config.my.proxy.insecureProxyExceptions;
      subscription_trust = config.my.proxy.subscriptionTrust;
      sensitive_domains = config.my.proxy.sensitiveDomains;
      allowed_hosts = config.my.proxy.subscriptionAuditAllowedHosts;
      allowed_server_cidrs = config.my.proxy.subscriptionAuditServerCidrs;
      protected_routes = config.my.proxy.subscriptionAuditProtectedRoutes;
    });

    data = "/var/lib/clash-core";
    setpriv = "${pkgs.util-linux}/bin/setpriv";
    yq = "${pkgs.yq-go}/bin/yq";
    snapshot_tool = "${./service/clash-verge-stage.py}";
    managed_core = "${managedCore}/bin/mihomo";
    managed_policy = config.my.proxy.activeManagedPolicy;
  });

  # Root launcher behind bin/verge-mihomo. It enforces the clash-on intent,
  # stages and validates the effective configuration, and drops the core to a
  # dedicated service identity before it opens a TUN or listener.
  coreLauncher = pkgs.writeShellScript "verge-mihomo-launcher" ''
    export PATH=${lib.makeBinPath [ pkgs.bash pkgs.coreutils pkgs.gnused pkgs.gnugrep pkgs.socat pkgs.util-linux ]}:$PATH
    ${builtins.replaceStrings
      [ "@desktopUid@" "@guiData@" "@clashCore@" "@python@" "@stageTool@" "@stageSettings@" "@controlGroup@" "@namespacePath@" "@systemctl@" ]
      [ (toString desktopUid) guiData "clash-core" "${pkgs.python3}/bin/python3" "${./service/clash-verge-stage.py}" (toString stageSettings) config.programs.clash-verge.group config.my.proxy.coreTopology.namespacePath "${pkgs.systemd}/bin/systemctl" ]
      (builtins.readFile ./service/clash-verge-core-launcher.sh)}
  '';

  # The packaged helper (2.3.3) execs the client-supplied core_path as root.
  # Pin the single spawn choke point to a root-owned, non-writable path; the
  # remaining authorization lives in the launcher and the firewall.
  patchedService = pkgs.unstable.clash-verge-rev.service.overrideAttrs (old: {
    postPatch = (old.postPatch or "") + ''
      ${pkgs.python3}/bin/python3 ${./service/patch_clash_service.py} .
    '';
  });

  managedGui = pkgs.unstable.clash-verge-rev.unwrapped.overrideAttrs (old: {
    postPatch = (old.postPatch or "") + ''
      ${pkgs.python3}/bin/python3 ${./gui/patch_clash_gui.py} . ${lib.boolToString config.my.proxy.tunMode}
    '';
  });

  testPolicy = policy: policy // {
    lf-private-targets = policy.lf-private-targets // {
      blocked-domains-file = if config.my.proxy.dnsBlocklistFile == null then ""
        else toString config.my.proxy.dnsBlocklistFile;
    };
  };

  # HTTP-only policy used by the core checks.
  httpOnlyConfig = config // {
    my = config.my // {
      proxy = config.my.proxy // {
        tunMode = false;
        managedDns = config.my.proxy.managedDns // { enhanced-mode = "redir-host"; };
      };
    };
  };

  httpOnlyPolicy = (import ../../config/network/proxy/profile.nix {
    inherit lib;
    config = httpOnlyConfig;
  }).options.my.proxy.managedProfile.default;

  # Rebuild the core only when its source patches change.
  mihomoDir = ./mihomo;
  managedSources = lib.fileset.toSource {
    root = mihomoDir;
    fileset = lib.fileset.unions [
      ./mihomo/patches ./mihomo/guard ./mihomo/config
      ./mihomo/controller ./mihomo/tests
    ];
  };

  managedCore = pkgs.unstable.mihomo.overrideAttrs (old: {
    doCheck = true;
    checkPhase = ''
      runHook preCheck
      export LF_MANAGED_TEST_POLICY=${pkgs.writeText "mihomo-managed-test-policy.json" (builtins.toJSON (testPolicy config.my.proxy.managedProfile))}
      export LF_MANAGED_TEST_HTTP_POLICY=${pkgs.writeText "mihomo-managed-test-http-policy.json" (builtins.toJSON (testPolicy httpOnlyPolicy))}
      export LF_MANAGED_TEST_LEGACY_PROFILE=${../../../tests/network/proxy/legacy-managed-profile.json}
      go test -mod=vendor ./config ./hub/route -run '^TestLFManaged' -count=1
      go test -mod=vendor ./adapter/inbound ./transport/socks5 -count=1
      go test -mod=vendor github.com/metacubex/sing-tun -run '^TestLFManaged' -count=1
      go test -mod=vendor ./component/lfguard -count=1
      go test -mod=vendor ./adapter ./adapter/provider -run '^TestLFManaged' -count=1
      go test -mod=vendor ./dns -run '^TestLFManaged' -count=1
      go test -mod=vendor ./adapter/outbound -run '^TestLFManagedDirect' -count=1
      go test -mod=vendor ./tunnel -run '^TestLFManaged' -count=1
      runHook postCheck
    '';

    postConfigure = (old.postConfigure or "") + ''
      chmod -R u+w vendor/github.com/metacubex/sing-tun
      ${pkgs.python3}/bin/python3 ${managedSources}/patches/patch_mihomo.py .
      ${pkgs.python3}/bin/python3 ${managedSources}/patches/patch_sing_tun.py .
    '';
  });

in
{
  imports = [ ./service/runtime.nix ];

  # Service mode drops the core to its pinned UID; the firewall permits its mark.
  # Source patches enforce the managed profile and TUN/guest redirect behavior.
  # Exact anchors reject incompatible upstream sources at build time.
  programs.clash-verge = {
    enable = true;
    package = (pkgs.unstable.clash-verge-rev.override {
      mihomo = managedCore;
    }).overrideAttrs (old: {
      installPhase = builtins.replaceStrings [ (toString old.passthru.unwrapped) ] [ (toString managedGui) ] old.installPhase;
      nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ pkgs.makeWrapper ];
      postFixup = (old.postFixup or "") + ''
        # Replace the helper copied by the GUI package with the locally pinned
        # build. The module starts bin/clash-verge-service from this package.
        rm -f "$out/bin/clash-verge-service"
        install -m 0755 ${patchedService}/bin/clash-verge-service "$out/bin/clash-verge-service"
        # bin/.verge-mihomo-wrapped is the package's symlink to the real
        # mihomo binary; keep it as the launcher's read-only passthrough
        # target and replace only the public wrapper.
        rm -f "$out/bin/verge-mihomo"
        install -m 0555 ${coreLauncher} "$out/bin/verge-mihomo"
        mv "$out/bin/clash-verge" "$out/bin/.clash-verge-launcher"
        # The inner wrapper must expose the public name to clash-on/off. A
        # nested shell otherwise changes argv[0] to .clash-verge-launcher.
        substituteInPlace "$out/bin/.clash-verge-launcher" \
          --replace-fail 'exec -a "$0"' "exec -a \"$out/bin/clash-verge\""
        makeWrapper "${guiProcess}/bin/clash-verge-gui-prepare" "$out/bin/clash-verge" \
          --add-flags "--launch $out/bin/clash-verge $out/bin/.clash-verge-launcher ${guiDefaults}/bin/clash-verge-defaults" \
          --add-flags ${lib.escapeShellArg (lib.escapeShellArg config.my.proxy.delayTestUrl)}
      '';
      passthru = (old.passthru or { }) // {
        inherit managedCore guiDefaultsCheck stageSettings;
        managedService = patchedService;
        unwrapped = managedGui;
      };
    });

    serviceMode = true;
    group = "clash-verge-control";
  };

}
