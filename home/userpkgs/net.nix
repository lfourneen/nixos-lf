{ pkgs, ... }:

{
  home.packages = with pkgs;[
    aria2
    clash-verge-rev
    media-downloader
    pkgs.unstable.motrix-next
    speedtest-go
    video-downloader
    yt-dlp
  ];
}

