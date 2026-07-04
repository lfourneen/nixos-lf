{ lib, pkgs, ... }:

{
  # Privilege authorization and authentication mechanisms
  security.sudo-rs = {
    enable = true;
    wheelNeedsPassword = true;
    execWheelOnly = true;
  };
  security.sudo.enable = false;

  # Allow unfree packages
  nixpkgs.config.allowUnfree = true;

  # Explicitly trust system CA bundle
  security.pki.certificates = [
    (builtins.readFile "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt")
  ];
  # Force Nix to use the system CA bundle
  nix.settings.ssl-cert-file = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";

  #Tailscale
  services.tailscale.enable = true;

  # AppArmor
  services.dbus.apparmor = "enabled";
  security.apparmor = {
    enable = true;
    enableCache = true;
    killUnconfinedConfinables = true;
    packages = with pkgs; [
      apparmor-utils
      apparmor-profiles
    ];
  };

  # ClamAV
  services.clamav = {
    daemon.enable = false;
    scanner.enable = false;
    updater.enable = true;
    updater.interval = "daily";
    #fangfrisch.enable = true;
    #clamonacc.enable = true;
    #daemon.settings = {
      #OnAccessPrevention = true;
      #OnAccessIncludePath = "/home/lfour/Downloads";
    #};
  };

  # Firejail
  programs.firejail = {
    enable = true;
    wrappedBinaries = {
      #firefox = {
        #executable = "${lib.getBin pkgs.firefox}/bin/firefox";
        #profile = "${pkgs.firejail}/etc/firejail/firefox.profile";
      #};
      #google-chrome = {
        #executable = "${lib.getBin pkgs.google-chrome}/bin/google-chrome";
        #profile = "${pkgs.firejail}/etc/firejail/google-chrome.profile";
      #};
      mpv = {
        executable = "${lib.getBin pkgs.mpv}/bin/mpv";
        profile = "${pkgs.firejail}/etc/firejail/mpv.profile";
      };
    };
  };

  # Polkit
  security.polkit.enable = true;

  # Kernel Sysctl
  boot.kernel.sysctl = {
    # Restrict kernel pointer exposure (non-root)
    "kernel.kptr_restrict" = 1;

    # Allow dmesg access (required for GPU / DRM debugging)
    "kernel.dmesg_restrict" = 0;

    # Disable core dumps for setuid binaries
    "fs.suid_dumpable" = 0;

    # Network layer hardening
    "net.ipv4.conf.all.accept_redirects" = 0;
    "net.ipv6.conf.all.accept_redirects" = 0;
    "net.ipv4.conf.all.send_redirects" = 0;
    "net.ipv4.ip_forward" = 1;
  };

  # PAM Hardening
  security.pam.loginLimits = [
    { domain = "*"; item = "maxlogins"; type = "hard"; value = "10"; }
  ];

  # OOM Protection
  systemd.oomd.enable = true;
  services.earlyoom = {
    enable = true;
    freeMemThreshold = 10;
    freeSwapThreshold = 10;
  };

  # Removable media / desktop integration
  services.gvfs.enable = true;
  services.udisks2.enable = true;
  services.devmon.enable = true;

  # Auditd: noisy and unnecessary for desktop usage
  security.auditd.enable = false;

  environment.systemPackages = with pkgs; [
    # CA / TLS
    cacert
    # ClamAV
    clamav clamtk 
    # Scanner
    lynis osslsigncode
  ];
}

