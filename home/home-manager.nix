{ osConfig, ... }:

let
  user = osConfig.my.machine.desktopUser;
  account = osConfig.users.users.${user};
in

{
  # Let Home-Manager install and manage itself.
  programs.home-manager = {
    enable = true;
  };

  # Users settings
  home = {
    username = user;
    homeDirectory = account.home;

    sessionPath = [
      "$HOME/.local/bin"
    ];
  };

  home.sessionVariables = {
    NIXOS_OZONE_WL = "1";
    QT_QPA_PLATFORM = "wayland";
    ELECTRON_OZONE_PLATFORM_HINT = "wayland";
    GDK_BACKEND = "wayland";
    _JAVA_AWT_WM_NONREPARENTING = "1";
    DOCKER_HOST = "unix:///run/user/${toString account.uid}/docker.sock";
  };

  # Font
  fonts.fontconfig.enable = true;
}

