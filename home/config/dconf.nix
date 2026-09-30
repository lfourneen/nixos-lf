{ osConfig, ... }:

{
  # GUI system proxy via gsettings ("Use system proxy settings" apps: Zen/Firefox, Electron, Qt)
  # Points at gost-pac's HTTP listener (fail-open: Clash up -> mihomo, down -> direct)
  # NOTE: keep Clash Verge "System Proxy" OFF or it overwrites these values with
  # my.machine.ports.mihomoMixed.
  dconf.settings = {
    # org.gnome.system.proxy schema path is /system/proxy/ (not /org/gnome/).
    "system/proxy" = {
      mode = "manual";
      "ignore-hosts" = [
        "localhost"
        "127.0.0.0/8"
        "::1"
      ] ++ osConfig.my.machine.privateV4 ++ [
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
      port = osConfig.my.machine.ports.gostHttp;
    };
    
    "system/proxy/https" = {
      host = "127.0.0.1";
      port = osConfig.my.machine.ports.gostHttp;
    };

    # SOCKS/FTP are separate child schemas; pinned empty because they are the
    # keys the drift above writes with mihomo's mixed port.
    "system/proxy/socks" = { host = ""; port = 0; };
    "system/proxy/ftp" = { host = ""; port = 0; };
  };
}

