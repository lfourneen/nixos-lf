{ config, lib, pkgs, ... }:

let
  p = config.my.proxy;
  m = config.my.machine;
  python = pkgs.python3.withPackages (packages: [ packages.pyyaml ]);
  remoteDns = map (url: url + "#lf-remote") p.proxyOnlyDnsUpstreams;

  strictPolicy = p.managedProfile // {
    lf-private-targets = p.managedProfile.lf-private-targets // {
      proxy-only = true;
      local-target-cidrs = [ ];
      allowed-domains = [ ];
      remote-target-grants = [ ];
      allow-dynamic-remote-grants = false;
      magic-dns-suffix-file = "";
    };

    lf-dns-transport = p.managedDnsTransport // {
      dns = p.managedDnsTransport.dns // {
        nameserver = remoteDns;
        nameserver-policy = { };
        proxy-server-nameserver = remoteDns;
        proxy-server-nameserver-policy = { };
        default-nameserver = remoteDns;
      };
    };
    "dns.nameserver-policy" = { };
  };

  policies = {
    rules = pkgs.writeText "routing-rules-policy.json" (builtins.toJSON p.managedProfile);
    proxy-only = pkgs.writeText "routing-proxy-only-policy.json" (builtins.toJSON strictPolicy);
  };

  auditSettings = mode: pkgs.writeText "routing-${mode}-audit.json" (builtins.toJSON {
    strict_semantics = true;
    insecure_exceptions = p.insecureProxyExceptions;
    subscription_trust = p.subscriptionTrust;
    sensitive_domains = p.sensitiveDomains;
    allowed_hosts = p.subscriptionAuditAllowedHosts;
    allowed_server_cidrs = p.subscriptionAuditServerCidrs;
    protected_routes = if mode == "proxy-only"
      then lib.mapAttrs (_: _: "proxy") p.subscriptionAuditProtectedRoutes
      else p.subscriptionAuditProtectedRoutes;
  });

  artifacts = lib.genAttrs [ "rules" "proxy-only" ] (mode: pkgs.linkFarm "routing-${mode}" [
    { name = "policy.json"; path = policies.${mode}; }
    { name = "health.json"; path = p.routingHealthSettings.${mode}; }
    { name = "audit.json"; path = auditSettings mode; }
  ]);

  settings = pkgs.writeText "proxy-routing.json" (builtins.toJSON (if p.runtimeRouting then {
    state = "/run/proxy-mode";
    runtime = "/run/clash-verge-rev";
    inherit policies artifacts;
    enabled = p.runtimeRouting;
    stage_settings = config.programs.clash-verge.package.stageSettings;
    source_settings = p.sourceSettings;
    stage_tool = ../../../programs/clash/service/clash-verge-stage.py;
    authorization_tool = ./authorization.py;
    source_tool = ../../../programs/clash/service/source_broker.py;
    rules_tool = ../firewall/rules.py;
    state_tool = ./state.py;
    nft = "${pkgs.nftables}/bin/nft";
    topology = p.coreTopology;
    interface = p.tunDev;
    uplinks = [ m.wired.name m.wireless.name ];
    guests = map (name: { interface = name; sources = config.my.hardening.guestNetworks.${name}.sourceCidrs; }) p.transparentGuestInterfaces;
    guest_interfaces = p.guestInterfaces;
    source_uid = m.uids.clashFetch;
    dhcp_uid = m.uids.dhcpcd;
    dnsmasq_uid = config.users.users.dnsmasq.uid;
    real_dns_port = m.ports.coreRealDns;
    validator_port = m.ports.unbound;
    ipv6 = config.networking.enableIPv6;
    observer = p.coreTopology.observer;
  } else { enabled = false; }));

  command = pkgs.writeShellScript "proxy-routing" ''
    exec ${python}/bin/python3 -I ${./routing.py} ${settings} "$@"
  '';

