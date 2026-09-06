{ pkgs, ... }:

{
  # System locale
  i18n.defaultLocale = "en_US.UTF-8";

  i18n.extraLocaleSettings = {
    LC_ADDRESS = "en_US.UTF-8";
    LC_IDENTIFICATION = "en_US.UTF-8";
    LC_MEASUREMENT = "en_US.UTF-8";
    LC_MONETARY = "en_US.UTF-8";
    LC_NAME = "en_US.UTF-8";
    LC_NUMERIC = "en_US.UTF-8";
    LC_PAPER = "en_US.UTF-8";
    LC_TELEPHONE = "en_US.UTF-8";
    LC_TIME = "en_US.UTF-8";
  };

  services.xserver.xkb = {
    layout = "us";
    variant = "";
  };

  ## Fcitx5
  i18n.inputMethod = {
    enable = true;
    type = "fcitx5";

    fcitx5 = {
      waylandFrontend = true;

      addons = with pkgs; [
        # IM frontends
        fcitx5-gtk
        kdePackages.fcitx5-qt

        # Rime engine + Ice data
        (fcitx5-rime.override {
          rimeDataPkgs = [
            (pkgs.rime-ice.overrideAttrs (old: {
              postInstall = ''
                mv $out/share/rime-data/rime_ice_suggestion.yaml \
                   $out/share/rime-data/default.yaml
              '';
            }))
          ];
        })
      ];
    };
  };

  environment.systemPackages = with pkgs; [
    adwaita-icon-theme
    kdePackages.fcitx5-configtool
    libayatana-appindicator
    libappindicator
    libappindicator-gtk2
    libappindicator-gtk3
    hicolor-icon-theme
  ];
}

