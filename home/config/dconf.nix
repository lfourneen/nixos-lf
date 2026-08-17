{ ... }:
{
  # GUI system proxy via gsettings ("Use system proxy settings" apps: Zen/Firefox, Electron, Qt)
  # Points at gost-pac 33332 (fail-open: Clash up -> 7897, down -> direct)
  # NOTE: keep Clash Verge "System Proxy" OFF or it overwrites these values with 7897
  dconf.settings = {
    "org/gnome/system/proxy" = {
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
    };
    "org/gnome/system/proxy/http" = {
      host = "127.0.0.1";
      port = 33332;
    };
    "org/gnome/system/proxy/https" = {
      host = "127.0.0.1";
      port = 33332;
    };
  };
}

