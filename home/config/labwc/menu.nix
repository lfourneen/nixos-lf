{
  wayland.windowManager.labwc.menu = [
    {
      menuId = "root-menu";
      label = "Labwc";
      items = [
        {
          label = "Terminal";
          action = {
            name = "Execute";
            command = "alacritty";
          };
        }
        {
          label = "Browser";
          action = {
            name = "Execute";
            command = "chrome --ozone-platform=x11 %U";
          };
        }
        {
          label = "Files Manager";
          action = {
            name = "Execute";
            command = "dolphin";
          };
        }
        {
          label = "Applications";
          action = {
            name = "Execute";
            command = "fuzzel";
          };
        }
        {
          separator = true;
        }
        {
          label = "Exit labwc";
          action = {
            name = "Exit";
          };
        }
      ];
    }
  ];
}

