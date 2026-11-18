{ pkgs, config, lib, ... }:

let
  proxy = import ../../config/network/proxy/env.nix { inherit lib; };
  gostHttp = proxy.gostHttp config.my.machine.ports.gostHttp;
  relay = lib.optional (!config.my.proxy.transparentApplications) "gost-relay.service";

in
{
  # Flatpak-mirror service
  systemd.services.flatpak-mirror = {
    description = "Configure Flathub USTC Mirror";
    wantedBy = [ "multi-user.target" ];
    before = [ "flatpak-managed-install.service" ];
    after = [ "network-online.target" "dbus.service" ] ++ relay;
    wants = [ "network-online.target" ] ++ relay;

    path = [ pkgs.flatpak pkgs.coreutils pkgs.curl ];
    script = lib.optionalString (relay != [ ]) ''
      for _ in $(seq 1 30); do
        curl -s -o /dev/null --max-time 2 ${gostHttp}/ && break
        sleep 2
      done
    '' + ''
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

      Environment = lib.optionals (relay != [ ]) [
        "http_proxy=${gostHttp}"
        "https_proxy=${gostHttp}"
        "HTTP_PROXY=${gostHttp}"
        "HTTPS_PROXY=${gostHttp}"
        "ALL_PROXY=${gostHttp}"
        "all_proxy=${gostHttp}"
      ];
    };
  };
}

