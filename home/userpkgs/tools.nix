{ inputs, lib, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
in
{
  home.packages = with pkgs;[
    aria2
    imv
    media-downloader
    mpv
    unstable-pkgs.motrix-next
    unstable-pkgs.pince
    video-downloader
    scrcpy
    speedtest-go
    yazi
    yt-dlp
  ];
}

