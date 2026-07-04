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
        # Input method modules
        fcitx5-gtk
        kdePackages.fcitx5-qt

        # RIME engine
        fcitx5-rime
	      librime-lua
	      librime

	      # Chinese pinyin input method
	      libpinyin
	      rime-ice

        # Yunpinyin / extended dictionaries
        fcitx5-pinyin-moegirl
        qt6Packages.fcitx5-chinese-addons   # (optional) more engines and schemas
	      rime-zhwiki
	      rime-moegirl
	      rime-wanxiang
      ];
    };
  };

  environment.systemPackages = with pkgs; [
    kdePackages.fcitx5-configtool
  ];
}

