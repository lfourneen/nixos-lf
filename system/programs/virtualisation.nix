{ pkgs, ... }:

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
 
    # Enable Podman
    #podman = {
      #enable = true;
      #dockerCompat = true;
      #defaultNetwork.settings.dns_enabled = true;
    #};
  };

  systemd.user.services.docker.serviceConfig.TimeoutStopSec = "15s";

  environment.systemPackages = with pkgs; [
    # Docker container
    pkgs.unstable.docker-client 
    pkgs.unstable.docker-compose
    
    # Podman Container
    pkgs.unstable.dive 
    #pkgs.unstable.podman 
    #pkgs.unstable.podman-tui 
    #pkgs.unstable.podman-desktop 
    #pkgs.unstable.podman-compose 
    #pkgs.unstable.pods

    # Kubernetes
    pkgs.unstable.kubernetes 
    pkgs.unstable.kubectl 
    pkgs.unstable.kubernetes-helm-wrapped
    pkgs.unstable.kubernetes-validate

    # Linux to Android
    pkgs.unstable.waydroid 
    pkgs.unstable.waydroid-helper 
    pkgs.unstable.nftables

    # Linux to Linux
    pkgs.unstable.distrobox 
  ];
}

