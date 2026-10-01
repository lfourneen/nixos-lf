{ config, libs, pkgs, ... }:

let
  # TD 5.6.3 release 88061 (NL). Extract the vendor zip found in first:
  # unzip -q /path/to/TD_5.6.3_Release_88061_NL.zip -d ~/FHS/
  tdRelease = "5.6.3";
  tdBuild = "88061";
  tdRoot = "${config.users.users.lfour.home}/FHS/TD_${tdRelease}_Release_${tdBuild}_NL";

in
{
  environment.systemPackages = with pkgs; [
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
        gtk2 libGL mesa
        pango

        # Qt/xcb runtime (Qt itself is statically linked)
        xkeyboard_config libxkbcommon
      ] ++ (with pkgs; [
        # X11 Libraries
        libICE libSM libX11
        libxcb xcbutil libXcomposite
        libXcursor libXdamage libXext
        libXfixes libXi libXinerama
        libXrandr libXrender
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
        export LD_LIBRARY_PATH=${tdRoot}/lib:/run/opengl-driver/lib:$LD_LIBRARY_PATH
      '';
    })
  ];
}

