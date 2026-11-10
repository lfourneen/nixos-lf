{ pkgs, config, compatConfig }:

let
  home = config.home-manager.users.${config.my.machine.desktopUser};
  compatHome = compatConfig.home-manager.users.${compatConfig.my.machine.desktopUser};
  firefox = home.programs.firefox.finalPackage;

  manifest = pkgs.writeText "firefox-test.json" (builtins.toJSON {
    package = firefox;
    driver = ./firefox_driver.py;
    inherit (firefox.netsec) settings prefs;
    rootHealthSettings = config.my.proxy.healthSettings;
    desktopHealthSettings = config.my.proxy.desktopHealthSettings;
    desktopUid = config.users.users.${config.my.machine.desktopUser}.uid;
    compatPrefs = compatHome.programs.firefox.finalPackage.netsec.prefs;
    policies = home.programs.firefox.policies;
    relayPort = config.my.machine.ports.gostHttp;
    desktop = home.xdg.dataFile."applications/firefox.desktop".source;
    aliases = map (id: home.xdg.dataFile."applications/${id}.desktop".source)
      [ "app.zen_browser.zen" "com.google.Chrome" ];
    # System-level defaults; values are single strings, the runtime test expects lists.
    mime = builtins.mapAttrs (_: app: [ app ]) (import ../../system/config/mimeapps.nix);
    flatpaks = config.services.flatpak.packages;
  });

in
pkgs.runCommand "firefox-privacy-checks" {
  nativeBuildInputs = [ pkgs.python3 ];
} ''
  export PYTHONDONTWRITEBYTECODE=1
  export FIREFOX_GUARD=${../../home/programs/firefox/guard.py}
  export FIREFOX_SANDBOX=${../../home/programs/firefox/sandbox.py}
  export FIREFOX_HEALTH=${../../system/programs/systemd/netsec/health.py}
  export XDG_CACHE_HOME="$TMPDIR/firefox-cache"
  mkdir -p "$XDG_CACHE_HOME"
  python3 ${./test_firefox.py}
  mkdir -p "$out"
  python3 ${./test_firefox_runtime.py} ${manifest} "$out"
''
