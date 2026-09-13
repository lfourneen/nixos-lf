{ pkgs, ... }:

{
  home.packages = with pkgs;[
    ncdu
    pkgs.unstable.wineWow64Packages.waylandFull
    pkgs.unstable.winetricks
    yazi
  ];
}

