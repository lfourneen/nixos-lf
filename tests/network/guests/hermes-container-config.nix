{ lib, pkgs, ... }:

{
  imports = [ ../../../system/programs/hermes/containers.nix ];

  virtualisation.docker.rootless.enable = true;
  users.groups.hermes = { };
  users.users.hermes = {
    isSystemUser = true;
    uid = 988;
    group = "hermes";
    home = "/var/lib/hermes";
    createHome = true;
    linger = true;
    subUidRanges = [{ startUid = 165536; count = 65536; }];
    subGidRanges = [{ startGid = 165536; count = 65536; }];
  };

  systemd.services.hermes-agent.enable = false;
  systemd.services.hermes-backend.enable = false;
  systemd.services.hermes-containers.wantedBy = lib.mkForce [ ];

  # Exercise legacy retirement explicitly before starting this manager.
  users.users.hermes-containers.linger = lib.mkForce false;
  environment.etc."hermes-container.py".source = ./hermes_container_fixture.py;
  environment.etc."hermes-network-image.tar".source = pkgs.dockerTools.buildImage {
    name = "network-fixture";
    tag = "local";
    copyToRoot = pkgs.buildEnv {
      name = "network-fixture-root";
      paths = [ pkgs.bash pkgs.curl pkgs.python3 ];
      pathsToLink = [ "/bin" ];
    };
  };
}
