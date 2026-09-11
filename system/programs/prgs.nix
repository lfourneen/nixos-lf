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

  # NH
  programs.nh = {
    package = pkgs.nh;
    enable = true;
  };

  # FZF
  programs.fzf.fuzzyCompletion = true;

  # Appimage
  programs.appimage = {
    enable = true;
    binfmt = true;
  };
}

