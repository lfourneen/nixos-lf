{ pkgs, ... }:

{
  environment.systemPackages = with pkgs; [
    # Base libs
    openssl

    # Base cli
    age
    bat
    dig
    duf
    fd
    file
    iotop 
    lsd
    lshw
    lsof
    pciutils
    psutils
    sops
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
    networkmanagerapplet

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

