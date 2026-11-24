{ pkgs, config, lib, ... }:

let
  proxy = import ../../config/network/proxy/env.nix { inherit lib; };
  socketGuests = import ../../config/network/guests/policy.nix { inherit config lib; };
  rootless = config.virtualisation.docker.rootless;
  daemonConfig = (pkgs.formats.json { }).generate "rootless-docker-daemon.json" rootless.daemon.settings;

  rootlessLauncher = pkgs.writeShellScript "rootless-docker-launcher" ''
    ${lib.optionalString (socketGuests.socketUids != [ ]) ''
    case "$(${pkgs.coreutils}/bin/id -u)" in
      ${lib.concatStringsSep "|" (map toString socketGuests.socketUids)})

        # This identity uses native sockets plus forced real-address DNS. An
        # inherited explicit host proxy would lose the backend's UID identity.
        unset ${lib.concatStringsSep " " proxy.unsetNames}
        ;;
    esac
    ''}
    exec ${rootless.package}/bin/dockerd-rootless --config-file=${daemonConfig}
  '';

in
{
  virtualisation = {
    # Keep the existing runtime available until its workloads are retired.
    containerd.enable = lib.mkDefault true;

    # Enable Docker
    docker = {
      package = pkgs.unstable.docker;

      # Disable the system ROOT dockerd - rootless docker is the ONLY daemon now
      enable = false;
      storageDriver = "btrfs";
    };

    docker.rootless = {
      enable = true;
      setSocketVariable = true;
      daemon.settings = {
        registry-mirrors = [
          "https://docker.m.daocloud.io"
          "https://hub-mirror.c.163.com"
        ];
      };
    };
  };

  systemd.user.services.docker.serviceConfig.TimeoutStopSec = "15s";
  systemd.user.services.docker.serviceConfig.ExecStart = lib.mkForce (toString rootlessLauncher);
}
