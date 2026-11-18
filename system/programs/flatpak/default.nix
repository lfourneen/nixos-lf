{ config, lib, pkgs, ... }:

let
  proxy = import ../../config/network/proxy/env.nix { inherit lib; };
  gostHttp = proxy.gostHttp config.my.machine.ports.gostHttp;
  noProxy = proxy.noProxy {
    privateV4 = config.my.machine.privateV4;
    extra = [ "192.168.1.1" "*.local" ];
  };

in
{
  imports = [ ./mirror.nix ];
  services.flatpak = {
    enable = true;
    
    update.auto = {
      enable = true;
      onCalendar = "daily";
    };
    
    overrides.settings.global = {
      Environment = {
        GTK_THEME = "Adwaita-dark";
        QT_QPA_PLATFORMTHEME = "gtk3";
      } // lib.optionalAttrs (!config.my.proxy.transparentApplications) {
        HTTP_PROXY = "${gostHttp}";
        HTTPS_PROXY = "${gostHttp}";
        http_proxy = "${gostHttp}";
        https_proxy = "${gostHttp}";
        NO_PROXY = noProxy;
      };
      
      Context = {
        filesystems = [ "xdg-run/dconf" ];
      };
      "Session Bus Policy" = {
        "ca.desrt.dconf" = "talk";
      };
    };

    overrides.settings."cn.lceda.LCEDAPro" = {
      Context = {
        filesystems = [ "~/Projects" "~/.config/LCEDA-Pro" ];
        sockets = [ "!x11" "wayland" ];
      };
      
      "Session Bus Policy" = {
        "org.freedesktop.FileManager1" = "talk";
      };
    };

    # Keep QQ on the Wayland backend (launcher passes --ozone-platform=wayland
    # when WAYLAND_DISPLAY is set) so the CSD titlebar stays draggable under
    # niri, while leaving the X11 socket available for its clipboard code.
    overrides.settings."com.qq.QQ" = {
      Context = {
        sockets = [ "wayland" "x11" ];
      };
    };

    overrides.settings."com.tencent.wemeet" = {
      Context = {
        filesystems = [ "${pkgs.wemeet-cursor-hook}" ];
      };
      
      Environment = {
        LD_PRELOAD = "${pkgs.wemeet-cursor-hook}/lib/libwemeet-cursor-hook.so";
      };
    };

    packages = [
      # Communication tools
      "com.baidu.NetDisk"
      "com.dingtalk.DingTalk"
      "com.discordapp.Discord"
      "com.qq.QQ"
      "im.riot.Riot"
      "com.tencent.WeChat"
      "com.tencent.wemeet"
      "org.telegram.desktop"

      # Development tools
      #"cc.arduino.IDE2"
      "cn.lceda.LCEDAPro"
      #"com.jetbrains.CLion"
      #"com.jetbrains.PyCharm-Professional"
      "com.st.STM32CubeMX"
      #"io.qt.Designer"
      #"io.qt.Linguist"
      #"io.qt.QtCreator"
      #"io.qt.qdbusviewer"
      #"org.kicad.KiCad"

      # Games & entertainment
      #"com.ranfdev.DistroShelf"
      #"com.usebottles.bottles"
      "com.vysp3r.ProtonPlus"
      "moe.kopuz.kopuz"
      "net.lutris.Lutris"
      "org.prismlauncher.PrismLauncher"
      #"io.github.screwys.Rufin"
      "com.spotify.Client"

      # Multimedia & creativity
      #"com.obsproject.Studio"
      #"org.blender.Blender"
      #"org.freecad.FreeCAD"
      #"org.gimp.GIMP"
      #"org.inkscape.Inkscape"
      #"org.kde.kdenlive"
      #"org.kde.krita"
      #"org.shotcut.Shotcut"

      # Office & productivity
      "cn.wps.wps_365"
      #"com.jgraph.drawio.desktop"
      #"md.obsidian.Obsidian"
      #"org.onlyoffice.desktopeditors"
      #"org.texstudio.TeXstudio"

      # Utilities & system tools
      "com.github.tchx84.Flatseal"
      "org.octave.Octave"
      #"org.videolan.VLC"
    ];
  };

  # Order install/update startup behind the relay. Root traffic remains subject
  # to the same mark-based kernel policy; A passthrough is an explicit mode.
  systemd.services.flatpak-managed-install = lib.mkIf (!config.my.proxy.transparentApplications) {
    wants = [ "gost-relay.service" ];
    after = [ "gost-relay.service" ];
  };
}

