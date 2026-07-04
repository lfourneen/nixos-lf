{ pkgs, ... }: 

{
  # Enable Java
  programs.java.enable = true;
  
  # Enable Direnv
  programs.direnv.enable = true;

  # Nix Index
  programs.nix-index = {
    enable = true;
    enableFishIntegration = true;
    enableBashIntegration = true;
  };
  programs.command-not-found.enable = false;

  # FZF
  programs.fzf.fuzzyCompletion = true;

  # Gamemode
  programs.gamemode.enable = true;

  # Gamescope
  programs.gamescope = {
    enable = true;
    capSysNice = true;
  };

  # Appimage
  programs.appimage = {
    enable = true;
    binfmt = true;
  };

  # Steam
  programs.steam = {
    enable = true;
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
    extest.enable = true;
    protontricks.enable = true;
  };
  hardware.steam-hardware.enable = true;
}

