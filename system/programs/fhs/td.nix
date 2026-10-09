{ config, libs, pkgs, ... }:

let
  # TangDynasty TD 2026.2 release (NL). Extract the vendor zip first:
  # unzip -q /path/to/TD_Release_2026.2_NL.zip -d ~/FHS/
  tdRelease = "2026.2";
  tdRoot = "${config.users.users.lfour.home}/FHS/TD_Release_${tdRelease}_NL";

  # Sandboxed runtime exposing every native library the vendor binary needs.
  tdFhs = pkgs.buildFHSEnvBubblewrap {
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
      dbus icu krb5 libusb1
      libuuid linux-pam udev

      # Graphics & UI (GTK/GL)
      atk cairo fontconfig
      freetype gdk-pixbuf glib
      gtk2 libGL mesa
      pango

      # Qt/xcb runtime (Qt5 + ICU are bundled under ${tdRoot}/lib/Qt)
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
      export QT_PLUGIN_PATH=${tdRoot}/lib/Qt/plugins
      unset XDG_SESSION_TYPE
      unset XDG_CURRENT_DESKTOP
      unset GDK_BACKEND
      unset MOZ_ENABLE_WAYLAND
      export LD_LIBRARY_PATH=${tdRoot}/lib:${tdRoot}/lib/Qt/lib:/run/opengl-driver/lib:$LD_LIBRARY_PATH
    '';
  };

  # `td` command that jumps straight into the GUI instead of dropping you into a
  # shell where you still have to `cd` somewhere and run `./td.sh -gui`.
  td = pkgs.writeShellScriptBin "td" ''
    exec ${tdFhs}/bin/td-fhs -c 'cd "${tdRoot}" && exec "${tdRoot}/bin/td.sh" -gui'
  '';

  # Launcher icon (extracted from the vendor binary) so the icon theme can find it.
  tdIcon = pkgs.runCommandLocal "td-icon" { } ''
    install -Dm644 ${./icons/td-icon.png} $out/share/icons/hicolor/512x512/apps/td.png
  '';

  # Desktop entry so Noctalia's application search lists and launches TD.
  tdDesktop = pkgs.makeDesktopItem {
    name = "td";
    desktopName = "TangDynasty TD";
    genericName = "FPGA / EDA Design Suite";
    comment = "Anlogic TangDynasty TD ${tdRelease}";
    exec = "${td}/bin/td";
    icon = "td";
    terminal = false;
    categories = [ "Development" "Electronics" ];
    type = "Application";
  };

in
{
  environment.systemPackages = [
    tdFhs
    td
    tdIcon
    tdDesktop
  ];
}

