{ pkgs, lib, ... }:

let
  defaultApplications = import ./mimeapps.nix;

in
{
  # Dconf
  programs.dconf.enable = true;

  # Default applications live at the system level so ~/.config/mimeapps.list
  # stays a writable file the desktop can rewrite ("set as default").
  # See system/config/mimeapps.nix for why home-manager's xdg.mimeApps broke it.
  environment.etc."xdg/mimeapps.list".text =
    "[Default Applications]\n"
    + lib.concatStrings (lib.mapAttrsToList (type: app: "${type}=${app}\n") defaultApplications);

  # Desktop Evironment 
  services = {
    # Gnome
    gnome.gnome-keyring.enable = true;

    desktopManager = {
      # GNOME 
      gnome.enable = true;
    };

    displayManager = {
      defaultSession = "niri";

      # GDM 
      gdm = {
        enable = true;
      };
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
    pkgs.unstable.gnomeExtensions.status-tray
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


    # Niri
    kitty
    lswt
    pkgs.unstable.linux-wallpaperengine
    pkgs.unstable.mpvpaper
    xwayland-satellite # xwayland support
    wl-clipboard
    clipman
    wayland-utils
    wlr-randr
  ];
}

