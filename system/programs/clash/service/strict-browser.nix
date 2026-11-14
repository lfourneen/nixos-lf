{ config, lib, pkgs, ... }:

let
  p = config.my.proxy;
  settings = pkgs.writeText "strict-browser-relay.json" (builtins.toJSON {
    stateModule = ../../../config/network/proxy/state.py;
    port = config.my.machine.ports.mihomoStrict;
    coreUid = config.my.machine.uids.clashCore;
    maxAge = p.stateMaxAgeSeconds;
    verificationMaxAge = p.verificationMaxAgeSeconds;
    sourceAddress = if p.coreNetns then p.coreTopology.host4 else "127.0.0.1";
    observer = if p.coreNetns then p.coreTopology.observer else null;
    observerModule = ../../../config/network/proxy/core_network.py;
  });

in
{
  config = lib.mkIf p.strictBrowser {
    assertions = [{ assertion = p.controlledSources; message = "Strict browser inlet requires deterministic controlled-source routing."; }];
    systemd.sockets.clash-strict = {
      wantedBy = [ "sockets.target" ];
      socketConfig = {
        ListenStream = "/run/clash-strict/proxy.sock";
        SocketMode = "0600";
        SocketUser = config.my.machine.desktopUser;
        DirectoryMode = "0755";
        RemoveOnStop = true;
      };
    };

    systemd.services.clash-strict = {
      serviceConfig = {
        ExecStart = "${pkgs.python3}/bin/python3 -I ${../../../../home/programs/firefox/network.py} host ${settings}";
        User = "gost";
        Group = "gost";
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        RestrictAddressFamilies = [ "AF_INET" "AF_UNIX" ];
        IPAddressDeny = "any";
        IPAddressAllow = [ "localhost" ] ++ lib.optionals p.coreNetns
          (let t = p.coreTopology; in [ t.host4 t.core4 ]);
        MemoryMax = "128M";
        TasksMax = 64;
        Restart = "on-failure";
      };
    };
  };
}
