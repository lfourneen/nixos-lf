{ inputs, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };

in
{
  home.packages = with pkgs;[
    aria2
    clash-verge-rev
    media-downloader
    unstable-pkgs.motrix-next
    speedtest-go
    video-downloader
    yt-dlp
  ];
}

