{ config, lib, pkgs, ... }:

let
  cfg = config.my.browser;
  relay = "127.0.0.1:${toString config.my.machine.ports.gostHttp}";
  # Firefox 159's Preferences handler rejects privacy.resistFingerprinting,
  # most privacy.* and telemetry prefs, and arbitrary security.* names.
  # Keep these in user.js for the separate strict profile instead of shipping
  # policies that the browser silently refuses. Supported policy prefs lock.
  allowedPrefixes = [ "browser." "network." "media." "dom." "layout."
    "extensions." "datareporting.policy." "app.update." "intl." "geo." "webgl.disabled"
    "privacy.fingerprintingProtection" "privacy.globalprivacycontrol.enabled" ];
  policyPreferences = (lib.filterAttrs (name: _: lib.any
    (prefix: lib.hasPrefix prefix name) allowedPrefixes) cfg.zen.preferences)
    // {
      # HTTP/HTTPS use the mode-aware relay; clear a previous SOCKS override.
      "network.proxy.socks" = "";
      "network.proxy.socks_port" = 0;
      "network.proxy.socks_remote_dns" = false;
      "network.proxy.socks5_remote_dns" = false;
    };
  userJs = pkgs.writeText "zen-strict-user.js" ("// Managed dedicated privacy profile; restart after a rebuild.\n"
    + lib.concatStringsSep "\n" (lib.mapAttrsToList
      (name: value: "user_pref(${builtins.toJSON name}, ${builtins.toJSON value});") cfg.zen.preferences) + "\n");
  fonts = pkgs.writeText "browser-fonts.conf" ''
    <?xml version="1.0"?>
    <!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
    <fontconfig>
      <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
      <!-- Keep only runtime/application fonts; discard host and user dirs. -->
      <reset-dirs/>
      <dir>/usr/share/fonts</dir>
      <dir>/app/share/fonts</dir>
      <dir>/app/zen/fonts</dir>
      <!-- Fixed CJK coverage, independent of the host's personal font set. -->
      <dir>${pkgs.noto-fonts-cjk-sans}/share/fonts</dir>
      <cachedir prefix="xdg">fontconfig-netsec</cachedir>
    </fontconfig>
  '';

  # Chrome / Chromium managed policy. Flatpak Chrome declares `host-etc` (host
  # /etc appears at /run/host/etc) and its launcher (chrome.sh) symlinks
  # /run/host/etc/opt/chrome/policies/{managed,recommended}/*.json into the
  # sandbox, so a host file at /etc/opt/chrome/policies/managed is what it reads.
  # Verify with chrome://policy.
  chromePolicies = cfg.chrome.policies // {
    ProxySettings = {
      ProxyMode = "fixed_servers";
      ProxyServer = "http://${relay}";
      ProxyBypassList = "<-loopback>";
    };
  };
  chromePolicyFile = pkgs.writeText "chrome-managed-policies.json" (builtins.toJSON chromePolicies);

  # Extension ID -> AMO install entry. AMO resolves the latest signed XPI by ID
  # (`/downloads/latest/<id>/latest.xpi`), so no version/hash is pinned. Braces
  # in the GUID-style IDs are percent-encoded so the URL stays valid.
  encId = id: lib.replaceStrings [ "{" "}" ] [ "%7B" "%7D" ] id;
  extensionSettings = lib.mapAttrs
    (id: mode: {
      installation_mode = mode;
      install_url = "https://addons.mozilla.org/firefox/downloads/latest/${encId id}/latest.xpi";
    })
    cfg.zen.extensions;

  # Firefox / Zen policy. The `preferences` option is folded into the policy
  # tree's `Preferences` key; an explicit `policies.Preferences`/`ExtensionSettings`
  # is merged under it so freeform policies can still extend those keys.
  zenPolicy = builtins.toJSON {
    policies = cfg.zen.policies // {
      Preferences = (cfg.zen.policies.Preferences or { }) // lib.mapAttrs
        (_: value: { Value = value; Status = "locked";
          Type = if builtins.isBool value then "boolean" else if builtins.isInt value then "number" else "string";
        }) policyPreferences;
      ExtensionSettings = (cfg.zen.policies.ExtensionSettings or { })
        // lib.optionalAttrs cfg.strictPrivacy { "*".installation_mode = "blocked"; }
        // extensionSettings;
      DisableTelemetry = true;
      DisableFirefoxStudies = true;
      DisableEncryptedClientHello = false;
      EnableTrackingProtection = { Value = true; Locked = true; Cryptomining = true; Fingerprinting = true; };
    } // {
      Proxy = {
        Mode = "manual";
        Locked = true;
        HTTPProxy = relay;
        SSLProxy = relay;
        UseHTTPProxyForAllProtocols = true;
        Passthrough = "";
      };
      DNSOverHTTPS = {
        Enabled = true;
        Locked = true;
        Fallback = !cfg.zen.dohOnly;
        ProviderURL = cfg.zen.preferences."network.trr.uri";
        ExcludedDomains = [ ];
      };
      CaptivePortal = true;
      NetworkPrediction = false;
      DisableFirefoxAccounts = true;
    };
  };
  zenPolicyFile = pkgs.writeText "zen-policies.json" zenPolicy;

  # Flatpak system-wide extension directory (Zen is installed system-wide). The
  # arch/branch mirror the installed ref (app/app.zen_browser.zen/x86_64/stable).
  zenExt = "/var/lib/flatpak/extension/app.zen_browser.zen.systemconfig/x86_64/stable";
  launcherSettings = pkgs.writeText "browser-guard.json" (builtins.toJSON {
    flatpak = "${pkgs.flatpak}/bin/flatpak";
    stateModule = ./systemd/netsec/state.py;
    healthModule = ./systemd/netsec/health.py;
    healthSettings = config.my.proxy.healthSettings;
    maxAge = config.my.proxy.stateMaxAgeSeconds;
    verificationMaxAge = config.my.proxy.verificationMaxAgeSeconds;
    inherit userJs;
    zenPolicy = zenPolicyFile;
    installedZenPolicy = "${zenExt}/policies/policies.json";
    chromePolicy = chromePolicyFile;
    installedChromePolicy = "/etc/opt/chrome/policies/managed/browser.json";
    strictPrivacy = cfg.strictPrivacy;
    fonts = fonts;
    fontDirectories = [ "/usr/share/fonts" "/app/share/fonts" "/app/zen/fonts"
      "${pkgs.noto-fonts-cjk-sans}/share/fonts" ];
    installedZenFonts = "${zenExt}/fonts.conf";
    installedChromeFonts = "/etc/netsec-browser/fonts.conf";
  });
  launcher = pkgs.writeShellScriptBin "browser-private" ''
    exec ${pkgs.python3}/bin/python3 -I ${./browser-guard.py} ${launcherSettings} "$@"
  '';

