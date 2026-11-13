{ pkgs, config, ... }:

let
  statusPython = pkgs.python3.withPackages (p: [ p.pyyaml ]);
  settings = pkgs.writeText "proxy-inspect.json" (builtins.toJSON {
    state_tool = "${../proxy/state.py}";
    profiles = "${config.users.users.${config.my.machine.desktopUser}.home}/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles.yaml";
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
    exec ${statusPython}/bin/python3 ${../../../../home/config/clash/status.py} ${../proxy/state.py} \
      --profiles ${config.users.users.${config.my.machine.desktopUser}.home}/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles.yaml \
      --max-age ${toString config.my.proxy.stateMaxAgeSeconds} \
      --verification-max-age ${toString config.my.proxy.verificationMaxAgeSeconds} ${if config.my.proxy.proxyOnly then "--proxy-only" else ""} ${if config.my.proxy.controlledSources then "--controlled-source" else ""} "$@"
  '') (pkgs.writeShellScriptBin "proxy-inspect" ''
    exec ${statusPython}/bin/python3 ${./inspect.py} ${settings} "$@"
  '') (pkgs.writeShellScriptBin "proxy-policy" ''
    exec ${statusPython}/bin/python3 ${./inspect.py} ${settings} --policy-only
  '') (pkgs.writeShellScriptBin "proxy-subscription" ''
    exec ${statusPython}/bin/python3 ${./subscription_check.py} \
      ${config.users.users.${config.my.machine.desktopUser}.home}/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles.yaml \
      --proxy http://127.0.0.1:${toString config.my.machine.ports.mihomoMixed} "$@"
  '') ];
  systemd.services.clash-verge.serviceConfig = { MemoryAccounting = true; };
  systemd.services.gost-relay.serviceConfig = { MemoryAccounting = true; };
}

