{ pkgs, ... }:

{
  home.packages = with pkgs; [
    gjs
    imv
    mangohud
    mpv
    scrcpy
    sillytavern
    waywallen
  ] ++ pkgs.lib.optional (pkgs ? bilibili) pkgs.bilibili;
}

