{ pkgs, ... }:

{
  # SystemD initrd
  boot.initrd.systemd.enable = true;

  # /var/tmp is left to systemd's tmp.conf (30d): a top-level rule here applies
  # to the main system too, and the duplicate won, silently cutting it to 7d.

  # environment.systemPackages only reaches the main system PATH, so anything the
  # initrd needs is added via extraBin. `gost-pac` uses absolute store paths and
  # runs in the main system, so gost is not needed here.
  boot.initrd.systemd.extraBin = {
    curl = "${pkgs.curl}/bin/curl";
    sed = "${pkgs.gnused}/bin/sed";
  };
}

