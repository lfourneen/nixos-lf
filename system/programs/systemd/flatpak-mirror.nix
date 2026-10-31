{ pkgs, config, ... }:

{
  # Flatpak-mirror service
  systemd.services.flatpak-mirror = {
    description = "Configure Flathub USTC Mirror";
    wantedBy = [ "multi-user.target" ];
    before = [ "flatpak-managed-install.service" ]; 
    # This unit reaches the mirror through gost's proxy (see Environment below),
    # so order after gost-relay too; soft only, it must still run if it fails.
    after = [ "network-online.target" "dbus.service" "gost-relay.service" ]; 
    wants = [ "network-online.target" "gost-relay.service" ];
    
    path = [ pkgs.flatpak pkgs.coreutils pkgs.curl ];
    # gost-relay is started before it listens, and mihomo finishes its own
    # start-up behind it, so one attempt at boot loses the race. Wait for the
    # proxy port, then retry the download.
    script = ''
      for _ in $(seq 1 30); do
        curl -s -o /dev/null --max-time 2 http://127.0.0.1:${toString config.my.machine.ports.gostHttp}/ && break
        sleep 2
      done
      for attempt in $(seq 1 5); do
        flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo &&
          flatpak remote-modify flathub --url=https://mirrors.ustc.edu.cn/flathub && exit 0
        echo "flatpak remote setup failed (attempt $attempt)" >&2
        sleep 10
      done
      exit 1
    '';
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      # systemd service has no login-session env; without proxy, dl.flathub.org is
      # GFW-blocked (DNS poisoned) -> "Could not resolve hostname". Route via gost-relay.
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

