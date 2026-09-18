{ pkgs, ... }:

{
  # SystemD initrd
  boot.initrd.systemd.enable = true;

  # /var/tmp is left to systemd's tmp.conf (30d): a top-level rule here applies
  # to the main system too, and the duplicate won, silently cutting it to 7d.

  # environment.systemPackages only reaches the main system PATH (the initrd had
  # none of these), so add them via extraBin. Only `gost` leaves the main PATH
  # (curl/sed are still provided by nixpkgs defaultPackages) and gost-pac uses
  # absolute store paths, so the proxy chain is unaffected. Cost: the initrd
  # grows by the binaries plus their direct shared libraries.
  boot.initrd.systemd.extraBin = {
    curl = "${pkgs.curl}/bin/curl";
    gost = "${pkgs.gost}/bin/gost";
    sed = "${pkgs.gnused}/bin/sed";
  };
}

