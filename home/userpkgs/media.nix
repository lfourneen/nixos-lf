{ pkgs, ... }:

{
  home.packages = with pkgs; [
    imv
    mangohud
    mpv
    scrcpy
    sillytavern
    waywallen-layer-shell
  ] ++ pkgs.lib.optional (pkgs ? bilibili) pkgs.bilibili;
}

