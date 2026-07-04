{ inputs, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
in
{
  environment.systemPackages = with pkgs; [
    # Base libs
    openssl
    unstable-pkgs.glibc

    # Base cli
    bat
    duf
    fd
    file
    iotop 
    lsd
    lshw
    lsof
    pciutils
    psutils
    ripgrep-all
    tree
    usbutils

    # Archives
    p7zip
    peazip
    unrar
    unzip
    zip

    # Download
    aria2
    wget

    # Network
    dhcpcd

    # Monitoring
    btop
    fastfetch
    lm_sensors

    # Hardware
    vdpauinfo

    # Editors
    neovim
    vim

    # Media
    ffmpeg-full
    
    # Rust
    dioxus-cli
    
    # Flatpak
    flatpak-builder
  ];
}

