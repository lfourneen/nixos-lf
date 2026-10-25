{ lib, osConfig, ... }:

let
  # Single source of truth in system/config/network.nix (my.proxy.tunMode).
  tunMode = osConfig.my.proxy.tunMode;

  # Same single source as the firewall rules; the template must pin the same
  # device name or TUN traffic stops matching them.
  tunDev = osConfig.my.proxy.tunDev;

  # Fake-ip pool, pinned from the system option so the template and
  # nftables-verify cannot drift.
  fakeIpRange = osConfig.my.proxy.fakeIpRange;

  m = osConfig.my.machine;
  port = m.ports;
  # Portal/LAN/CGNAT bypass the TUN; same ranges as the firewall.
  routeExclude = lib.optionals osConfig.my.hardening.allowPrivateEgress (m.privateV4 ++ m.ulaV6 ++ m.linkLocalV6)
    ++ m.multicastV4 ++ [ "${m.limitedBroadcastV4}/32" ] ++ m.multicastV6;
  # No trailing newline: the interpolation line supplies it.
  yamlItems = indent: xs: lib.concatStringsSep "\n" (map (x: "${indent}- ${x}") xs);

  # Always-resolve names (never handed a fake IP). The TUN route-exclude covers
  # ranges, not names, so this is what keeps an ISP portal, LAN host or captive
  # probe working by name. It replaces the subscription's list (merge wins by
  # key), so it must stay complete on its own.
  fakeIpFilter = osConfig.my.proxy.managedDns.fake-ip-filter;

  # IP-check / leak-test endpoints that must not egress DIRECT (a CN probe then
  # reports the real home IP as a "backup exit"). Suffixes cover subdomains.
  probeDomains = [
    "ip.cn" "ip138.com" "cip.cc" "ipip.net" "ip.sb" "ipw.cn" "ip125.com"
    "ip111.cn" "ip.3322.net" "ip.skk.moe" "ping0.cc" "ippure.com"
    "iplark.com" "ipcheck.ing" "ip.gs" "ipaddress.com" "ip.me" "ipinfo.io"
    "ip-api.com" "ipapi.co" "ipapi.is" "ipify.org" "icanhazip.com"
    "ifconfig.me" "ifconfig.co" "ident.me" "seeip.org" "checkip.amazonaws.com"
    "checkip.dyndns.org" "test-ipv6.com" "whatismyipaddress.com"
    "whatismyip.com" "myip.com" "ipchicken.com" "2ip.io" "whoer.net"
    "browserleaks.com" "ipleak.net" "dnsleaktest.com" "dnsleak.com"
    "dnschecker.org" "ip2location.com" "dns-detect.alicdn.com" "itdog.cn"
    "17ce.com" "boce.com" "tool.lu" "ping.pe"
    # Cross-network CDN/trace targets such as ip.net.coffee's "domestic" probes;
    # a CN-fronted /cdn-cgi/trace here reports the real v4 and v6 on the Direct path.
    "cloudflare-cn.com" "qualcomm.cn" "perfops.byte-test.com"
    # Common CN IP-echo endpoints.
    "chinaz.com" "pv.sohu.com"
    # NetEase CDN: a HEAD replies with the cdn-user-ip header (real IP on Direct).
    "necaptcha.nosdn.127.net"
  ];
  # Prepend-forced through the proxy, the subscription sends these DIRECT which times out
  healthProbeHosts = lib.filter (host: host != null) (map (url:
    let parts = builtins.match "https?://([a-zA-Z0-9.-]+)(:[0-9]+)?(/.*)?" url;
    in if parts == null then null else builtins.head parts
  ) osConfig.my.proxy.nodeProbeUrls);
  forcedProxyDomains = lib.unique (probeDomains ++ healthProbeHosts ++ [
    "dl.google.com" 
    "dl-ssl.google.com" 
    "dl.googleusercontent.com"
  ]);
  forcedProxyDomainsJs =
    "[ " + lib.concatStringsSep ", " (map (d: "\"${d}\"") forcedProxyDomains) + " ]";

  # The TUN self-test name must get a fake-ip answer, so it must not be matched
  # by fakeIpFilter; a routine edit that adds e.g. "+.baidu.com" would otherwise
  # silently break the health gate's TUN layer and pin the host in Mode C.
  tunProbeName = osConfig.my.proxy.tunProbeName;
  suffixOf = p: lib.removePrefix "+." (lib.removePrefix "*." (lib.removePrefix "." p));
  probeFiltered = lib.any (p:
    let s = suffixOf p;
    in p == tunProbeName || (s != p && (tunProbeName == s || lib.hasSuffix ".${s}" tunProbeName))
  ) fakeIpFilter;

