{ inputs, lib, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
in
{
  home.packages = with pkgs;[
    unstable-pkgs.google-chrome
    #unstable-pkgs.firefox
    clash-verge-rev
    #v2rayn
  ];
}

