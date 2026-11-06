{ pkgs, config }:

let
  home = config.home-manager.users.${config.my.machine.desktopUser};
  firefox = home.programs.firefox.finalPackage;
  manifest = pkgs.writeText "firefox-test.json" (builtins.toJSON {
    package = firefox;
    inherit (firefox.netsec) settings prefs;
    policies = home.programs.firefox.policies;
    relayPort = config.my.machine.ports.gostHttp;
    desktop = home.xdg.dataFile."applications/firefox.desktop".source;
    aliases = map (id: home.xdg.dataFile."applications/${id}.desktop".source)
      [ "app.zen_browser.zen" "com.google.Chrome" ];
    mime = home.xdg.mimeApps.defaultApplications;
    flatpaks = config.services.flatpak.packages;
  });

in
pkgs.runCommand "firefox-privacy-checks" {
  nativeBuildInputs = [ pkgs.python3 ];
} ''
  export PYTHONDONTWRITEBYTECODE=1
  export FIREFOX_GUARD=${../../home/programs/firefox/guard.py}
  export XDG_CACHE_HOME="$TMPDIR/firefox-cache"
  mkdir -p "$XDG_CACHE_HOME"
  python3 ${./test_firefox.py}
  mkdir -p "$out"
  python3 ${./test_firefox_runtime.py} ${manifest} "$out"
''
