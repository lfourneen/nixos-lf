{ config, pkgs, ... }:

let
  h = config.my.hardening;
  settings = pkgs.writeText "network-identities.json" (builtins.toJSON {
    nmcli = "${config.networking.networkmanager.package}/bin/nmcli";
    interface = config.my.machine.wireless.name;
    stateDirectory = "/var/lib/network-identity";
    profiles = h.wifi.identities;
    wifiDefault = h.wifi.clonedMacAddress;
    wired = h.wired.clonedMacAddress;
  });

  check = pkgs.runCommand "network-identity-config" { } ''
    ${pkgs.python3}/bin/python3 ${./link_identity.py} ${settings} check-config
    touch "$out"
  '';

  tool = pkgs.writeShellScriptBin "network-identity" ''
    exec ${pkgs.python3}/bin/python3 ${./link_identity.py} ${settings} "$@"
  '';

in
{
  my.hardening.linkIdentitySettings = settings;
  my.hardening.linkIdentityCheck = check;
  system.checks = [ check ];
  environment.systemPackages = [ tool ];
  systemd.tmpfiles.rules = [ "d /var/lib/network-identity 0700 root root -" ];
}
