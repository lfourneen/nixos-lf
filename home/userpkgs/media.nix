{ inputs, lib, pkgs, ... }:

{
  home.packages = with pkgs;[
    bilibili
    imv
    mangohud
    mpv
    scrcpy
    sillytavern
  ];
}

