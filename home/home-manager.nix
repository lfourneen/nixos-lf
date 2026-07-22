{ ... }:

{
  # Let Home-Manager install and manage itself.
  programs.home-manager = {
    enable = true;
  };
  
  # Users settings
  home = {
    username = "lfour";
    homeDirectory = "/home/lfour";
    sessionPath = [ "$HOME/.local/bin" ];
  };

  home.sessionVariables = {
    NIXOS_OZONE_WL = "1";
    QT_QPA_PLATFORM = "wayland";
    ELECTRON_OZONE_PLATFORM_HINT = "wayland";
    GDK_BACKEND = "wayland";
    _JAVA_AWT_WM_NONREPARENTING = "1";
  };

  # Font
  fonts.fontconfig.enable = true;

  # Home-Manager version
  home.stateVersion = "26.05";
}

