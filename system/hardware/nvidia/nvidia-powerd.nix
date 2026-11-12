{ lib, pkgs, ... }:

{
  # NVIDIA powerd service
  systemd.services.nvidia-powerd = {
    after = [ "nvidia-persistenced.service" "multi-user.target" ];
    wantedBy = lib.mkForce [ "multi-user.target" ];
    serviceConfig = {
      ExecStartPre = "${pkgs.bash}/bin/bash -c 'while [ ! -e /dev/nvidiactl ]; do sleep 0.5; done'";
    };
  };
}

