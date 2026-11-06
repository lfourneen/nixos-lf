{ lib, config, ... }:

{
  # Declarative browser policy and dedicated strict-profile preferences.
  # Supported settings use the enterprise-policy channel; other Gecko prefs
  # use a generated user.js in the separate strict profile, without
  # replaying UI toggles:
  #   * Chrome (Flatpak): a managed policy JSON under /etc/opt/chrome/policies/
  #     managed, which the flatpak's launcher symlinks into its sandbox.
  #   * Zen (Flatpak): the app.zen_browser.zen.systemconfig extension, read from
  #     /app/etc/zen/policies/policies.json in the sandbox.
  #
  # Scope: security/privacy prefs (DNS, ECH, fingerprinting, tracking
  # protection, WebRTC) plus the AMO extensions listed under `extensions`. It is
  # NOT a profile migrator and never touches bookmarks/tabs/history/site data.
  #
  # Only supported enterprise Preferences are locked. user.js settings (RFP
  # among them) are restored at launch, but are not runtime locks.
  options.my.browser = {
    generated = lib.mkOption {
      type = lib.types.attrsOf lib.types.path;
      internal = true;
      readOnly = true;
      description = "Rendered browser policy/profile artifacts for isolated verification.";
    };
    strictPrivacy = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Browser fingerprinting, font and extension protection. Independent of the A/B/C network choice; does not require proxy mode or change boot intent.";
    };
    zen = {
      dohOnly = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Require browser DoH even in Mode A; false permits the managed system resolver when DoH is unavailable.";
      };
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Manage Zen via the Flatpak systemconfig policies.json extension.";
      };

      preferences = lib.mkOption {
        type = lib.types.attrsOf (lib.types.oneOf [ lib.types.bool lib.types.int lib.types.str ]);
        default = {
          # --- DNS security -------------------------------------------------
          # Prefer DoH while preserving the managed resolver for captive
          # portals and Mode A. The explicit relay follows the system mode.
          "network.trr.mode" = if config.my.browser.zen.dohOnly then 3 else 2;
          "network.trr.uri" = "https://1.1.1.1/dns-query";

          # ECH discovery uses HTTPS/SVCB records; these also exist in ordinary DNS.
          "network.dns.echconfig.enabled" = true;
          "network.dns.http3_echconfig.enabled" = true;
          "network.dns.force_waiting_https_rr" = true;
          # GREASE ECH when no config is known, so its absence is not a tell.
          "security.tls.ech.grease_probability" = 100;

          "network.dns.disablePrefetch" = true;
          "network.prefetch-next" = false;
          "network.predictor.enabled" = false;
          "network.http.speculative-parallel-limit" = 0;
          "network.proxy.failover_direct" = false;
          "network.proxy.allow_hijacking_localhost" = true;

          # --- WebRTC (IP leak over peer connections) -----------------------
          "media.peerconnection.enabled" = false;
          "media.peerconnection.ice.default_address_only" = true;

          # --- Tracking protection / anti-fingerprinting --------------------
          "privacy.fingerprintingProtection" = true;
          # Unsupported by enterprise Preferences: installed in the dedicated
          # strict profile's user.js, alongside the supported locked policies.
          "privacy.resistFingerprinting" = config.my.browser.strictPrivacy;
          "privacy.resistFingerprinting.letterboxing" = config.my.browser.strictPrivacy;
          "privacy.spoof_english" = 2;
          "layout.css.font-visibility.standard" = 1;
          "layout.css.font-visibility.trackingprotection" = 1;
          "layout.css.font-visibility.private" = 1;
          "browser.display.use_document_fonts" = 1;
          "browser.contentblocking.category" = "strict";
          "network.cookie.cookieBehavior" = 5;
          # HTTPS-First permits HTTP-only login portals; never force HTTPS on
          # local addresses such as the unauthed 192.168.1.1 gateway.
          "dom.security.https_only_mode" = false;
          "dom.security.https_only_mode_pbm" = false;
          "dom.security.https_only_mode.upgrade_local" = false;
          "dom.security.https_first" = true;
          "dom.security.https_first_for_local_addresses" = false;
          "webgl.disabled" = config.my.browser.strictPrivacy;
          "dom.webgpu.enabled" = false;
          "geo.enabled" = false;
          "privacy.trackingprotection.enabled" = true;
          "privacy.trackingprotection.socialtracking.enabled" = true;
          "privacy.trackingprotection.emailtracking.enabled" = true;
          "privacy.query_stripping.enabled" = true;
          "privacy.query_stripping.enabled.pbmode" = true;
          "privacy.bounceTrackingProtection.mode" = 1;
          "privacy.annotate_channels.strict_list.enabled" = true;
          "privacy.globalprivacycontrol.enabled" = true;
          "privacy.clearOnShutdown_v2.formdata" = true;

          # --- Telemetry / studies / crash reporting ------------------------
          "toolkit.telemetry.enabled" = false;
          "toolkit.telemetry.unified" = false;
          "toolkit.telemetry.archive.enabled" = false;
          "toolkit.telemetry.shutdownPingSender.enabled" = false;
          "toolkit.telemetry.newProfilePing.enabled" = false;
          "toolkit.telemetry.updatePing.enabled" = false;
          "toolkit.telemetry.bhrPing.enabled" = false;
          "toolkit.telemetry.firstShutdownPing.enabled" = false;
          "datareporting.healthreport.uploadEnabled" = false;
          "datareporting.policy.dataSubmissionEnabled" = false;
          "app.normandy.enabled" = false;
          "app.shield.optoutstudies.enabled" = false;
          "browser.discovery.enabled" = false;
          "browser.newtabpage.activity-stream.feeds.telemetry" = false;
          "browser.newtabpage.activity-stream.telemetry" = false;
          "browser.ping-centre.telemetry" = false;
          "browser.tabs.crashReporting.sendReport" = false;
          "browser.crashReports.unsubmittedCheck.autoSubmit2" = false;
          "extensions.htmlaboutaddons.recommendations.enabled" = false;
        };
        description = ''
          Supported prefs are locked by enterprise policy. All values also
          seed user.js in the dedicated strict profile; unsupported policy
          prefs such as RFP take effect there and are not runtime locks.
        '';
      };

      policies = lib.mkOption {
        type = lib.types.attrsOf lib.types.anything;
        default = { };
        description = ''
          Raw Firefox/Zen policy tree merged alongside `preferences` (which
          becomes `Preferences`). Use it for anything this module does not model:
          SearchEngines, Bookmarks, DisableDeveloperTools, ...
        '';
      };

      # AMO extensions to (re)install, keyed by extension ID (not display name).
      # The install URL is derived from the ID -- AMO resolves the latest signed
      # XPI by ID (`/downloads/latest/<id>/latest.xpi`), so nothing needs a hash
      # or a version bump. Only the ID changing would need an edit here, and the
      # default keeps a single blocker instead of stacking overlapping addons.
      extensions = lib.mkOption {
        type = lib.types.attrsOf (lib.types.enum [ "normal_installed" "force_installed" ]);
        default = {
          "uBlock0@raymondhill.net" = "force_installed";
          "{fb25c100-22ce-4d5a-be7e-75f3d6f0fc13}" = "normal_installed";  # KISS Translator
        };
        description = ''
          Map of extension ID -> "normal_installed" (install, user may remove) or
          "force_installed" (install and lock, cannot be disabled/removed).
        '';
      };
    };

    chrome = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Manage Chrome via /etc/opt/chrome/policies/managed.";
      };

      policies = lib.mkOption {
        type = lib.types.attrsOf lib.types.anything;
        default = {
          # ECH wherever the origin publishes an ECHConfig...
          EncryptedClientHelloEnabled = true;
          # Keep managed resolver fallback available for captive portals in A.
          DnsOverHttpsMode = "automatic";
          DnsOverHttpsTemplates = "https://1.1.1.1/dns-query https://8.8.8.8/dns-query";
          # Telemetry off.
          MetricsReportingEnabled = false;
          UrlKeyedAnonymizedDataCollectionEnabled = false;
          WebRtcIPHandling = "disable_non_proxied_udp";
          NetworkPredictionOptions = 2;
          DefaultLocalFontsSetting = 2;
          BlockThirdPartyCookies = true;
          BackgroundModeEnabled = false;
          SyncDisabled = true;
          BrowserSignin = 0;
          ExtensionInstallBlocklist = [ "*" ];
        };
        description = "Raw Chrome managed-policy JSON.";
      };
    };
  };
}

