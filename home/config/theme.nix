{ config, pkgs, ... }: 

{
  gtk = {
    enable = true;
    colorScheme = "light";

    theme = {
      name = "Adwaita";
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

    # Breeze's default palette is the light variant. Avoid the Adwaita QStyle
    # and its Qt platform theme: some Qt apps render incorrectly or lag with
    # the Adwaita style.
    style = {
      name = "breeze";
    };
  };
}

