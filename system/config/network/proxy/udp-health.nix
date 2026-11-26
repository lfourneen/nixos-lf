{ config, lib, pkgs, ... }:

let
  p = config.my.proxy;
  cfg = p.udpHealth;
  user = config.my.machine.desktopUser;
  script = "${./.}/udp_health.py";
  settings = pkgs.writeText "proxy-udp-health.json" (builtins.toJSON (cfg // {
    uid = config.users.users.${user}.uid;
    gid = config.users.groups.${config.users.users.${user}.group}.gid;
    setpriv = "${pkgs.util-linux}/bin/setpriv";
  }));
  validated = pkgs.runCommand "proxy-udp-health-settings" { } ''
    ${pkgs.python3}/bin/python3 ${script} ${settings} --validate
    cp ${settings} "$out"
  '';

in
{
  options.my.proxy.udpHealth = lib.mkOption {
    type = lib.types.nullOr (lib.types.submodule {
      options = {
        address = lib.mkOption { type = lib.types.str; description = "Public IP literal of an operator-approved UDP echo endpoint."; };
        port = lib.mkOption { type = lib.types.ints.between 1 65535; description = "UDP echo port."; };
      };
    });
    default = null;
    description = "Advisory echo measurement for healthy transparent Proxy-only. Uses one datagram every two minutes.";
  };

  config = lib.mkIf (cfg != null) {
    assertions = [{
      assertion = p.runtimeRouting && p.transparentApplications;
      message = "my.proxy.udpHealth requires managed transparent applications and runtime routing.";
    }];
    environment.etc."proxy-udp-health.json".source = validated;
    systemd.services.proxy-udp-health = {
      description = "Measure UDP through the authorized Proxy-only exit";
      after = [ "proxy-mode.service" ];
      unitConfig = { StartLimitIntervalSec = 60; StartLimitBurst = 1; };
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${pkgs.python3}/bin/python3 ${script} ${validated}";
        TimeoutStartSec = 8;
        UMask = "0077";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        ReadWritePaths = [ "/run/proxy-mode" ];
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
      };
    };
    systemd.timers.proxy-udp-health = {
      wantedBy = [ "timers.target" ];
      timerConfig = { OnActiveSec = "1min"; OnUnitInactiveSec = "2min"; AccuracySec = "5s"; };
    };
  };
}
