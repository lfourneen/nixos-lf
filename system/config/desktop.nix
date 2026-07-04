{ inputs, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
in
{
  # Dconf
  programs.dconf.enable = true;
  
  # Desktop Evironment 
  services = {
    # Gnome
    gnome.gnome-keyring.enable = true;
    desktopManager = {
      # GNOME 
      gnome.enable = true;

      # KDE 
      #plasma6.enable = true;

      # Cosmic 
      #cosmic = {
        #enable = true;
        #showExcludedPkgsWarning = true;
        #xwayland.enable = true;
      #};
    };
    displayManager = {
      defaultSession = "gnome";

      #cosmic-greeter = {
        #enable = true;
      #};
      
      # GDM 
      gdm = {
        enable = true;
      };

      # PLM 
      #plasma-login-manager = {
        #enable = true;
      #};

      # SDDM
      #sddm = {
        #enable = true;
        #wayland.enable = true;
      #};
    };
  };

  # LabWC Desktop
  #programs.labwc = {
    #enable = true;
    #package = unstable-pkgs.labwc;
  #};

  xdg.portal = {
    enable = true;
    extraPortals = [
      pkgs.xdg-desktop-portal
      pkgs.xdg-desktop-portal-gtk
      pkgs.xdg-desktop-portal-wlr
      pkgs.kdePackages.xdg-desktop-portal-kde 
      pkgs.xdg-desktop-portal-gnome
    ];
  };

  services.udev.packages = with pkgs; [ 
    pkgs.gnome-settings-daemon
  ];

  # Colord
  services.colord.enable = true;
  # Indusrial I/O
  hardware.sensor.iio.enable = true;

  # Niri
  programs.niri.enable = true;
  programs.xwayland.enable = true;
  #services.greetd = {
    #enable = true;
    #settings = {
      #default_session = {
        #command = ''
          #${pkgs.tuigreet}/bin/tuigreet \
            #--time \
            #--remember \
            #--remember-user-session
        #'';
        #user = "greeter";
      #};
    #};
  #};

  #environment.cosmic.excludePackages = with pkgs; [
    #cosmic-player
  #];

  environment.systemPackages = with pkgs; [ 
    # Gnome extensions
    gnomeExtensions.advanced-weather-companion
    gnomeExtensions.appindicator
    gnomeExtensions.astra-monitor
    gnomeExtensions.bluetooth-battery-meter
    gnomeExtensions.caffeine
    gnomeExtensions.clipboard-indicator
    gnomeExtensions.coverflow-alt-tab
    gnomeExtensions.dash-to-dock
    gnomeExtensions.desktop-icons-ng-ding
    gnomeExtensions.dim-completed-calendar-events
    gnomeExtensions.docker
    gnomeExtensions.emoji-copy
    gnomeExtensions.force-quit
    gnomeExtensions.just-perfection
    gnomeExtensions.proxy-switcher
    gnomeExtensions.quick-settings-audio-devices-renamer
    gnomeExtensions.user-themes
    gnomeExtensions.wifi-qrcode
    gnomeExtensions.workspace-indicator
    
    # Gnome windows themes
    pkgs.adwaita-icon-theme
    adwaita-qt
    adwaita-qt6

    # Applications
    dconf-editor
    ddcutil
    gnome-software
    gnome-tweaks
    hydrapaper
    nautilus
    nautilus-open-any-terminal
    ptyxis
    
    # KDE
    #haruna
    #kdePackages.dolphin
    #kdePackages.dolphin-plugins
    #kdePackages.wallpaper-engine-plugin
    wayland-utils # Wayland utilities

    # Niri
    fuzzel
    kitty
    linux-wallpaperengine
    lswt
    mpvpaper
    #nemo
    xwayland-satellite # xwayland support
    #swayidle
    #swww
    #tuigreet
    wl-clipboard
    clipman
    wlr-randr
  ];
}

