{ pkgs, ... }:

{
  home.packages = with pkgs;[
    pkgs.unstable.wineWow64Packages.waylandFull
    pkgs.unstable.winetricks
    yazi
  ];
}

