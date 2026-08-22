{ pkgs, ... }:

{
  # SystemD initrd
  boot.initrd.systemd.enable = true;

  # Tmpfiles
  systemd.tmpfiles.rules = [
  "d /var/tmp 1777 root root 7d"
  ];

  environment.systemPackages = with pkgs; [ 
    curl
    gnused
    gost
  ];
}

