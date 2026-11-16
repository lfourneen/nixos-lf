{ lib, config, ... }:

let
  proxy = import ../network/proxy/env.nix { inherit lib; };
  ports = config.my.machine.ports;
  privateV4 = config.my.machine.privateV4;

in
{
  systemd.services.nix-daemon = {
    description = "Nix Daemon (downloads via gost-relay proxy)";
    # Socket-activated: only orders boot-time startup; wants, not requires.
    wants = [ "gost-relay.service" ];
    after = [ "gost-relay.service" ];
    environment = {
      HTTP_PROXY = proxy.gostHttpSlash ports.gostHttp;
      HTTPS_PROXY = proxy.gostHttpSlash ports.gostHttp;
      NO_PROXY = proxy.noProxy { inherit privateV4; };
    };
  };
}
