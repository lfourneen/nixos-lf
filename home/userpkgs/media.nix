{ pkgs, ... }:

{
  home.packages = with pkgs; [
    imv
    mangohud
    mpv
    scrcpy
    sillytavern
  ] ++ pkgs.lib.optional (pkgs ? bilibili) pkgs.bilibili;
}

