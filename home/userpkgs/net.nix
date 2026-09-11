{ pkgs, ... }:

{
  home.packages = with pkgs;[
    aria2
    media-downloader
    pkgs.unstable.motrix-next
    speedtest-go
    video-downloader
    yt-dlp
  ];
}

