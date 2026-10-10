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
    procps
    psutils
    sops
    tree
    usbutils

    # Search & files
    ripgrep-all

    # Git & GitHub
    gh

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

    # Test
    jmeter

    # Monitoring
    btop
    fastfetch
    lm_sensors

    # Hardware
    vdpauinfo

    # Editors
    neovim
    vim

    # MCP
    context7-mcp
    mcp-nixos
    mcp-server-fetch
    pkgs.unstable.pdf-mcp
    pkgs.unstable.open-websearch
    pybibget
    python3Packages.arxiv2bib

    # Document
    texliveFull
    poppler-utils
    pandoc
    tesseract
    ocrmypdf
    qpdf
    mupdf

    # AI agents
    dsh
    dsh-desktop
    oh-my-codex
    oh-my-opencode

    # Media
    ffmpeg-full
    
    # Rust
    dioxus-cli
    
    # Flatpak
    flatpak-builder
  ];
}