in
{
  options.my.proxy = {
    runtimeRouting = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Enable root-controlled rules/proxy-only transactions with registered sources, local DNSSEC and kernel authorization expiry.";
    };

    proxyOnlyDnsUpstreams = lib.mkOption {
      type = lib.types.nonEmptyListOf lib.types.str;
      default = [ "https://1.1.1.1/dns-query" "https://8.8.8.8/dns-query" ];
      description = "Literal-address authenticated DoH endpoints for proxy-only application and infrastructure DNS. Transport always uses the managed remote group.";
    };

    activeManagedPolicy = lib.mkOption { type = lib.types.str; internal = true; };
    routingCommand = lib.mkOption { type = lib.types.path; internal = true; };
    routingSettings = lib.mkOption { type = lib.types.path; internal = true; };
    routingPolicies = lib.mkOption { type = lib.types.attrsOf lib.types.path; internal = true; };
    routingPolicyValues = lib.mkOption { type = lib.types.attrs; internal = true; };
    routingHealthSettings = lib.mkOption { type = lib.types.attrsOf lib.types.path; internal = true; };
    sourceSettings = lib.mkOption { type = lib.types.path; internal = true; };
  };

  config = {
    my.proxy.activeManagedPolicy = if p.runtimeRouting then "/run/proxy-mode/routing-active/policy.json"
      else toString (pkgs.writeText "mihomo-managed-policy.json" (builtins.toJSON p.managedProfile));
    my.proxy.routingCommand = command;
    my.proxy.routingSettings = settings;
    my.proxy.routingPolicies = policies;
    my.proxy.routingPolicyValues = { rules = p.managedProfile; proxy-only = strictPolicy; };

    assertions = lib.optional p.runtimeRouting {
      assertion = p.controlledSources && p.localDnssec && p.coreNetns && p.tunMode
        && p.dnsEncryption == "strict" && !p.proxyOnly && p.authorizationLeaseSeconds != null
        && lib.all (url: builtins.match "https://([[][0-9a-fA-F:]+[]]|[0-9.]+)(:[0-9]+)?/[^#[:space:]]*" url != null) p.proxyOnlyDnsUpstreams;
      message = "Runtime routing requires controlled sources, local DNSSEC, strict encrypted DNS, isolated TUN, kernel leases and proxyOnly=false for the rules boot default.";
    };

    systemd.services.proxy-mode.preStart = lib.mkIf p.runtimeRouting ''${command} initialize'';

    systemd.services.proxy-real-dns = lib.mkIf p.runtimeRouting {
      description = "Validated real-address DNS through the selected application path";
      wantedBy = [ "multi-user.target" ];
      after = [ "proxy-mode.service" ];
      serviceConfig = {
        ExecStart = "${pkgs.python3}/bin/python3 -I ${../dns/real_proxy.py} ${settings}";
        User = "dnsmasq";
        Group = "dnsmasq";
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
        MemoryMax = "128M";
        TasksMax = 48;
        Restart = "on-failure";
        RestartSec = 2;
      };
    };

    systemd.services.clash-real-dns = lib.mkIf p.runtimeRouting {
      description = "Fixed local DNSSEC validator bridge in the core namespace";
      requires = [ "clash-core-network.service" ];
      after = [ "clash-core-network.service" ];
      bindsTo = [ "clash-core-network.service" ];
      serviceConfig = {
        ExecStart = "${pkgs.python3}/bin/python3 -I ${../dns/real_proxy.py} ${settings} --backend";
        NetworkNamespacePath = p.coreTopology.namespacePath;
        Group = "dnsmasq";
        RuntimeDirectory = "clash-real-dns";
        RuntimeDirectoryMode = "0750";
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" ];
        MemoryMax = "128M";
        TasksMax = 32;
        Restart = "on-failure";
        RestartSec = 2;
      };
    };

    systemd.services.clash-verge.requires = lib.mkIf p.runtimeRouting [ "clash-real-dns.service" ];
    systemd.services.clash-verge.after = lib.mkIf p.runtimeRouting [ "clash-real-dns.service" ];
  };
}
