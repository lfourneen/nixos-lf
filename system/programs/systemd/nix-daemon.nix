{ lib, config, ... }:

{
  systemd.services.nix-daemon = {
    description = "Nix Daemon (downloads via gost-pac proxy)";
    # Socket-activated: only orders boot-time startup; wants, not requires.
    wants = [ "gost-pac.service" ];
    after = [ "gost-pac.service" ];
    environment = {
      HTTP_PROXY = "http://127.0.0.1:${toString config.my.machine.ports.gostHttp}/";
      HTTPS_PROXY = "http://127.0.0.1:${toString config.my.machine.ports.gostHttp}/";
      NO_PROXY = "127.0.0.1,localhost,::1,${lib.concatStringsSep "," config.my.machine.privateV4}";
    };
  };
}

