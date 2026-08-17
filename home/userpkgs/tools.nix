{ inputs, lib, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
in
{
  home.packages = with pkgs;[
    ncdu
    unstable-pkgs.wineWow64Packages.waylandFull
    unstable-pkgs.winetricks
    yazi
  ];
}

