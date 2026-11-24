{ pkgs, config, lib, ... }:

let
  machine = config.my.machine;
  proxy = config.my.proxy;
  desktopUid = config.users.users.${machine.desktopUser}.uid;
  proxyEnv = import ../proxy/env.nix { inherit lib; };

  environmentKeys = proxyEnv.trustOverrideNames;

  systemdEnvironment = unit:
    lib.mapAttrs (_: value: builtins.hashString "sha256" (toString value))
      (lib.filterAttrs (key: _: builtins.elem key environmentKeys) config.systemd.services.${unit}.environment);
  units = lib.filter (unit: builtins.hasAttr unit config.systemd.services) [
    "hermes-agent" "hermes-backend" "gost-relay"
  ];

  socketUnit = name: let socket = config.systemd.sockets.${name}.socketConfig; in {
    path = socket.ListenStream;
    user = socket.SocketUser or "root";
    group = socket.SocketGroup or null;
    mode = socket.SocketMode;
  };

  listener = service: user: protocol: address: port: {
    inherit service user protocol address port;
  };

  dualWildcard = service: user: protocol: port:
    map (address: listener service user protocol address port) [ "0.0.0.0" "::" ];

in
pkgs.writeText "proxy-inspect.json" (builtins.toJSON {
  state_tool = "${../proxy/state.py}";
  profiles = "${config.users.users.${config.my.machine.desktopUser}.home}/.local/share/io.github.clash-verge-rev.clash-verge-rev/profiles.yaml";
  interfaces = [ config.my.machine.wired.name config.my.machine.wireless.name config.my.proxy.tunDev ];
  ip = "${pkgs.iproute2}/bin/ip";
  tc = "${pkgs.iproute2}/bin/tc";
  ethtool = "${pkgs.ethtool}/bin/ethtool";
  nft = "${pkgs.nftables}/bin/nft";
  systemctl = "${pkgs.systemd}/bin/systemctl";

  posture = {
    script = ./posture.py;
    listenerModule = ../proxy/listeners.py;
    listenerSettings = proxy.listenerSettings;
    ingress = config.my.hardening.hostIngress;
    caBundle = config.security.pki.caBundle;
    inherit desktopUid environmentKeys;
    environmentUnits = lib.genAttrs units systemdEnvironment;
    tailscale = if config.services.tailscale.enable then lib.getExe config.services.tailscale.package else null;
    dconf = "${pkgs.dconf}/bin/dconf";
    declarations =
      lib.optionals (config.my.hardening.tailscale.ingressPort != null)
        (dualWildcard "tailscaled" "root" "udp" config.my.hardening.tailscale.ingressPort)
      ++ lib.optionals config.services.avahi.enable (dualWildcard "avahi-daemon" "avahi" "udp" 5353)
      ++ lib.optionals config.services.sunshine.enable
        (dualWildcard "sunshine" machine.desktopUser "tcp" (lib.toIntBase10 (toString (config.services.sunshine.settings.port or 47989))))
      ++ lib.optionals (config.my.hardening.socketGuestUsers != [ ] || proxy.controlledSources)
        (map (protocol: (listener "unbound-infrastructure" null protocol "127.0.0.1" machine.ports.unboundInfrastructure)
          // { uids = [ machine.uids.unboundInfrastructure ]; }) [ "tcp" "udp" ]);
    unix = lib.optionalAttrs config.programs.clash-verge.enable {
      core-controller = { path = "/run/clash-verge-rev/core/mihomo.sock"; uid = machine.uids.clashCore; };
      gui-controller = {
        path = "/run/user/${toString desktopUid}/clash-verge-rev/verge-mihomo.sock";
        uid = desktopUid;
        group = "clash-core";
        mode = "0600";
      };
    } // lib.optionalAttrs (config.systemd.sockets ? clash-strict) {
      strict-browser = socketUnit "clash-strict";
    } // lib.optionalAttrs (config.systemd.sockets ? hermes-containers) {
      hermes-containers = socketUnit "hermes-containers";
    };
  };

  policy = {
    runtimeRouting = config.my.proxy.runtimeRouting;
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
})
