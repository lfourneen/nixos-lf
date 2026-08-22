{ pkgs, ... }:

{
  # Flatpak-mirror service
  systemd.services.flatpak-mirror = {
    description = "Configure Flathub USTC Mirror";
    wantedBy = [ "multi-user.target" ];
    before = [ "flatpak-managed-install.service" ]; 
    after = [ "network-online.target" "dbus.service" ]; 
    wants = [ "network-online.target" ];
    
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
        "http_proxy=http://127.0.0.1:33332"
        "https_proxy=http://127.0.0.1:33332"
        "HTTP_PROXY=http://127.0.0.1:33332"
        "HTTPS_PROXY=http://127.0.0.1:33332"
        "ALL_PROXY=http://127.0.0.1:33332"
        "all_proxy=http://127.0.0.1:33332"
      ];
    };
  };
}

