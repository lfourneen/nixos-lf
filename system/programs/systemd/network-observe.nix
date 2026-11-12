{ pkgs, config, ... }:

let
  settings = pkgs.writeText "proxy-inspect.json" (builtins.toJSON {
    interfaces = [ config.my.machine.wired.name config.my.machine.wireless.name config.my.proxy.tunDev ];
    ip = "${pkgs.iproute2}/bin/ip";
    tc = "${pkgs.iproute2}/bin/tc";
    ethtool = "${pkgs.ethtool}/bin/ethtool";
    systemctl = "${pkgs.systemd}/bin/systemctl";

    policy = {
      native = {
        mode = config.my.hardening.directEgress.mode;
        inherit (config.my.hardening) allowPrivateEgress;
        grants = config.my.hardening.directEgress.exceptions;
      };

      core = {
        inherit (config.my.proxy) proxyOnly localTargetCidrs privateTargetDomains subscriptionTrust;
        remoteGrants = map (grant: {
          leafIdentity = builtins.hashString "sha256" grant.proxy;
          inherit (grant) cidr inlets network ports;
        }) config.my.proxy.remoteTargetGrants;
        insecureCertificateExceptions = builtins.length config.my.proxy.insecureProxyExceptions;
        mutableRemoteIdentities = config.my.proxy.allowDynamicRemoteGrants;
      };

      guests = {
        interfaces = config.my.proxy.guestInterfaces;
        socketAccounts = config.my.hardening.socketGuestUsers;
      };
      boundary = "Native grants and core target grants are independent; local proxy sockets do not preserve the caller UID. Root and managed infrastructure are trusted.";
    };
  });

in
{
  environment.systemPackages = [ (pkgs.writeShellScriptBin "proxy-status" ''
    exec ${pkgs.python3}/bin/python3 ${./netsec/status.py} ${./netsec/state.py} \
      --max-age ${toString config.my.proxy.stateMaxAgeSeconds} \
      --verification-max-age ${toString config.my.proxy.verificationMaxAgeSeconds} ${if config.my.proxy.proxyOnly then "--proxy-only" else ""} "$@"
  '') (pkgs.writeShellScriptBin "proxy-inspect" ''
    exec ${pkgs.python3}/bin/python3 ${./netsec/inspect.py} ${settings} "$@"
  '') (pkgs.writeShellScriptBin "proxy-policy" ''
    exec ${pkgs.python3}/bin/python3 ${./netsec/inspect.py} ${settings} --policy-only
  '') ];
  systemd.services.clash-verge.serviceConfig = { MemoryAccounting = true; };
  systemd.services.gost-relay.serviceConfig = { MemoryAccounting = true; };
}

