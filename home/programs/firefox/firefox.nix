{ config, lib, osConfig, pkgs, ... }:

let
  # Browser traffic follows the same gost relay as the rest of the session.
  relay = "127.0.0.1:${toString osConfig.my.machine.ports.gostHttp}";
  strictPrivacy = true;
  compat = config.my.firefox.videoSiteCompat;
  transparent = config.my.firefox.networkPath == "transparent";
  profile = "${config.programs.firefox.configPath}/lfour";
  fontPackages = [ pkgs.dejavu_fonts pkgs.noto-fonts pkgs.noto-fonts-cjk-sans pkgs.noto-fonts-color-emoji ];
  fonts = pkgs.writeText "firefox-fonts.conf" ''
    <?xml version="1.0"?>
    <!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
    <fontconfig>
      <reset-dirs />
      ${lib.concatMapStringsSep "\n" (p: "<dir>${p}/share/fonts</dir>") fontPackages}
      <cachedir prefix="xdg">fontconfig</cachedir>
      <alias><family>sans-serif</family><prefer><family>DejaVu Sans</family><family>Noto Sans CJK SC</family></prefer></alias>
      <alias><family>serif</family><prefer><family>DejaVu Serif</family><family>Noto Serif</family></prefer></alias>
      <alias><family>monospace</family><prefer><family>DejaVu Sans Mono</family></prefer></alias>
    </fontconfig>
  '';

  prefs = {
    # DNS / ECH
    # Keep blocklists, split DNS, MagicDNS and portal recovery on the system path.
    "network.trr.mode" = 5;
    "network.trr.uri" = "";
    "doh-rollout.enabled" = false;
    "network.dns.echconfig.enabled" = true;
    "network.dns.http3_echconfig.enabled" = true;
    "network.dns.native_https_query" = true;
    "network.dns.use_https_rr_as_altsvc" = true;
    "network.dns.force_use_https_rr" = false;
    "network.dns.echconfig.fallback_to_origin_when_all_failed" = false;
    "security.tls.ech.grease_probability" = 100;
    "network.dns.disablePrefetch" = true;
    "network.prefetch-next" = false;
    "network.predictor.enabled" = false;
    "network.http.speculative-parallel-limit" = 0;
    "network.proxy.failover_direct" = false;
    "network.proxy.allow_hijacking_localhost" = true;
    "network.proxy.socks" = "";
    "network.proxy.socks_port" = 0;
    "network.proxy.socks_remote_dns" = false;
    "network.proxy.socks5_remote_dns" = false;
    "network.proxy.autoconfig_url" = "";
    "network.proxy.no_proxies_on" = "";
    "network.http.http3.enable" = false;
    "network.dns.disablePrefetchFromHTTPS" = true;

    # WebRTC
    "media.peerconnection.enabled" = false;
    "media.peerconnection.ice.default_address_only" = true;

    # Fingerprinting / tracking
    "privacy.fingerprintingProtection" = true;
    "privacy.resistFingerprinting" = strictPrivacy;
    "privacy.resistFingerprinting.letterboxing" = strictPrivacy;
    "privacy.spoof_english" = 2;
    "layout.css.font-visibility.standard" = if strictPrivacy then 1 else 3;
    "layout.css.font-visibility.trackingprotection" = if strictPrivacy then 1 else 3;
    "layout.css.font-visibility.private" = if strictPrivacy then 1 else 3;
    "browser.display.use_document_fonts" = 1;
    "browser.contentblocking.category" = "strict";
    "network.cookie.cookieBehavior" = 5;
    "dom.security.https_only_mode" = false;
    "dom.security.https_only_mode_pbm" = false;
    "dom.security.https_only_mode.upgrade_local" = false;
    "dom.security.https_first" = true;
    "dom.security.https_first_for_local_addresses" = false;
    "webgl.disabled" = strictPrivacy;
    "dom.webgpu.enabled" = false;
    "geo.enabled" = false;
    "privacy.trackingprotection.enabled" = true;
    "privacy.trackingprotection.socialtracking.enabled" = true;
    "privacy.trackingprotection.emailtracking.enabled" = true;
    "privacy.query_stripping.enabled" = true;
    "privacy.query_stripping.enabled.pbmode" = true;
    "privacy.query_stripping.strip_on_share.enabled" = true;
    "privacy.bounceTrackingProtection.mode" = 1;
    "privacy.annotate_channels.strict_list.enabled" = true;
    "privacy.globalprivacycontrol.enabled" = true;
    "privacy.clearOnShutdown_v2.formdata" = true;

    # Telemetry / studies / crash reporting
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

    # Referrer / preconnect
    # Cross-origin Referer stays suppressed unless the compatibility
    # switch is enabled for media CDNs that reject requests without it.
    "network.http.referer.XOriginPolicy" = if compat then 0 else 2;
    "network.http.referer.XOriginTrimmingPolicy" = 2;
    "browser.urlbar.speculativeConnect.enabled" = false;
    "browser.places.speculativeConnect.enabled" = false;

    # Local data
    "browser.formfill.enable" = false;
    "browser.sessionstore.privacy_level" = 2;

    # Suggestions / promotions
    "browser.search.suggest.enabled" = false;
    "browser.urlbar.suggest.searches" = false;
    "browser.urlbar.quicksuggest.enabled" = false;
    "browser.urlbar.suggest.quicksuggest.sponsored" = false;
    "browser.urlbar.suggest.quicksuggest.nonsponsored" = false;
    "browser.newtabpage.activity-stream.feeds.snippets" = false;
    "browser.newtabpage.activity-stream.feeds.section.topstories" = false;
    "browser.newtabpage.activity-stream.showSponsored" = false;
    "browser.newtabpage.activity-stream.showSponsoredTopSites" = false;
    "browser.topsites.contile.enabled" = false;
    "extensions.pocket.enabled" = false;

    # Remaining surfaces
    "browser.ml.chat.enabled" = false;
    "browser.ml.linkPreview.enabled" = false;
    "browser.ipProtection.enabled" = false;
    "browser.ipProtection.autoStartEnabled" = false;
    "network.connectivity-service.enabled" = false;
    "pdfjs.enableScripting" = false;
    "dom.battery.enabled" = false;
    "device.sensors.enabled" = false;
    "browser.region.update.enabled" = false;
    "browser.region.network.url" = "";
  };

  # Nixpkgs' native AutoConfig can lock RFP and other preferences outside the
  # enterprise Preferences allow-list. These apply to every profile of this package.
  lockedPrefs = lib.concatStringsSep "\n" (lib.mapAttrsToList
    (name: value: "lockPref(${builtins.toJSON name}, ${builtins.toJSON value});") prefs);

  encId = id: lib.replaceStrings [ "{" "}" ] [ "%7B" "%7D" ] id;
  extensionSettings = lib.mapAttrs
    (id: mode: {
      installation_mode = mode;
      install_url = "https://addons.mozilla.org/firefox/downloads/latest/${encId id}/latest.xpi";
    })
    {
      "uBlock0@raymondhill.net" = "force_installed";
      "{fb25c100-22ce-4d5a-be7e-75f3d6f0fc13}" = "normal_installed"; # KISS Translator
    };

  policies = {
    ExtensionSettings = lib.optionalAttrs strictPrivacy { "*".installation_mode = "blocked"; } // extensionSettings;
    DisableTelemetry = true;
    DisableFirefoxStudies = true;
    DisableEncryptedClientHello = false;
    DisableFirefoxAccounts = true;
    EnableTrackingProtection = {
      Value = true;
      Locked = true;
      Cryptomining = true;
      Fingerprinting = true;
    };
    Proxy = if transparent then { Mode = "none"; Locked = true; } else {
      Mode = "manual";
      Locked = true;
      HTTPProxy = relay;
      SSLProxy = relay;
      UseHTTPProxyForAllProtocols = true;
      Passthrough = "";
    };
    DNSOverHTTPS = {
      Enabled = false;
      Locked = true;
    };
    DisableAppUpdate = true;
    DisableProfileImport = true;
    DisableProfileRefresh = true;
    DisableFormHistory = true;
    SearchSuggestEnabled = false;
    SearchEngines.Default = "Google";
    CaptivePortal = true;
    NetworkPrediction = false;
    DontCheckDefaultBrowser = true;
    OverrideFirstRunPage = "";
    OverridePostUpdatePage = "";
    UserMessaging = {
      ExtensionRecommendations = false;
      FeatureRecommendations = false;
      UrlbarInterventions = false;
      MoreFromMozilla = false;
      SkipOnboarding = true;
    };
    FirefoxHome = {
      Locked = true;
      SponsoredTopSites = false;
      SponsoredPocket = false;
      Snippets = false;
    };
  };
  settings = pkgs.writeText "firefox-launch.json" (builtins.toJSON {
    inherit profile strictPrivacy fonts;
    networkPath = config.my.firefox.networkPath;
    fontDirectories = map (p: "${p}/share/fonts") fontPackages;
    fontList = "${pkgs.fontconfig}/bin/fc-list";
    stateModule = ../../../system/programs/systemd/netsec/state.py;
    healthModule = ../../../system/programs/systemd/netsec/health.py;
    healthSettings = osConfig.my.proxy.desktopHealthSettings;
    maxAge = osConfig.my.proxy.stateMaxAgeSeconds;
    verificationMaxAge = osConfig.my.proxy.verificationMaxAgeSeconds;
  });
  desktop = pkgs.makeDesktopItem {
    name = "firefox";
    desktopName = "Firefox";
    exec = "@firefox@ %U";
    icon = "firefox";
    categories = [ "Network" "WebBrowser" ];
    mimeTypes = [ "text/html" "x-scheme-handler/http" "x-scheme-handler/https" ];
    startupWMClass = "firefox";
    actions = {
      new-window = { name = "New Window"; exec = "@firefox@ --new-window %U"; };
      new-private-window = { name = "New Private Window"; exec = "@firefox@ --private-window %U"; };
    };
  };

