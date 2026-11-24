{ pkgs, ... }:

{
  virtualisation.waydroid.enable = true;

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

    # Kubernetes client tools
    pkgs.unstable.kubectl
    pkgs.unstable.kubernetes-helm-wrapped
    pkgs.unstable.kubernetes-validate

    # Linux to Android
    pkgs.unstable.waydroid
    pkgs.unstable.waydroid-helper
    pkgs.unstable.nftables

    # Linux to Linux
    pkgs.unstable.distrobox

    # user-mode networking backend for libvirt VMs (egress becomes host
    # sockets, so it is captured by TUN)
    passt
  ];
}