in
{
  config = lib.mkMerge [
    { my.browser.generated = { zen = zenPolicyFile; chrome = chromePolicyFile;
        preferences = userJs; inherit fonts; }; }
    { environment.systemPackages = [ launcher ]; }
    (lib.mkIf cfg.strictPrivacy {
      # host-etc cannot follow a NixOS /etc/static symlink inside Flatpak.
      environment.etc."netsec-browser/fonts.conf" = { source = fonts; mode = "0644"; };
      services.flatpak.overrides.settings = lib.genAttrs
        ([ ] ++ lib.optional cfg.zen.enable "app.zen_browser.zen"
          ++ lib.optional cfg.chrome.enable "com.google.Chrome")
        (id: {
          Environment.FONTCONFIG_FILE = if id == "app.zen_browser.zen"
            then "/app/etc/zen/fonts.conf" else "/run/host/etc/netsec-browser/fonts.conf";
          Context = {
            filesystems = [ "${pkgs.noto-fonts-cjk-sans}:ro" ];
            sockets = [ "!x11" "!fallback-x11" "!cups" "!pcsc" "wayland" ];
            devices = [ "!all" "dri" ];
          };
        });
    })
    (lib.mkIf cfg.chrome.enable {
      # Chrome's flatpak launcher symlinks this into its sandbox (see above).
      environment.etc."opt/chrome/policies/managed/browser.json" = {
        source = chromePolicyFile;
        mode = "0644";
      };
    })

    (lib.mkIf cfg.zen.enable {
      # Explicit replacement on each changed generation, without store symlinks
      # inside the Flatpak extension. Launch refuses a stale policy copy.
      systemd.services.browser-policy-install = {
        description = "Install the managed Zen Flatpak policy extension";
        wantedBy = [ "multi-user.target" ];
        before = [ "flatpak-managed-install.service" ];
        restartTriggers = [ zenPolicyFile fonts ];
        serviceConfig.Type = "oneshot";
        script = ''
          set -eu
          install -d -m 0755 ${zenExt}/policies
          refresh() {
            if [ -L "$2" ] || ! ${pkgs.diffutils}/bin/cmp -s "$1" "$2"; then
              install -m 0644 "$1" "$2.new"
              mv -fT "$2.new" "$2"
            fi
          }
          refresh ${zenPolicyFile} ${zenExt}/policies/policies.json
          refresh ${fonts} ${zenExt}/fonts.conf
        '';
      };
      systemd.timers.browser-policy-install = {
        description = "Reconcile the managed Zen Flatpak policy extension";
        wantedBy = [ "timers.target" ];
        timerConfig = { OnBootSec = "2min"; OnUnitInactiveSec = "5min"; };
      };
    })
  ];
}

