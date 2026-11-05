{ pkgs, config, options }:

pkgs.runCommand "browser-privacy-checks" {
  nativeBuildInputs = [ pkgs.python3 pkgs.nodejs ];
} ''
  export BROWSER_GUARD=${../../system/programs/browser-guard.py}
  export PYTHONDONTWRITEBYTECODE=1
  python3 ${./test_browser.py}
  node --check ${config.my.browser.generated.preferences}
  python3 ${./test_browser_policy.py} ${pkgs.writeText "browser-artifacts.json" (builtins.toJSON {
    inherit (config.my.browser) generated strictPrivacy;
    relayPort = config.my.machine.ports.gostHttp;
    dohOnly = config.my.browser.zen.dohOnly;
    bootMode = config.my.proxy.bootMode;
    proxyOnly = config.my.proxy.proxyOnly;
    connectivityEnabled = config.networking.networkmanager.settings.connectivity.enabled or true;
    cjkFonts = "${pkgs.noto-fonts-cjk-sans}/share/fonts";
    fontEtcMode = config.environment.etc."netsec-browser/fonts.conf".mode or null;
    zenEnabled = config.my.browser.zen.enable;
    policyRepairTimer = config.systemd.timers.browser-policy-install.timerConfig.OnUnitInactiveSec or null;
    defaults = { bootMode = options.my.proxy.bootMode.default; proxyOnly = options.my.proxy.proxyOnly.default; };
  })}
  touch "$out"
''
