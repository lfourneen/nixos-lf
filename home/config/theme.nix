{ config, pkgs, ... }: 

{
  gtk = {
    enable = true;
    colorScheme = "dark";

    theme = {
      name = "Adwaita-dark";
      package = pkgs.gnome-themes-extra;
    };

    iconTheme = {
      name = "Adwaita";
      package = pkgs.adwaita-icon-theme;
    };

    gtk3 = {
      enable = true;
      theme = config.gtk.theme;
      extraConfig = {
        gtk-cursor-theme-name = "Iochi Mari (Gym ver.)";
        gtk-cursor-theme-size = 48;
      };
    };

    gtk4 = {
      enable = true;
      theme = config.gtk.theme;
      extraConfig = {
        gtk-cursor-theme-name = "Iochi Mari (Gym ver.)";
        gtk-cursor-theme-size = 48;
      };
    };
  };

  qt = {
    enable = true;
    platformTheme.name = "adwaita";

    style = {
      name = "adwaita-dark";
      package = pkgs.adwaita-qt6;
    };
  };
}

