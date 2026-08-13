{ inputs, lib, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
in
{
  home.packages = with pkgs;[
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