in
{
  assertions = [
    {
      assertion = !probeFiltered;
      message = ''
        cvr-merge.nix: my.proxy.tunProbeName (${tunProbeName}) is matched by a
        fake-ip-filter entry, so it would resolve to a real IP and proxy-mode's
        TUN self-test would fail, pinning the host in Mode C. Remove that filter
        entry, or change my.proxy.tunProbeName.
      '';
    }
  ];

  home.file.".local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/Merge.yaml" = {
    text = ''
      # Profile Enhancement Merge Template for Clash Verge

      profile:
        store-selected: true
        # Persist the fake-ip <-> domain mapping across a core restart, so a
        # restarted core still knows the addresses it handed out instead of
        # losing them (the affected flows are non-TCP/QUIC, which sniffer also
        # helps; audit MEDIUM-5).
        store-fake-ip: true

      # Fix the mixed port to align with the probe and forwarding ports in gost-relay.nix.
      mixed-port: ${toString port.mihomoMixed}
      port: 0
      socks-port: 0
      redir-port: 0
      listeners: []
      tunnels: []
      ss-config: ""
      vmess-config: ""
      tuic-server: { enable: false }
      iptables: { enable: false }

      # Pin the routing mode and IPv6. The GUI owns both keys, and `direct` would
      # be turned into Mode C by the health gate; a rebuild must not change either
      # silently. (The GUI still injects `secret`/ports after the merge, so a pin
      # here is defence in depth, not a guarantee.)
      mode: rule
      ipv6: ${lib.boolToString osConfig.networking.enableIPv6}

      # Pin the log level: proxy-mode's health gate parses the core's connection
      # log to confirm a probe was proxied (not DIRECT) and to learn the group it
      # used. The GUI owns this key otherwise, and warning/silent would silently
      # disable that layer of the gate.
      log-level: info

      # Pin the mark the core sets on its own outbound sockets; the nft kill switch
      # exempts the core by this mark rather than by uid 0. Must match
      # my.machine.mihomoMark (nftables-verify asserts it at runtime).
      routing-mark: ${toString m.mihomoMark}

      # allow-lan:false keeps the plain proxy ports on loopback. bind-address
      # must stay "*" so the optional TPROXY listener (vmTransparentProxy) can
      # accept transparent traffic; the controller is loopback via config.yaml.
      allow-lan: false
      bind-address: "*"

      # TPROXY inbound for the optional VM transparent proxy (vmTransparentProxy).
      tproxy-port: ${toString (if osConfig.my.proxy.transparentGuestInterfaces == [ ] then 0 else port.mihomoTproxy)}
      # `enable` must always be listed here: the GUI owns tun.enable, so without
      # this key a rebuild with tunMode = false leaves the TUN up with an empty
      # exclusion list. This template wins per key, and nftables-verify reports the
      # case where Verge injects the key after the merge instead.
      tun:
        enable: ${lib.boolToString tunMode}
        route-address: []
        route-address-set: []
        route-exclude-address-set: []
        include-interface: []
        exclude-interface: []
        include-uid: []
        exclude-uid: []
        include-uid-range: []
        exclude-uid-range: []
      ${lib.optionalString tunMode ''
      # TUN: stack/dns-hijack/strict-route are authoritative in the Clash Verge
      # GUI (Stack=Mixed, DNS Hijack=any:53, Strict Route=ON). strict-route is
      # required because auto-route's `from ::/1 iif lo` rule otherwise lets
      # locally-generated IPv6 bypass TUN.
      #
      # auto-redirect needs the sing-tun patch applied in
      # system/programs/clash-verge.nix.
        stack: ${osConfig.my.proxy.tunStack}
        device: ${tunDev}
        auto-route: true
        iproute2-table-index: ${toString osConfig.my.proxy.routeTable}
        auto-redirect: true
        strict-route: true
        dns-hijack:
          - any:53
          - tcp://any:53
        mtu: ${toString osConfig.my.proxy.tunMtu}
      ${lib.optionalString osConfig.networking.enableIPv6 "  inet6-address: [ \"${osConfig.my.proxy.tunIpv6Address}\" ]\n"}
      ${lib.optionalString (osConfig.my.proxy.tunStack == "mips") "  congestion-controller: ${osConfig.my.proxy.tunCongestionControl}\n"}
        # Portal/LAN/CGNAT targets must bypass the TUN, else the ISP login page is
        # unreachable.
        route-exclude-address:
      ${yamlItems "    " routeExclude}
      ''}
      # Foreign DoH (1.1.1.1/8.8.8.8) is blocked when dialed directly, but
      # `respect-rules` sends it through the proxy, so ipleak sees the proxy's
      # resolver instead of the local one. CN names stay on domestic DoH.
      dns: ${builtins.toJSON (osConfig.my.proxy.managedDns // {
        enhanced-mode = if tunMode then "fake-ip" else "redir-host";
        fake-ip-range = fakeIpRange;
      })}

      # Sniffing still matters with fake-ip: it recovers the domain for flows
      # that arrive as a bare address (filtered/LAN names, pure-IP traffic), and
      # it feeds QUIC where the TLS hello is unreadable. force-dns-mapping keeps
      # the sniffed name in sync with the fake-ip mapping so DOMAIN/GEOSITE rules
      # match TUN traffic.
      sniffer:
        enable: true
        force-dns-mapping: true
        parse-pure-ip: true
        override-destination: true
        sniff:
          HTTP:
            ports: [ 80, 8080-8880 ]
          TLS:
            ports: [ 443, 8443 ]
          QUIC:
            ports: [ 443, 8443 ]

      # Manual extra proxy rules (order beats GEOIP,CN,DIRECT); the probe domains
      # are added by Script.js instead, via the subscription's own catch-all group.
      prepend-rules: []

      # Keep the TCP controller off and never ship the default secret; replace the
      # placeholder with `openssl rand -hex 16`. Verge may override both keys.
      external-controller: ""
      external-controller-tls: ""
      external-controller-pipe: ""
      external-doh-server: ""
      # The GUI owns its local Unix controller; no reusable secret is shipped.
    '';
    force = true;
  };

  # Global extension script (after Merge, before per-profile extensions):
  # (1) keep only the dns keys the Merge template declares (DNS_KEYS must match
  # that block); (2) prepend forcedProxyDomains via the catch-all group.
  home.file.".local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/Script.js" = {
    text = ''
      // Managed by home/config/cvr-merge.nix -- do not edit in the GUI.
      const DNS_KEYS = new Set([
        "enable", "listen", "ipv6", "enhanced-mode", "fake-ip-range",
        "fake-ip-filter", "use-hosts", "respect-rules",
        "default-nameserver", "proxy-server-nameserver", "nameserver",
        "nameserver-policy",
      ]);
      const GEOSITE_KEYS = new Set(["geosite:cn", "geosite:geolocation-!cn"]);
      const FORCED_PROXY_DOMAINS = ${forcedProxyDomainsJs};

      // Catch-all group name differs per subscription, so take it from the
      // final MATCH rule instead of hardcoding (a stale name fails the config).
      function catchAllPolicy(rules) {
        for (let i = rules.length - 1; i >= 0; i--) {
          const r = rules[i];
          if (typeof r === "string" && r.slice(0, 6) === "MATCH,") {
            return r.slice(6).trim();
          }
        }
        return null;
      }

      function main(config) {
        // Replace the DNS object, including nested policy entries. Per-profile
        // extensions still run later; the core's final parser rejects any drift.
        if (config) config.dns = ${builtins.toJSON (osConfig.my.proxy.managedDns // {
          enhanced-mode = if tunMode then "fake-ip" else "redir-host";
          fake-ip-range = fakeIpRange;
        })};

        const rules = config && config.rules;
        if (Array.isArray(rules) && rules.length) {
          const policy = catchAllPolicy(rules);
          if (policy && policy !== "DIRECT" && policy !== "REJECT") {
            const inject = [];
            for (const d of FORCED_PROXY_DOMAINS) {
              const rule = "DOMAIN-SUFFIX," + d + "," + policy;
              if (rules.indexOf(rule) === -1) inject.push(rule);
            }
            if (inject.length) config.rules = inject.concat(rules);
          }
        }
        return config;
      }
    '';
    force = true;
  };
}