in
{
  imports = [ ./options.nix ];
  assertions = [{
    assertion = !transparent || osConfig.my.proxy.tunMode;
    message = "Firefox transparent networking requires my.proxy.tunMode=true.";
  }];

  programs.firefox = {
    enable = true;
    package = (pkgs.firefox.override {
      extraPrefs = lockedPrefs;
    }).overrideAttrs (old: {
      # Home Manager's policy/appDataDir override preserves this final launcher.
      # The unchecked binary stays private to this package, outside PATH.
      buildCommand = old.buildCommand + ''
        mv "$out/bin/firefox" "$out/bin/.firefox-unchecked"
        cat > "$out/bin/firefox" <<EOF
        #!${pkgs.runtimeShell}
        exec ${pkgs.python3}/bin/python3 -I ${./guard.py} ${settings} "$out/bin/.firefox-unchecked" "\$@"
        EOF
        chmod +x "$out/bin/firefox"
        cp ${desktop}/share/applications/firefox.desktop "$out/share/applications/firefox.desktop"
        substituteInPlace "$out/share/applications/firefox.desktop" \
          --replace-fail '@firefox@' "$out/bin/firefox"
      '';
      passthru = (old.passthru or { }) // {
        netsec = { inherit settings fonts lockedPrefs prefs; };
      };
    });
    configPath = "${config.xdg.configHome}/mozilla/firefox";
    profiles.lfour = {
      id = 0;
      isDefault = true;
      name = "lfour";
      path = "lfour";
      settings = prefs;
    };
    inherit policies;
  };

  # XDG_DATA_HOME wins over system/Flatpak exports and existing pinned IDs.
  xdg.dataFile = {
    "applications/firefox.desktop".source =
      "${config.programs.firefox.finalPackage}/share/applications/firefox.desktop";
  } // lib.genAttrs
    [ "applications/app.zen_browser.zen.desktop" "applications/com.google.Chrome.desktop" ]
    (path: {
      source = "${pkgs.makeDesktopItem {
        name = lib.removeSuffix ".desktop" (baseNameOf path);
        desktopName = "Firefox";
        exec = "${config.programs.firefox.finalPackage}/bin/firefox %U";
        icon = "firefox";
        noDisplay = true;
      }}/share/${path}";
    });
  # Default application assignments are declared in the system layer instead:
  # system/config/mimeapps.nix. home-manager's xdg.mimeApps turns
  # ~/.config/mimeapps.list into a read-only /nix/store symlink, which makes
  # every "set as default" in the desktop fail with EROFS.
}
