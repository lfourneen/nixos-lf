{ pkgs, ... }:

{
  home.packages = with pkgs;[
    gh
    ncdu
    pkgs.unstable.wineWow64Packages.waylandFull
    pkgs.unstable.winetricks
    yazi
  ];
}

