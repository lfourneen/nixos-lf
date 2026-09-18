{ ... }:

{
  # GUI system proxy via gsettings ("Use system proxy settings" apps: Zen/Firefox, Electron, Qt)
  # Points at gost-pac 33332 (fail-open: Clash up -> 7897, down -> direct)
  # NOTE: keep Clash Verge "System Proxy" OFF or it overwrites these values with 7897
  dconf.settings = {
    # org.gnome.system.proxy schema path is /system/proxy/ (not /org/gnome/).
    "system/proxy" = {
      mode = "manual";
      "ignore-hosts" = [
        "localhost"
        "127.0.0.0/8"
        "::1"
        "10.0.0.0/8"
        "172.16.0.0/12"
        "192.168.0.0/16"
        "100.64.0.0/10"
        "*.local"
      ];
      # dconf is written once at activation, so runtime drift (Clash Verge,
      # noctalia, GUI) is not corrected here: check `dconf dump /system/proxy/`.
      # Keep the PAC pointer empty so apps cannot take a non-gost proxy path.
      "use-same-proxy" = true;
      "autoconfig-url" = "";
    };

    "system/proxy/http" = {
      host = "127.0.0.1";
      port = 33332;
    };
    
    "system/proxy/https" = {
      host = "127.0.0.1";
      port = 33332;
    };

    # SOCKS/FTP are separate child schemas; pinned empty because they are the
    # keys the drift above writes with 127.0.0.1:7897.
    "system/proxy/socks" = { host = ""; port = 0; };
    "system/proxy/ftp" = { host = ""; port = 0; };
  };
}

