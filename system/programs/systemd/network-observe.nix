{ pkgs, config, ... }:

let
  settings = pkgs.writeText "proxy-inspect.json" (builtins.toJSON {
    interfaces = [ config.my.machine.wired.name config.my.machine.wireless.name config.my.proxy.tunDev ];
    ip = "${pkgs.iproute2}/bin/ip";
    tc = "${pkgs.iproute2}/bin/tc";
    ethtool = "${pkgs.ethtool}/bin/ethtool";
    systemctl = "${pkgs.systemd}/bin/systemctl";
  });

in
{
  environment.systemPackages = [ (pkgs.writeShellScriptBin "proxy-inspect" ''
    exec ${pkgs.python3}/bin/python3 ${./netsec/inspect.py} ${settings}
  '') ];
  systemd.services.clash-verge.serviceConfig = { CPUAccounting = true; MemoryAccounting = true; };
  systemd.services.gost-relay.serviceConfig = { CPUAccounting = true; MemoryAccounting = true; };
}

