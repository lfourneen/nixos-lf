{ ... }:

{  
  # Flatpak
  services.flatpak = {
    enable = true;
    packages = [
      "app.zen_browser.zen"
      "cn.lceda.LCEDAPro"
      "cn.wps.wps_365"
      "com.baidu.NetDisk"
      "com.dingtalk.DingTalk"
      "com.github.tchx84.Flatseal"
      "com.jgraph.drawio.desktop"
      "com.qq.QQ"
      "com.ranfdev.DistroShelf"
      "com.spotify.Client"
      "com.st.STM32CubeMX"
      "com.tencent.WeChat"
      "com.usebottles.bottles"
      "com.tencent.wemeet"
      "com.vysp3r.ProtonPlus"
      "io.github.screwys.Rufin"
      "md.obsidian.Obsidian"
      "net.lutris.Lutris"
      "org.onlyoffice.desktopeditors"
      "org.prismlauncher.PrismLauncher"
    ];
    update.auto = {
      enable = true;
      onCalendar = "daily";
    };
  };
}
