{ pkgs, ... }:

{
  home.packages = with pkgs; [
    gjs
    imv
    mangohud
    mpv
    scrcpy
    waywallen
  ] ++ pkgs.lib.optional (pkgs ? bilibili) pkgs.bilibili;
}

