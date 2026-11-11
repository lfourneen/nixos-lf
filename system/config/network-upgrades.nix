{ config, lib, pkgs, ... }:

let
  # Fingerprint upstream sources and their primary package recipe, rather than
  # the entire Nixpkgs revision or a locally patched binary's derivation.
  fingerprint = package: let
    position = builtins.match "(.*):[0-9]+" package.meta.position;
  in {
    version = package.version;
    source = package.src.outputHash;
    dependencies = package.goModules.outputHash or (package.cargoDeps.outputHash or null);
    recipe = builtins.hashFile "sha256" (builtins.head position);
  };
  sources = builtins.mapAttrs (_: fingerprint) {
    mihomo = config.programs.clash-verge.package.managedCore;
    gost = config.my.proxy.gostPackage;
    helper = config.programs.clash-verge.package.managedService;
    gui = config.programs.clash-verge.package.unwrapped;
    dnsmasq = config.services.dnsmasq.package;
    firefox = config.home-manager.users.${config.my.machine.desktopUser}.programs.firefox.finalPackage.unwrapped;
    unbound = config.services.unbound.package;
    chrony = config.services.chrony.package;
    tailscale = config.services.tailscale.package;
    nftables = pkgs.nftables;
    networkmanager = config.networking.networkmanager.package;
    systemd = config.systemd.package;
    bubblewrap = pkgs.bubblewrap;
    dbusProxy = pkgs.xdg-dbus-proxy;
    seccomp = pkgs.libseccomp;
    dhcpcd = pkgs.dhcpcd;
  };
  selected = pkgs.writeText "network-upgrade-selected.json" (builtins.toJSON sources);
  check = pkgs.runCommand "network-upgrade-review" { } ''
    ${pkgs.python3}/bin/python3 ${../programs/systemd/netsec/upgrades.py} check \
      ${./network-upgrades.json} ${selected}
    touch "$out"
  '';
in
{
  options.my.proxy = {
    upgradeSources = lib.mkOption { type = lib.types.attrs; internal = true; readOnly = true; };
    upgradeCheck = lib.mkOption { type = lib.types.package; internal = true; readOnly = true; };
  };
  config = {
    my.proxy.upgradeSources = sources;
    my.proxy.upgradeCheck = check;
    system.checks = [ check ];
  };
}
