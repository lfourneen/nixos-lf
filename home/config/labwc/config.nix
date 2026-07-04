{ inputs, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
in
{
  wayland.windowManager.labwc = {
    enable = true;
    package = unstable-pkgs.labwc;
    systemd.enable = true;
    xwayland.enable = true;
  };

  home.packages = with pkgs;[
    unstable-pkgs.labwc-tweaks
    unstable-pkgs.labwc-gtktheme
    unstable-pkgs.labwc-tweaks-gtk
    unstable-pkgs.labwc-menu-generator
  ];
}

