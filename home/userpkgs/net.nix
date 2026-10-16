{ pkgs, ... }:

{
  home.packages = with pkgs;[
    aria2
    media-downloader
    pkgs.unstable.rayburst
    speedtest-go
    video-downloader
    yt-dlp
  ];
}

