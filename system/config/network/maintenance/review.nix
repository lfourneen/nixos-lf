{ config, lib, pkgs, ... }:

let
  selected = pkgs.writeText "network-upgrade-selected.json" (builtins.toJSON config.my.proxy.upgradeSources);
  check = pkgs.runCommand "network-upgrade-review" { } ''
    ${pkgs.python3}/bin/python3 ${./upgrades.py} check \
      ${./sources.json} ${selected}
    touch "$out"
  '';

in
{
  options.my.proxy.upgradeCheck = lib.mkOption { type = lib.types.package; internal = true; readOnly = true; };
  config = {
    my.proxy.upgradeCheck = check;
    system.checks = [ check ];
  };
}
