{ ... }:

{
  # Let Home-Manager install and manage itself.
  programs.home-manager.enable = true;
  
  # Users settings
  home = {
    username = "lfour";
    homeDirectory = "/home/lfour";
    sessionPath = [ "$HOME/.local/bin" ];
  };

  # Home-Manager version
  home.stateVersion = "26.05";
}

