{ ... }:
{
  services.flatpak = {
    enable = true;
    packages = [
      # Communication tools
      "app.zen_browser.zen"
      "com.baidu.NetDisk"
      "com.dingtalk.DingTalk"
      "com.discordapp.Discord"
      "com.qq.QQ"
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

      # Utilities & system tools
      "com.github.tchx84.Flatseal"
      "io.github.screwys.Rufin"
      "org.bleachbit.BleachBit"
      "org.octave.Octave"
    ];
    update.auto = {
      enable = true;
      onCalendar = "daily";
    };
  };
}

