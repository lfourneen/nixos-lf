{ pkgs, config, ... }:

{
  # Flatpak-mirror service
  systemd.services.flatpak-mirror = {
    description = "Configure Flathub USTC Mirror";
    wantedBy = [ "multi-user.target" ];
    before = [ "flatpak-managed-install.service" ]; 
    # This unit reaches the mirror through gost's proxy (see Environment below),
    # so order after gost-pac too; soft only, it must still run if it fails.
    after = [ "network-online.target" "dbus.service" "gost-pac.service" ]; 
    wants = [ "network-online.target" "gost-pac.service" ];
    
    path = [ pkgs.flatpak ];
    script = ''
      flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
      flatpak remote-modify flathub --url=https://mirrors.ustc.edu.cn/flathub
    '';
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      # systemd service has no login-session env; without proxy, dl.flathub.org is
      # GFW-blocked (DNS poisoned) -> "Could not resolve hostname". Route via gost-pac.
      Environment = [
        "http_proxy=http://127.0.0.1:${toString config.my.machine.ports.gostHttp}"
        "https_proxy=http://127.0.0.1:${toString config.my.machine.ports.gostHttp}"
        "HTTP_PROXY=http://127.0.0.1:${toString config.my.machine.ports.gostHttp}"
        "HTTPS_PROXY=http://127.0.0.1:${toString config.my.machine.ports.gostHttp}"
        "ALL_PROXY=http://127.0.0.1:${toString config.my.machine.ports.gostHttp}"
        "all_proxy=http://127.0.0.1:${toString config.my.machine.ports.gostHttp}"
      ];
    };
  };
}

