{ pkgs, ... }:

{
  # Nix settings 
  nix.settings = {
    experimental-features = [ "nix-command" "flakes" ];
    system-features = [ "gccarch-znver4" "gccarch-znver3" "gccarch-x86-64-v3" "gccarch-x86-64-v2" "gccarch-x86-64" ];
    trusted-users = [ "root" "lfour" "hermes" ];
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

  # Optimise
  nix.optimise = {
    automatic = true;
    dates = ["daily"];
  };

  time = {
    timeZone = "Asia/Shanghai";
    # Windows stores local time in hardware RTC; NixOS stores UTC by default.
    # Set local RTC so both OSes agree on the hardware clock (dual-boot fix).
    hardwareClockInLocalTime = true;
  };

  # User services that ignore SIGTERM (or block on a dying NetworkManager)
  # only get cleaned up after the user manager's 90s default stop timeout,
  # which is what the "Stopping User Manager" hang is. Kill them 10s after
  # SIGTERM instead.
  systemd.user.extraConfig = "DefaultTimeoutStopSec=10s";

  # Valid font config
  fonts = {
    fontDir.enable = true;
    packages = with pkgs; [
      google-fonts
      font-awesome
      maple-mono.NF
      maple-mono.NF-CN
      noto-fonts
      noto-fonts-cjk-sans
      noto-fonts-cjk-serif
      noto-fonts-color-emoji
      nerd-fonts.jetbrains-mono
      nerd-fonts.ubuntu
      nerd-fonts.ubuntu-mono
      twitter-color-emoji
      times-newer-roman
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

}

