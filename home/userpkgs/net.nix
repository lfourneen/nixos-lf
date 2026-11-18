{ pkgs, ... }:

{
  home.packages = with pkgs;[
    aria2
    pkgs.unstable.rayburst
    speedtest-go
    yt-dlp
  ];
}

