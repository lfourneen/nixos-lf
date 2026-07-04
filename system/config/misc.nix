{ pkgs, ... }:

{
  # Nix settings 
  nix.settings = {
    experimental-features = [ "nix-command" "flakes" ];
    system-features = [ "gccarch-znver3" "gccarch-x86-64-v3" "gccarch-x86-64-v2" "gccarch-x86-64" ];
    trusted-users = [ "root" "lfour" ];
    auto-optimise-store = true;
    max-jobs = 16;
  };

  # Linux kernel
  boot.kernelPackages = pkgs.linuxPackages_xanmod_latest;

  # GC
  nix.gc = {
    automatic = true;
    dates = "weekly";
    options = "--delete-older-than 3d";
  };

  # Systemd service: optimise
  nix.optimise = {
    automatic = true;
    dates = ["daily"];
  };

  time.timeZone = "Asia/Shanghai";

  # Valid font config for Chinese + English + Nerd Fonts
  fonts = {
    fontDir.enable = true;
    packages = with pkgs; [
      maple-mono.NF
      maple-mono.NF-CN
      nerd-fonts.jetbrains-mono
      nerd-fonts.ubuntu-mono
      nerd-fonts.ubuntu
      noto-fonts
      noto-fonts-cjk-sans
      noto-fonts-cjk-serif
      font-awesome
      noto-fonts-color-emoji
      twitter-color-emoji
    ];
  };

  # Required fallback to avoid GNOME/KDE fcitx5 segmentation bugs
  fonts.fontconfig.defaultFonts = {
    sansSerif = [ "Maple Mono NF CN" ];
    serif     = [ "Maple Mono NF CN" ];
    monospace = [ "Maple Mono NF CN" ];
    emoji     = [ "Twitter Color Emoji" ];
  };

  # NixOS Version
  system.stateVersion = "26.05";

  environment.systemPackages = with pkgs; [
    nh
  ];
}

