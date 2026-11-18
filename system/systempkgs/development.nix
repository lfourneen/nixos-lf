{ pkgs, ... }:

{
  environment.systemPackages = with pkgs; [
    # Build tools
    bear
    cmake
    coreutils-full
    devenv
    gnumake
    pkg-config
    pkgconf
    ninja

    # Mixed
    gcc_multi
    gdb
    
    # Android
    android-tools

    # Java
    jdk

    # Python
    python313
    uv
    python313Packages.pip

    # Node.js
    corepack
    bun
    just
    nodejs_24
    pnpm
    yarn

    # Rust
    cargo
    rustc
    rustfmt

    # Go
    go

    # FPGA / HDL
    verible
    svls
    verilator
    yosys
    python313Packages.cocotb

    # waveform conversion / runtime for wave-mcp
    zlib
    gtkwave
  ];
}

