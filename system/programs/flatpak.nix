{ config, lib, pkgs, ... }:

let
  # gost's HTTP listener; machine.nix owns the port. NO_PROXY uses the ranges the
  # firewall already treats as local.
  gostHttp = "http://127.0.0.1:${toString config.my.machine.ports.gostHttp}";
  noProxy = "localhost,127.0.0.1,::1,"
    + lib.concatStringsSep "," config.my.machine.privateV4
    + ",192.168.1.1,*.local";

in
{
  services.flatpak = {
    enable = true;
    
    update.auto = {
      enable = true;
      onCalendar = "daily";
    };
    
    # Global override: route all Flatpak apps through gost's HTTP listener,
    # since the killswitch only exempts uid 0 and gost and sandboxes can't
    # read the host dconf proxy settings.
    overrides.settings.global = {
      Environment = {
        HTTP_PROXY = "${gostHttp}";
        HTTPS_PROXY = "${gostHttp}";
        # Lowercase twins: Electron/Chromium and some Go/Rust tooling read only
        # these. ALL_PROXY is deliberately absent - clients that treat it as a
        # SOCKS URL break on an http:// value. NO_PROXY matches the host list.
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
      "app.zen_browser.zen"
      "com.baidu.NetDisk"
      "com.dingtalk.DingTalk"
      "com.discordapp.Discord"
      "com.qq.QQ"
      "im.riot.Riot"
      "com.tencent.WeChat"
      "com.tencent.wemeet"
      "org.telegram.desktop"

      # Development tools
      "cc.arduino.IDE2"
      "cn.lceda.LCEDAPro"
      "com.jetbrains.CLion"
      "com.jetbrains.PyCharm-Professional"
      "com.st.STM32CubeMX"
      "io.qt.Designer"
      "io.qt.Linguist"
      "io.qt.QtCreator"
      "io.qt.qdbusviewer"
      "org.kicad.KiCad"

      # Games & entertainment
      "com.ranfdev.DistroShelf"
      "com.usebottles.bottles"
      "com.vysp3r.ProtonPlus"
      "moe.kopuz.kopuz"
      "net.lutris.Lutris"
      "org.prismlauncher.PrismLauncher"
      "io.github.screwys.Rufin"
      "com.spotify.Client"

      # Multimedia & creativity
      "com.obsproject.Studio"
      "org.blender.Blender"
      "org.freecad.FreeCAD"
      "org.gimp.GIMP"
      "org.inkscape.Inkscape"
      "org.kde.kdenlive"
      "org.kde.krita"
      "org.shotcut.Shotcut"

      # Office & productivity
      "cn.wps.wps_365"
      "com.jgraph.drawio.desktop"
      "md.obsidian.Obsidian"
      "org.onlyoffice.desktopeditors"
      "org.texstudio.TeXstudio"

      # Utilities & system tools
      "com.github.tchx84.Flatseal"
      "org.bleachbit.BleachBit"
      "org.octave.Octave"
      "org.videolan.VLC"
    ];
  };

  # Runs as root, outside the killswitch and without any *_PROXY, so only order
  # it behind gost-pac (wants, not requires: gost-pac is fail-open).
  systemd.services.flatpak-managed-install = {
    wants = [ "gost-pac.service" ];
    after = [ "gost-pac.service" ];
  };
}

