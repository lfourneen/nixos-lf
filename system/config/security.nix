{ inputs, lib, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };

in
{
  # Trust & licensing
  # Allow unfree packages
  nixpkgs.config.allowUnfree = true;

  # Explicitly trust system CA bundle
  security.pki.certificates = [
    (builtins.readFile "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt")
  ];
  # Force Nix to use the system CA bundle
  nix.settings.ssl-cert-file = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";

  # Privilege authorization
  security.sudo-rs = {
    enable = true;
    wheelNeedsPassword = true;
    execWheelOnly = true;
  };
  security.sudo.enable = false;

  security.polkit.enable = true;

  # PAM hardening
  security.pam.loginLimits = [
    { domain = "*"; item = "maxlogins"; type = "hard"; value = "10"; }
  ];

  # Kernel & network hardening
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
    "net.ipv4.conf.default.accept_redirects" = 0;
    "net.ipv6.conf.default.accept_redirects" = 0;
    "net.ipv4.conf.all.send_redirects" = 0;
    "net.ipv4.ip_forward" = 1;

    # Log spoofed packets with invalid/martian source addresses
    "net.ipv4.conf.all.log_martians" = 1;
    "net.ipv4.conf.default.log_martians" = 1;

    # Mitigate TCP TIME-WAIT assassination attacks (RFC 1337)
    "net.ipv4.tcp_rfc1337" = 1;

    # Drop source-routed packets (prevents IPv4/IPv6 spoofing and filter bypass)
    "net.ipv4.conf.all.accept_source_route" = 0;
    "net.ipv4.conf.default.accept_source_route" = 0;
    "net.ipv6.conf.all.accept_source_route" = 0;
  };

  # Mandatory access control (AppArmor)
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

  # Sandboxing (Firejail)
  programs.firejail = {
    enable = true;

    wrappedBinaries = {
      media-downloader = {
        executable = "${lib.getBin pkgs.media-downloader}/bin/media-downloader";
        profile = "${pkgs.firejail}/etc/firejail/default.profile";
      };

      mpv = {
        executable = "${lib.getBin pkgs.mpv}/bin/mpv";
        profile = "${pkgs.firejail}/etc/firejail/mpv.profile";
      };

      wine = {
        executable = "${lib.getBin unstable-pkgs.wineWow64Packages.waylandFull}/bin/wine";
        profile = "${pkgs.firejail}/etc/firejail/default.profile";
      };

      yt-dlp = {
        executable = "${lib.getBin pkgs.yt-dlp}/bin/yt-dlp";
        profile = "${pkgs.firejail}/etc/firejail/default.profile";
      };
    };
  };

  # Antivirus (ClamAV)
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

  # Audit
  security.auditd.enable = true;

  security.audit.rules = [
    # Log every execve where the resulting process is root (sudo, daemons, scripts)
    "-a always,exit -F arch=b64 -S execve -F euid=0 -k root_exec"

    # Log any open of files under /sys/devices/system/cpu (CPU freq/state tampering)
    "-a always,exit -F arch=b64 -S openat -F dir=/sys/devices/system/cpu -k sysfs_cpu"
  ];

  # OOM protection
  systemd.oomd = {
    enable = true;
    enableUserSlices = true;
    enableSystemSlice = true;

    settings.OOM = {
      SwapUsedLimit = "90%";
      DefaultMemoryPressureLimit = "80%";
      DefaultMemoryPressureDurationSec = "20s";
    };
  };

  #services.earlyoom = {
    #enable = true;
    #freeMemThreshold = 5;
    #freeSwapThreshold = 10;
    #enableNotifications = true;
    #avoidRegex = "(niri|kitty|Xwayland|wireplumber|pipewire)";
    #preferRegex = "(python|ComfyUI)";
  #};

  # Removable media / desktop integration
  services.gvfs.enable = true;
  services.udisks2.enable = true;
  services.devmon.enable = true;

  # Security tooling
  environment.systemPackages = with pkgs; [
    # CA / TLS
    cacert

    # ClamAV
    clamav clamtk

    # Scanner
    lynis osslsigncode
  ];
}

