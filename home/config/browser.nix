{ lib, osConfig, pkgs, ... }:

let
  # Shadow Flatpak's desktop entries so normal menu/MIME launches use the same
  # checked, separate profiles. Direct flatpak invocation is outside this
  # launcher precondition; the system enterprise policies still apply there.
  entries = (
    lib.optionalAttrs osConfig.my.browser.zen.enable {
      "app.zen_browser.zen" = {
        name = if osConfig.my.browser.strictPrivacy then "Zen (Private)" else "Zen";
        exec = "/run/current-system/sw/bin/browser-private zen %U";
        icon = "app.zen_browser.zen";
        categories = [ "Network" "WebBrowser" ];
        mimeType = [ "text/html" "x-scheme-handler/http" "x-scheme-handler/https" ];
        terminal = false;
      };
    } // lib.optionalAttrs osConfig.my.browser.chrome.enable {
      "com.google.Chrome" = {
        name = if osConfig.my.browser.strictPrivacy then "Chrome (Private)" else "Chrome";
        exec = "/run/current-system/sw/bin/browser-private chrome %U";
        icon = "com.google.Chrome";
        categories = [ "Network" "WebBrowser" ];
        mimeType = [ "text/html" "x-scheme-handler/http" "x-scheme-handler/https" ];
        terminal = false;
      };
    }
  );

in
{
  xdg.desktopEntries = entries;
  # Flatpak exports can precede the Home Manager profile in XDG_DATA_DIRS.
  # XDG_DATA_HOME has priority over both, including for existing dock IDs.
  xdg.dataFile = lib.mapAttrs' (id: entry: lib.nameValuePair "applications/${id}.desktop" {
    source = "${pkgs.makeDesktopItem {
      name = id;
      desktopName = entry.name;
      inherit (entry) exec icon categories terminal;
      mimeTypes = entry.mimeType;
    }}/share/applications/${id}.desktop";
  }) entries;
}
