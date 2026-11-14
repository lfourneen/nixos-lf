{ config, lib, pkgs, ... }:

let
  account = "hermes-containers";
  clients = "hermes-container-clients";
  uid = toString config.my.machine.uids.hermesContainers;
  endpoint = "/run/hermes-containers/docker.sock";
  backend = "/run/user/${uid}/hermes-docker.sock";
  docker = config.virtualisation.docker.rootless.package;
  unsetProxy = [ "HTTP_PROXY" "HTTPS_PROXY" "ALL_PROXY" "FTP_PROXY" "NO_PROXY" "http_proxy" "https_proxy" "all_proxy" "ftp_proxy" "no_proxy" ];
  prerequisites = [ "hermes-container-retire.service" "nftables.service" "socket-guest-dns.service" ];

  stopLegacy = pkgs.writeShellScript "stop-hermes-user-docker" ''
    set -eu
    old_uid=$(${pkgs.coreutils}/bin/id -u hermes)
    if ${config.systemd.package}/bin/systemctl is-active --quiet "user@$old_uid.service"; then
      ${config.systemd.package}/bin/systemctl --user --machine=hermes@.host stop docker.service
      if [ "$(${config.systemd.package}/bin/systemctl --user --machine=hermes@.host show -p LoadState --value docker.socket)" = loaded ]; then
        ${config.systemd.package}/bin/systemctl --user --machine=hermes@.host stop docker.socket
      fi
    fi
    if ${pkgs.procps}/bin/pgrep -u "$old_uid" -x dockerd >/dev/null; then
      echo "Hermes still has an old Docker daemon; refusing the new backend" >&2
      exit 1
    else
      status=$?
      [ "$status" -eq 1 ] || exit "$status"
    fi
    # Old images and volumes remain untouched for an explicit export/import.
  '';

in
{
  users.groups.${account} = { };
  users.groups.${clients}.members = [ "hermes" ];
  users.users.${account} = {
    isNormalUser = true;
    uid = config.my.machine.uids.hermesContainers;
    group = account;
    extraGroups = [ clients ];
    home = "/var/lib/hermes-containers";
    createHome = true;
    homeMode = "0700";
    linger = true;
    autoSubUidGidRange = false;
    subUidRanges = [{ startUid = 500000; count = 65536; }];
    subGidRanges = [{ startGid = 500000; count = 65536; }];
  };

  my.hardening.socketGuestUsers = [ account ];

  systemd.user.services.docker.unitConfig.ConditionUser = lib.mkForce [ "!root" "!hermes" "!hermes-containers" ];
  systemd.services.hermes-container-retire = {
    description = "Stop the previous Hermes user Docker before changing backends";
    serviceConfig = { Type = "oneshot"; ExecStart = stopLegacy; };
  };

  # A real user manager supplies Docker's systemd cgroup driver and bus. Start
  # it only after old backends have stopped and the account policy is installed.
  systemd.services."user@${uid}" = {
    overrideStrategy = "asDropin";
    requires = prerequisites;
    after = prerequisites;
  };

  systemd.user.services.hermes-docker = {
    description = "Restricted rootless Docker for Hermes workloads";
    wantedBy = [ "default.target" ];
    unitConfig.ConditionUser = account;
    path = [ "/run/wrappers" docker pkgs.iproute2 pkgs.util-linux pkgs.coreutils pkgs.slirp4netns pkgs.fuse-overlayfs ];

    environment = {
      HOME = "/var/lib/hermes-containers";
      DOCKERD_ROOTLESS_ROOTLESSKIT_NET = "slirp4netns";
    };

    serviceConfig = {
      Type = "notify";
      NotifyAccess = "all";
      ExecStart = "${docker}/bin/dockerd-rootless --host=unix://${backend} --group=0 --data-root=/var/lib/hermes-containers/docker --storage-driver=fuse-overlayfs";
      UnsetEnvironment = unsetProxy;
      UMask = "0077";
      Delegate = true;
      KillMode = "control-group";
      Restart = "on-failure";
      RestartSec = "3s";
      TimeoutStartSec = "90s";
      TimeoutStopSec = "30s";
      LimitNOFILE = 1048576;
      LimitNPROC = "infinity";
      TasksMax = "infinity";
      SocketBindDeny = config.systemd.slices.user.sliceConfig.SocketBindDeny;
    };
  };

  # Directly expose only a fixed Docker byte stream; the runtime stays 0700.
  # Docker operators still control workloads with this backend account's rights.
  systemd.sockets.hermes-containers = {
    wantedBy = [ "sockets.target" ];
    socketConfig = {
      ListenStream = endpoint;
      SocketUser = account;
      SocketGroup = clients;
      SocketMode = "0660";
      DirectoryMode = "0755";
      RemoveOnStop = true;
    };
  };

  systemd.services.hermes-containers = {
    description = "Hermes access to the restricted container backend";
    wantedBy = [ "multi-user.target" ];
    requires = prerequisites ++ [ "hermes-containers.socket" "user@${uid}.service" ];
    after = prerequisites ++ [ "hermes-containers.socket" "user@${uid}.service" ];

    serviceConfig = {
      User = account;
      Group = account;
      ExecStartPre = pkgs.writeShellScript "wait-hermes-container-socket" ''
        for attempt in $(${pkgs.coreutils}/bin/seq 1 300); do
          [ -S ${backend} ] && exit 0
          ${pkgs.coreutils}/bin/sleep 0.1
        done
        exit 1
      '';

      ExecStart = "${config.systemd.package}/lib/systemd/systemd-socket-proxyd ${backend}";
      UnsetEnvironment = unsetProxy;
      NoNewPrivileges = true;
      CapabilityBoundingSet = "";
      RestrictAddressFamilies = [ "AF_UNIX" ];
      ProtectSystem = "strict";
      ProtectHome = "read-only";
      PrivateTmp = true;
    };
  };

  systemd.services.hermes-agent.environment.DOCKER_HOST = "unix://${endpoint}";
  systemd.services.hermes-backend.environment.DOCKER_HOST = "unix://${endpoint}";
  systemd.services.hermes-agent.after = [ "hermes-containers.service" ];
  systemd.services.hermes-agent.wants = [ "hermes-containers.service" ];
  systemd.services.hermes-backend.after = [ "hermes-containers.service" ];
  systemd.services.hermes-backend.wants = [ "hermes-containers.service" ];
  systemd.services.hermes-agent.serviceConfig.SupplementaryGroups = [ clients ];
  systemd.services.hermes-backend.serviceConfig.SupplementaryGroups = [ clients ];
  environment.extraInit = ''
    if [ "$(id -un)" = hermes ]; then
      export DOCKER_HOST=unix://${endpoint}
    fi
  '';
}
