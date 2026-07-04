{ inputs, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
in
{
  # KVM
  programs.virt-manager.enable = true;
  virtualisation = {
    waydroid.enable = true;
    spiceUSBRedirection.enable = true;
    libvirtd = {
      enable = true;
      qemu = {
        package = pkgs.qemu_kvm;
        runAsRoot = true;
        swtpm.enable = true;
        vhostUserPackages = [ pkgs.virtiofsd ];
      };
    };
   
    # Enable Containerd
    containerd.enable = true;

    # Enable Docker
    docker = {
      package = unstable-pkgs.docker;
      enable = true;
      storageDriver = "btrfs";
      rootless = {
        enable = true;
        setSocketVariable = true;
      };
      daemon.settings = {
        registry-mirrors = [
          "https://docker.m.daocloud.io"
          "https://hub-mirror.c.163.com"
        ];
      };
    };
 
    # Enable Podman
    #podman = {
      #enable = true;
      #dockerCompat = true;
      #defaultNetwork.settings.dns_enabled = true;
    #};
  };

  environment.systemPackages = with pkgs; [
    # Docker container
    unstable-pkgs.docker-client unstable-pkgs.docker-compose
    
    # Podman Container
    unstable-pkgs.dive #podman podman-tui 
    #podman-desktop podman-compose pods

    # Kubernetes
    kubernetes kubectl kubernetes-helm-wrapped
    kubernetes-validate

    # Linux to Android
    unstable-pkgs.waydroid unstable-pkgs.waydroid-helper unstable-pkgs.nftables

    # Linux to Linux
    unstable-pkgs.distrobox 
  ];
}

