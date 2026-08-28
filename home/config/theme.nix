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
    };

    gtk4 = {
      enable = true;
      theme = config.gtk.theme;
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

