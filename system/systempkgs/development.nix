{ inputs, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
in
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
    meson
    ninja

    # Mixed
    gcc_multi
    gdb
    
    # C / C++
    llvmPackages_latest.clang
    llvmPackages_latest.clang-tools
    llvmPackages_latest.lldb
    llvmPackages_latest.libcxx
    llvmPackages_latest.libllvm

    # Android
    #unstable-pkgs.androidsdk
    unstable-pkgs.android-tools

    # GTK3 && GTK4
    gtk3
    gtk4

    # Electron
    electron

    # Java
    jdk

    # .NET
    dotnet-sdk
    dotnet-runtime

    # Python
    python313
    uv
    python313Packages.pip

    # Node.js
    bun
    just
    nodejs_24
    pnpm

    # Rust
    cargo
    cargo-tauri
    rustc
    rustfmt

    # Go
    go
  ];
}

