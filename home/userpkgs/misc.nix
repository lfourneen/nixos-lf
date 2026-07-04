{ inputs, lib, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
in
{
  home.packages = with pkgs;[
    # Office
    unstable-pkgs.wpsoffice-cn
    (lib.hiPrio (writeShellScriptBin "wps" ''
      exec ${unstable-pkgs.wpsoffice-cn}/bin/wps \
        --force-device-scale-factor=1 \
        "$@"
    ''))
    (lib.hiPrio (writeShellScriptBin "wpp" ''
      exec ${unstable-pkgs.wpsoffice-cn}/bin/wpp \
        --force-device-scale-factor=1 \
        "$@"
    ''))
    (lib.hiPrio (writeShellScriptBin "et" ''
      exec ${unstable-pkgs.wpsoffice-cn}/bin/et \
        --force-device-scale-factor=1 \
        "$@"
    ''))

    # Wine
    unstable-pkgs.wineWow64Packages.waylandFull
    unstable-pkgs.winetricks

    # Image
    gimp
    krita
    inkscape

    # Video
    kdePackages.kdenlive
    shotcut
    obs-studio
  ];
}

