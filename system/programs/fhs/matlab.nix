{ config, libs, pkgs, ... }:

let
  MatlabRoot = "/home/lfour/FHS/MATLAB";
in
{
  environment.systemPackages = with pkgs; [
    # MATLAB FHS
    (buildFHSEnvBubblewrap {
      name = "matlab-fhs";
      chdir = MatlabRoot;
      targetPkgs = pkgs: with pkgs; [
        # Archive & Core Utilities
        glibcLocales libuuid ncurses
        procps unzip zlib

        # Programming & Compilers
        gcc gfortran glibc
        jre python313

        # System & Hardware
        alsa-lib cups dbus
        nspr nss pam
        udev

        # Graphics & UI Frameworks
        atk cairo fontconfig
        gdk-pixbuf glib gtk2
        gtk3 libdrm libgbm
        libGL libxfixes libxft
        libxkbcommon mesa pango
      ] ++ (with pkgs; [
        # X11 Libraries
        libICE libSM libX11
        libXcomposite libXcursor libXdamage
        libXext libXi libXinerama
        libXrandr libXrender libXt
        libXtst libXxf86vm
      ]);
      extraBwrapArgs = [
        "--bind" "/run/udev" "/run/udev"
        "--bind-try" "/var/run/dbus" "/var/run/dbus"
        "--dev-bind" "/dev" "/dev"
      ];
      runScript = "bash";
      profile = ''
        export FHS=1
        export LANG=en_US.UTF-8
        export LC_ALL=en_US.UTF-8
        export QT_QPA_PLATFORM=xcb           # force X11 over Wayland
        export GDK_BACKEND=x11
        export LIBGL_ALWAYS_INDIRECT=1       # sometimes helps if GL probing fails
        export LD_LIBRARY_PATH="${MatlabRoot}/bin/glnxa64/:$LD_LIBRARY_PATH"
      '';
    })
  ];
}

