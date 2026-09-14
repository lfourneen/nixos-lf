{ ... }:

{
  services.flatpak = {
    enable = true;
    
    update.auto = {
      enable = true;
      onCalendar = "daily";
    };

    # Global override: route all Flatpak apps through gost (127.0.0.1:33332),
    # since the killswitch only exempts uid 0 and gost and sandboxes can't
    # read the host dconf proxy settings.
    overrides.settings.global = {
      Environment = {
        HTTP_PROXY = "http://127.0.0.1:33332";
        HTTPS_PROXY = "http://127.0.0.1:33332";
        NO_PROXY = "localhost,127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10";
      };
      Context = {
        filesystems = [ "xdg-run/dconf" ];
      };
      "Session Bus Policy" = {
        "ca.desrt.dconf" = "talk";
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
}

