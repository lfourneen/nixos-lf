{ config, lib, ... }:

let
  m = config.my.machine;
  p = config.my.proxy;
  privateAddresses = import ../lib/private-addresses.nix { inherit config lib; };

  routeExclude = lib.optionals config.my.hardening.directEgress.privateRoutes
    (m.privateV4 ++ m.ulaV6 ++ m.linkLocalV6)
    ++ m.multicastV4 ++ [ "${m.limitedBroadcastV4}/32" ] ++ m.multicastV6
    ++ lib.optionals p.coreNetns (let t = p.coreTopology; in [ t.subnet4 t.subnet6 ]);

  emptyTunFields = [ "route-address" "route-address-set" "route-exclude-address-set"
    "include-interface" "exclude-interface" "include-uid" "include-uid-range"
    "exclude-uid" "exclude-uid-range" "include-mac-address" "exclude-mac-address"
    "exclude-src-port" "exclude-src-port-range" "exclude-dst-port" "exclude-dst-port-range"
    "inet4-route-address" "inet6-route-address" "inet4-route-exclude-address"
    "inet6-route-exclude-address" "loopback-address" ];

  pow2 = n: lib.foldl' (v: _: v * 2) 1 (lib.range 1 n);

  cidr4 = value: let
    pair = lib.splitString "/" value;
    bits = lib.toInt (builtins.elemAt pair 1);
    ip = lib.foldl' (v: n: v * 256 + lib.toInt n) 0 (lib.splitString "." (builtins.head pair));
    size = pow2 (32 - bits);
    first = builtins.bitAnd ip (4294967296 - size);
  in { inherit first; last = first + size - 1; };
  fake = if p.fakeIpNet == "invalid" then { first = 0; last = -1; } else cidr4 p.fakeIpRange;

  reserved4 = [ "0.0.0.0/8" "127.0.0.0/8" "169.254.0.0/16" "224.0.0.0/4" "240.0.0.0/4" ]
    ++ m.privateV4 ++ [ m.vmSubnet m.waydroidSubnet ];

  encryptedDns = {
      enable = true;
      listen = "127.0.0.1:${toString m.ports.mihomoDns}";
      ipv6 = config.networking.enableIPv6;
      enhanced-mode = if p.tunMode then "fake-ip" else "redir-host";
      fake-ip-range = p.fakeIpRange;
      fake-ip-range6 = "";
      fake-ip-filter-mode = "blacklist";

      fake-ip-filter = [
        # Suffixes that only make sense on a local network: never fake-ip them,
        # so they resolve through whatever local resolver owns the name.
        "*.lan" "*.local" "*.localhost" "*.test" "*.home.arpa" "*.internal"
        "*.wlan" "*.intranet" "*.private" "*.domain" "*.home" "*.host" "*.corp"
        "+.ts.net"

        # QQ file downloads need real DNS answers for Hermes' SSRF check.
        # This exact-host filter changes DNS only; routing stays rule-driven.
        "grouptalk.c2c.qq.com"
        "localhost.ptlogin2.qq.com" "+.stun.*.*" "+.stun.*.*.*" "+.stun.*.*.*.*"
        "lens.l.google.com" "+.srv.nintendo.net" "+.stun.playstation.net" "+.xboxlive.com"
        "+.msftncsi.com" "+.msftconnecttest.com" "capture.apple.com"
        "connectivitycheck.gstatic.com" "connectivitycheck.android.com"
      ] ++ lib.optionals (config.my.hardening.directEgress.timeServerCidrs != [ ])
        (m.ntsUpstreams ++ config.my.hardening.directEgress.timeServerNames);

      use-hosts = true;
      respect-rules = true;
      default-nameserver = [ "tls://${lib.head m.mihomoDomesticDnsUpstreams}" "tls://119.29.29.29" ];
      proxy-server-nameserver = [ "https://${lib.head m.mihomoDomesticDnsUpstreams}/dns-query" ];
      proxy-server-nameserver-policy = { };
      direct-nameserver = [ ];
      direct-nameserver-follow-policy = false;
      nameserver = [ "https://1.1.1.1/dns-query" "https://8.8.8.8/dns-query" ];

      nameserver-policy = {
        "geosite:cn" = map (ip: "https://${ip}/dns-query") m.mihomoDomesticDnsUpstreams;
        "geosite:geolocation-!cn" = [ "https://1.1.1.1/dns-query" "https://8.8.8.8/dns-query" ];
      } // lib.genAttrs [ "+.google.com" "+.googleapis.com" "+.googleapis.cn" "+.googlevideo.com"
        "+.gstatic.com" "+.youtube.com" "+.youtu.be" "+.facebook.com" "+.twitter.com"
        "+.x.com" "+.github.com" "+.githubusercontent.com" "+.openai.com" "+.chatgpt.com"
        "+.anthropic.com" ] (_: [ "https://1.1.1.1/dns-query" ])
        // lib.optionalAttrs config.my.hardening.tailscale.magicDns {
          "+.ts.net" = [ "udp://${m.magicDns}:53#DIRECT" ];
        };
    };

