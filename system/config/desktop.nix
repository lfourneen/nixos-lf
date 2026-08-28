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
      defaultSession = "niri";

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
    };
  };

  # DBUS
  services.dbus = {
    enable = true;
    implementation = "broker";
  };

  # XDG
  xdg.portal = {
    enable = true;
    wlr.enable = true;

    extraPortals = [
      pkgs.xdg-desktop-portal
      pkgs.xdg-desktop-portal-gtk
      pkgs.xdg-desktop-portal-wlr
      pkgs.kdePackages.xdg-desktop-portal-kde 
      pkgs.xdg-desktop-portal-gnome
    ];
  };
  
  # Logind
  services.logind = {
    settings = {
      Login = {
        HandleLidSwitch = "lock";
        HandleLidSwitchExternalPower = "lock";
        HandleLidSwitchDocked = "ignore";
      };
    };
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
    gnomeExtensions.kiwi-is-not-apple
    gnomeExtensions.kiwi-menu
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
    ptyxis
    
    # KDE
    #haruna
    #kdePackages.dolphin
    #kdePackages.dolphin-plugins
    #kdePackages.wallpaper-engine-plugin
    wayland-utils # Wayland utilities

    # Niri
    kitty
    lswt
    mpvpaper
    unstable-pkgs.linux-wallpaperengine
    xwayland-satellite # xwayland support
    wl-clipboard
    clipman
    wlr-randr
  ];
}

