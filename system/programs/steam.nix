{ pkgs, ... }:

{
  # Gamemode
  programs.gamemode.enable = true;

  # Gamescope
  programs.gamescope = {
    enable = true;
    capSysNice = true;
  };

  # Steam
  programs.steam = {
    enable = true;
    extest.enable = true;
    protontricks.enable = true;

    extraPackages = with pkgs; [
      cef-binary
      fontconfig
    ];

    extraCompatPackages = with pkgs; [
      proton-ge-bin
      dwproton-bin
    ];

    fontPackages = with pkgs; [
      maple-mono.NF-CN
    ];
  };
}