in
{
  options.my.proxy.managedDnsTransport = lib.mkOption {
    type = lib.types.attrs;
    internal = true;
    readOnly = true;
    default = {
      dns = encryptedDns // {
        listen = "127.0.0.1:${toString m.ports.coreDnsTransport}";
        enhanced-mode = "normal";
        use-hosts = false;
        use-system-hosts = false;
        nameserver = map (url: url + lib.optionalString p.controlledSources
          (if p.proxyOnly then "#lf-remote" else "#lf-dns"))
          encryptedDns.nameserver;

        # The validator needs raw DNSSEC material and a domestic resolver's
        # view of domestic names. 360's DoH endpoints provide both (the
        # non-validating AliDNS path strips RRSIGs), and #DIRECT keeps the
        # authoritative server from seeing the proxy's address. Foreign names
        # keep the remote-proxied Cloudflare/Google upstreams.
        nameserver-policy = {
          "geosite:cn" = map (ip: "https://${ip}/dns-query#DIRECT")
            m.mihomoDomesticValidatingDnsUpstreams;
        };
      };
      bootstrap-listen = "127.0.0.1:${toString m.ports.coreDnsBootstrapTransport}";
    };
  };

  options.my.proxy.managedDns = lib.mkOption {
    type = lib.types.attrs;
    internal = true;
    readOnly = true;
    default = encryptedDns // lib.optionalAttrs p.localDnssec {
      use-hosts = false;

      # Two local validators have separate request lists, descriptors and caches.
      # Endpoint bootstrap must not queue behind application DNS.
      default-nameserver = [ "tcp://127.0.0.1:${toString m.ports.socketGuestDns}#DIRECT" ];
      proxy-server-nameserver = [ "tcp://127.0.0.1:${toString m.ports.socketGuestDns}#DIRECT" ];
      nameserver = [ "tcp://127.0.0.1:${toString m.ports.unbound}#DIRECT" ];
      nameserver-policy = lib.optionalAttrs config.my.hardening.tailscale.magicDns {
        "+.ts.net" = [ "udp://${m.magicDns}:53#DIRECT" ];
      };

      use-system-hosts = false;
    };
  };

  options.my.proxy.managedProfile = lib.mkOption {
    type = lib.types.attrs;
    internal = true;
    readOnly = true;
    default = {
      lf-dns-transport = if p.localDnssec then p.managedDnsTransport else null;
      lf-private-targets = {
        previous-dns = lib.optionalAttrs p.localDnssec (encryptedDns // { use-system-hosts = true; });
        local-dnssec = if p.localDnssec then "127.0.0.1:${toString m.ports.unbound}" else "";
        validator-ports = lib.optionals p.localDnssec [ m.ports.unbound m.ports.socketGuestDns
          m.ports.coreDnsTransport m.ports.coreDnsBootstrapTransport ]
          ++ lib.optional p.runtimeRouting m.ports.coreRealDns;
        proxy-only = p.proxyOnly;
        reject-plaintext-credentials = p.controlledSources;
        strict-inlets = lib.optional p.strictBrowser "lf-strict";
        subscription-trust = p.subscriptionTrust;
        insecure-proxy-exceptions = p.insecureProxyExceptions;
        allow-dynamic-remote-grants = p.allowDynamicRemoteGrants;
        blocked-domains-file = if p.dnsBlocklistFile == null then "" else "/etc/netsec/dns-blocklist.txt";
        blocked-domains = map lib.toLower p.blockedDomains;
        blocked-domain-exceptions = map lib.toLower p.blockedDomainExceptions;
        prefixes = privateAddresses.ipv4 ++ privateAddresses.ipv6;
        allowed-domains = p.privateTargetDomains;
        local-target-cidrs = p.localTargetCidrs;
        remote-target-grants = p.remoteTargetGrants;
        nat64-prefixes = p.nat64Prefixes;
        fake-ip-prefix = p.fakeIpNet;
        magic-dns-suffix-file = if config.my.hardening.tailscale.magicDns then "/run/netsec-tailnet/domain" else "";
      };

      listeners = lib.optional p.strictBrowser {
        name = "lf-strict"; type = "mixed"; listen = "127.0.0.1";
        port = m.ports.mihomoStrict; udp = false;
      };

      tunnels = [ ];
      external-controller = "";
      external-controller-tls = "";
      external-controller-pipe = "";
      external-doh-server = "";
      ss-config = "";
      vmess-config = "";
      "tuic-server.enable" = false;
      "iptables.enable" = false;
      "geo-auto-update" = false;

      # Providers can smuggle a catch-all rule past the top-level rule check;
      # the managed profile pins the map empty until a reviewed provider policy
      # exists. Rulesets that need providers must be added here explicitly.
      "rule-providers" = { };
      mixed-port = m.ports.mihomoMixed;
      port = 0;
      socks-port = 0;
      redir-port = 0;
      tproxy-port = if p.transparentGuestInterfaces == [ ] then 0 else m.ports.mihomoTproxy;
      allow-lan = false;

      # Keep the plain proxy ports on loopback for allow-lan=false, but let the
      # optional TPROXY listener accept transparent traffic addressed to the
      # original destination. Without this the tproxy socket binds 127.0.0.1
      # and the kernel cannot deliver guest packets to it.
      bind-address = "*";
      ipv6 = config.networking.enableIPv6;
      routing-mark = m.mihomoMark;
      "tcp-concurrent" = true;
      "tun.enable" = p.tunMode;
      "tun.file-descriptor" = 0;
      "dns.fallback" = [ ];

      # ECH's outer SNI is a cover name in both Rule and uniform proxy modes.
      # Fake-IP mappings and explicit proxy targets already supply the domain.
      "sniffer.enable" = false;
    } // lib.optionalAttrs p.controlledSources {
      "geodata-mode" = true;
      "profile.store-selected" = true;
    }
      // lib.optionalAttrs p.coreNetns { "interface-name" = p.coreTopology.peer; }
      // lib.optionalAttrs p.runtimeRouting { "profile.store-fake-ip" = true; }
      // lib.mapAttrs' (key: value: lib.nameValuePair "dns.${key}" value) p.managedDns
      // lib.genAttrs (map (key: "tun.${key}") emptyTunFields) (_: [ ])
      // lib.optionalAttrs p.tunMode (lib.mapAttrs' (key: value:
        lib.nameValuePair "tun.${key}" value) p.tunRouting // {
        "tun.device" = p.tunDev;
        "tun.stack" = p.tunStack;
        "tun.mtu" = p.tunMtu;
        "tun.auto-route" = true;
        "tun.auto-redirect" = true;
        "tun.strict-route" = true;
        "tun.iproute2-table-index" = p.routeTable;
        "tun.route-exclude-address" = routeExclude;
        "tun.inet6-address" = lib.optionals config.networking.enableIPv6 [ p.tunIpv6Address ];
        "tun.dns-hijack" = [ "any:53" "tcp://any:53" ];

        # Require DNS interception during profile parsing and controller PATCH.
        "tun.dns-mode" = "hijack";
      }) // lib.optionalAttrs (p.localDnssec && p.tunMode) {
        "tun.exclude-uid" = [ m.uids.unbound ];
      };
  };

  config.assertions = [
    {
      assertion = !p.localDnssec || (p.coreNetns && p.dnsEncryption == "strict");
      message = "Local B DNSSEC requires the managed core namespace and strict DNS encryption.";
    }
    {
      assertion = lib.all (value: let other = cidr4 value;
        in fake.last < other.first || other.last < fake.first) reserved4;
      message = "Fake-IP must not overlap real LAN, guest, tailnet, loopback or reserved IPv4 ranges.";
    }

    {
      assertion = builtins.bitAnd m.mihomoMark 16711680 == 0
        && !(builtins.elem m.mihomoMark [ 563 p.tunRouting.auto-redirect-input-mark p.tunRouting.auto-redirect-output-mark ])
        && p.tunRouting.auto-redirect-input-mark != p.tunRouting.auto-redirect-output-mark
        && lib.all (mark: mark > 0 && mark <= 4294967295 && mark != 563
          && builtins.bitAnd mark 16711680 == 0)
          [ p.tunRouting.auto-redirect-input-mark p.tunRouting.auto-redirect-output-mark ];
      message = "The core mark must not collide with TPROXY, sing-tun or Tailscale reserved marks.";
    }

    {
      assertion = p.tunRouting.iproute2-rule-index > 5270
        && p.tunRouting.iproute2-rule-index + 10 < 32766
        && p.tunRouting.auto-redirect-iproute2-fallback-rule-index > 32767
        && p.tunRouting.auto-redirect-iproute2-fallback-rule-index <= 4294967295;
      message = "Managed TUN rules must follow Tailscale, precede main/default, and reserve the fallback after them.";
    }

    {
      assertion = builtins.match "[a-zA-Z0-9][a-zA-Z0-9.-]*[a-zA-Z0-9]" p.tunProbeName != null;
      message = "The TUN probe must be a valid hostname.";
    }

    {
      assertion = m.vmBridge != m.waydroidBridge && m.wired.name != m.wireless.name;
      message = "Guest bridges and physical uplinks must have distinct roles.";
    }
  ];
}
