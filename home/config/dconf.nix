{ osConfig, ... }:

let
  transparent = osConfig.my.proxy.transparentApplications;
  
in
{
  dconf.settings = {
    # GTK/client-side cursor theme (resize, text, not-allowed, ...). The
    # compositor (niri) uses its own `cursor { xcursor-theme }`; without this,
    # GTK apps fall back to whatever gsettings holds and the two look different.
    # Theme lives in ~/.local/share/icons, so no nix package is needed.
    "org/gnome/desktop/interface" = {
      cursor-theme = "Iochi Mari (Gym ver.)";
      cursor-size = 48;
      document-font-name = "Maple Mono NF CN Medium Italic 10";
      monospace-font-name = "Maple Mono NF CN Medium Italic 10";
    };

    # org.gnome.system.proxy schema path is /system/proxy/ (not /org/gnome/).
    "system/proxy" = {
      mode = if transparent then "none" else "manual";
      "ignore-hosts" = [
        "localhost"
        "127.0.0.0/8"
        "::1"
      ] ++ osConfig.my.machine.privateV4 ++ [
        "*.local"
      ];
      "use-same-proxy" = !transparent;
      "autoconfig-url" = "";
    };

    "system/proxy/http" = {
      host = if transparent then "" else "127.0.0.1";
      port = if transparent then 0 else osConfig.my.machine.ports.gostHttp;
    };
    
    "system/proxy/https" = {
      host = if transparent then "" else "127.0.0.1";
      port = if transparent then 0 else osConfig.my.machine.ports.gostHttp;
    };

    # SOCKS/FTP are separate child schemas; pinned empty because they are the
    # keys the drift above writes with mihomo's mixed port.
    "system/proxy/socks" = { host = ""; port = 0; };
    "system/proxy/ftp" = { host = ""; port = 0; };
  };
}

