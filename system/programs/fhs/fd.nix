{ config, pkgs, ... }:

let
  # FutureDynasty FD 2026.2 release. Extract the vendor zip first:
  # unzip -q /path/to/FD_Release_2026.2.zip -d ~/FHS/
  fdRelease = "2026.2";
  fdRoot = "${config.users.users.lfour.home}/FHS/FD_Release_${fdRelease}/FutureDynasty";

  # Sandboxed runtime exposing every native library the vendor Eclipse-based
  # IDE, its bundled JRE and the TD tools under ${fdRoot}/tools need.
  fdFhs = pkgs.buildFHSEnvBubblewrap {
    name = "fd-fhs";
    targetPkgs = pkgs: with pkgs; [
      # Basic Utilities
      bash coreutils file findutils
      unzip which xdg-utils zlib

      # Build Tools & Compilers
      cmake gcc gnumake
      stdenv.cc.cc stdenv.cc.cc.lib

      # System & Hardware
      dbus icu krb5 libusb1
      libuuid libsecret libxcrypt-legacy
      linux-pam ncurses openssl udev

      # Graphics & UI (Eclipse/GTK3 + bundled Qt5)
      atk at-spi2-atk cairo fontconfig
      freetype gdk-pixbuf glib
      gtk2 gtk3 libGL mesa
      pango

      # Qt/xcb runtime (Qt5 is bundled under ${fdRoot}/tools/td/lib/Qt)
      xkeyboard_config libxkbcommon
    ] ++ (with pkgs; [
      # X11 Libraries
      libICE libSM libX11
      libxcb xcbutil libXcomposite
      libXcursor libXdamage libXext
      libXfixes libXi libXinerama
      libXrandr libXrender libXtst
      libxshmfence
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

      # Mirror the vendor `fd` launcher so the IDE and the bundled TD tools
      # resolve their SDK files.
      export FD_SDK_PATH="${fdRoot}"
      export IDE_ROOT_PATH="${fdRoot}"
      export BSP_RESOURCE_PATH="${fdRoot}/data/embeddedsw"
      export IDE_TOOL_PATH="${fdRoot}/tools"
      export PATH="${fdRoot}:${fdRoot}/tools:$PATH"

      export QT_QPA_PLATFORM=xcb
      export QT_XKB_CONFIG_ROOT="${pkgs.xkeyboard_config}/share/X11/xkb"
      export QT_PLUGIN_PATH="${fdRoot}/tools/td/lib/Qt/plugins"
      unset XDG_SESSION_TYPE
      unset XDG_CURRENT_DESKTOP
      unset GDK_BACKEND
      unset MOZ_ENABLE_WAYLAND
      export LD_LIBRARY_PATH="${fdRoot}/tools/td/lib:${fdRoot}/tools/td/lib/Qt/lib:/run/opengl-driver/lib:$LD_LIBRARY_PATH"
    '';
  };

  # `fd` command that jumps straight into the IDE instead of dropping you into
  # a shell where you still have to `cd` somewhere and run `./fd`.
  fd = pkgs.writeShellScriptBin "fd" ''
    exec ${fdFhs}/bin/fd-fhs -c 'cd "${fdRoot}" && exec "${fdRoot}/fd"'
  '';

  # Launcher icon (taken from the vendor product plugin) so the icon theme can
  # find it.
  fdIcon = pkgs.runCommandLocal "fd-icon" { } ''
    install -Dm644 ${./icons/fd-icon.png} $out/share/icons/hicolor/256x256/apps/fd.png
  '';

  # Desktop entry so Noctalia's application search lists and launches FD.
  fdDesktop = pkgs.makeDesktopItem {
    name = "fd";
    desktopName = "FutureDynasty FD";
    genericName = "FPGA / SoC Embedded IDE";
    comment = "Anlogic FutureDynasty FD ${fdRelease}";
    exec = "${fd}/bin/fd";
    icon = "fd";
    terminal = false;
    categories = [ "Development" "Electronics" ];
    type = "Application";
  };

in
{
  environment.systemPackages = [
    fdFhs
    fd
    fdIcon
    fdDesktop
  ];
}

