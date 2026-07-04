{ config, libs, pkgs, ... }:

let
  tdRoot = "/home/lfour/FHS/TD_4.6.7_Linux";
in
{
  environment.systemPackages = with pkgs; [
    # TD (Qt5) FHS
    (buildFHSEnvBubblewrap {
      name = "td-fhs";
      chdir = tdRoot;
      targetPkgs = pkgs: with pkgs; [
        # Basic Utilities
        bash coreutils file
        unzip which zlib

        # Build Tools & Compilers
        cmake gcc gnumake
        stdenv.cc.cc

        # System & Hardware
        dbus icu libusb1
        libuuid linux-pam udev

        # Graphics & UI (GTK/GL)
        atk cairo fontconfig
        freetype gdk-pixbuf glib
        gtk2 gtk3 libGL
        mesa pango

        # Qt Framework
        libsForQt5.qt5.qtbase libsForQt5.qt5.qttools xkeyboard_config
      ] ++ (with pkgs; [
        # X11 Libraries
        libICE libSM libX11
        libxcb libXcomposite libXcursor
        libXdamage libXext libXfixes
        libXi libXinerama libXrandr
        libXrender xcbutil
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
        export QT_QPA_PLATFORM=xcb
        export QT_XKB_CONFIG_ROOT="${pkgs.xkeyboard_config}/share/X11/xkb"
        unset XDG_SESSION_TYPE
        unset XDG_CURRENT_DESKTOP
        unset GDK_BACKEND
        unset MOZ_ENABLE_WAYLAND
        export LD_LIBRARY_PATH=${tdRoot}/lib/Qt/lib:${tdRoot}/lib:/run/opengl-driver/lib:$LD_LIBRARY_PATH
      '';
    })
  ];
}

