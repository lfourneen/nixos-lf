{ config, ... }:

{
  environment = {
    sessionVariables = {
      # Enable Wayland for Electron/Chromium apps
      NIXOS_OZONE_WL = "1";

      # Fix cursor glitches on some GPUs (Wayland)
      WLR_NO_HARDWARE_CURSORS = "1";

      # AMD CPU
      WINE_CPU_TOPOLOGY = "16:0-15";

      # Fcitx5 variables — Wayland-safe
      #GTK_IM_MODULE = "fcitx";   # For gnome
      QT_IM_MODULE = "fcitx";
      QT_IM_MODULES = "wayland;fcitx";
      XIM="fcitx";
      XMODIFIERS = "@im=fcitx";
      GLFW_IM_MODULE = "fcitx";   # For games/tools using GLFW
      SDL_IM_MODULE = "fcitx";    # For SDL apps (games)

      # Proxy
      #http_proxy = "";
      #https_proxy = "";
      #all_proxy = "";
      #ALL_PROXY = "";
      #NO_PROXY = "";
    };
  
    variables = {
      EDITOR = "nvim";
    
      # sops CLI age identity: /etc/environment is not $HOME-expanded, so use an
      # absolute path. Works even in root shells (sops set under /etc/nixos).
      SOPS_AGE_KEY_FILE = "${config.users.users.lfour.home}/.config/sops/age/keys.txt";
    };

    extraOutputsToInstall = [ "dev" ];
  };

  systemd.tmpfiles.rules = [
    "L+ /run/docker.sock - - - - /run/user/1000/docker.sock"
  ];
}

