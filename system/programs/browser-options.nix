{ lib, ... }:

{
  # Declarative browser policy. Everything here lands in each browser's
  # enterprise-policy channel, so it survives profile loss/migration without
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
  # Note: Firefox-family `Preferences` policies are *locked* (the Zen settings
  # UI shows them as managed). That is the point of declaring them; to change
  # one, edit it here and rebuild.
  options.my.browser = {
    zen = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Manage Zen via the Flatpak systemconfig policies.json extension.";
      };

      preferences = lib.mkOption {
        type = lib.types.attrsOf (lib.types.oneOf [ lib.types.bool lib.types.int lib.types.float lib.types.str ]);
        default = {
          # --- DNS security -------------------------------------------------
          # DoH-only (mode 3) to Cloudflare. In this repo DNS already exits
          # encrypted (dnsmasq -> mihomo, or unbound DoT); this keeps the
          # browser's own resolver encrypted too.
          "network.trr.mode" = 3;
          "network.trr.uri" = "https://mozilla.cloudflare-dns.com/dns-query";

          # ECH needs the HTTPS/SVCB RR, which only a DoH resolver returns.
          "network.dns.echconfig.enabled" = true;
          "network.dns.http3_echconfig.enabled" = true;
          "network.dns.force_waiting_https_rr" = true;
          # GREASE ECH when no config is known, so its absence is not a tell.
          "security.tls.ech.grease_probability" = 100;

          "network.dns.disablePrefetch" = true;

          # --- WebRTC (IP leak over peer connections) -----------------------
          "media.peerconnection.enabled" = false;
          "media.peerconnection.ice.default_address_only" = true;

          # --- Tracking protection / anti-fingerprinting --------------------
          "privacy.fingerprintingProtection" = true;
          "privacy.fingerprintingProtection.overrides" = "-FontVisibilityBaseSystem,-FontVisibilityLangPack";
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
          User-branch prefs applied (and locked) through the policy engine.
          Defaults mirror the security-relevant prefs already present in this
          machine's Zen profile.
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
      # list mirrors what this machine's Zen profile already has installed.
      extensions = lib.mkOption {
        type = lib.types.attrsOf (lib.types.enum [ "normal_installed" "force_installed" ]);
        default = {
          "adguardadblocker@adguard.com" = "normal_installed";
          "CanvasBlocker@kkapsner.de" = "normal_installed";
          "firefox@tampermonkey.net" = "normal_installed";
          "uBlock0@raymondhill.net" = "normal_installed";
          "addon@darkreader.org" = "normal_installed";
          "x-comment-blocker@amahteru" = "normal_installed";
          "{73a6fe31-595d-460b-a920-fcc0f8843232}" = "normal_installed"; # NoScript
          "{5efceaa7-f3a2-4e59-a54b-85319448e305}" = "normal_installed"; # Immersive Translate
          "{8e515334-52b5-4cc5-b4e8-675d50af677d}" = "normal_installed"; # ScriptCat
          "{b9db16a4-6edc-47ec-a1f4-b86292ed211d}" = "normal_installed"; # Video DownloadHelper
          "ffext_basicvideoext@startpage24" = "normal_installed"; # Video Downloader professional
          "jid1-NIfFY2CA8fy1tg@jetpack" = "normal_installed"; # AdBlock for Firefox
          "{fda20e5e-61ff-43d2-b6aa-e8f323b648f3}" = "normal_installed"; # csdn和知乎增强器
          "{c1809289-dca4-44b3-9f2d-f3d34dcce930}" = "normal_installed"; # csdn便利助手
          "{3fc4b50b-4786-4753-b3d8-9350bbe4e1e6}" = "normal_installed"; # fuckcsdn
          "{8537be35-53d7-4b62-976f-11f190f9dc73}" = "normal_installed"; # csdn_cleaner
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
          # ...which needs DoH. `automatic` (not `secure`): use the templates
          # while the core is up, fall back in Mode A rather than black-holing.
          DnsOverHttpsMode = "automatic";
          DnsOverHttpsTemplates = "https://1.1.1.1/dns-query https://8.8.8.8/dns-query";
          # Telemetry off.
          MetricsReportingEnabled = false;
          UrlKeyedAnonymizedDataCollectionEnabled = false;
        };
        description = "Raw Chrome managed-policy JSON.";
      };
    };
  };
}

