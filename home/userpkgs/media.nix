{ pkgs, ... }:

{
  home.packages = with pkgs; [
    imv
    mangohud
    mpv
    scrcpy
    sillytavern
    waywallen
  ] ++ pkgs.lib.optional (pkgs ? bilibili) pkgs.bilibili;
}

