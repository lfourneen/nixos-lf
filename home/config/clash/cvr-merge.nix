{ lib, osConfig, ... }:

let
  # Single source of truth in system/config/network/network.nix (my.proxy.tunMode).
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
  routeExclude = lib.optionals osConfig.my.hardening.directEgress.privateRoutes (m.privateV4 ++ m.ulaV6 ++ m.linkLocalV6)
    ++ m.multicastV4 ++ [ "${m.limitedBroadcastV4}/32" ] ++ m.multicastV6;

  # No trailing newline: the interpolation line supplies it.
  yamlItems = indent: xs: lib.concatStringsSep "\n" (map (x: "${indent}- ${x}") xs);

  # Always-resolve names (never handed a fake IP). The TUN route-exclude covers
  # ranges, not names, so this is what keeps an ISP portal, LAN host or captive
  # probe working by name. It replaces the subscription's list (merge wins by
  # key), so it must stay complete on its own.
  fakeIpFilter = osConfig.my.proxy.managedDns.fake-ip-filter;

  # Known IP-echo resources can be embedded by ordinary websites. In split
  # routing these destinations must use the proxy even when GEOIP would select
  # DIRECT. Uniform proxyOnly routing also covers destinations outside the list.
  probeDomains = [
    # IP lookup and geolocation sites.
    "ip.cn" "ip138.com" "cip.cc" "ipip.net" "ipw.cn" "ip125.com"
    "ip.gs" "ipx.al"  "ipaddress.com" "ipinfo.io" "ip2location.com"
    "whatismyipaddress.com" "whatismyip.com" "myip.com" "ipchicken.com" "2ip.io"
    "detectradar.com"

    # IP echo and address-information APIs.
    "ip.sb" "ip.3322.net" "ip.me" "ip-api.com" "ipapi.co" "ipapi.is"
    "ipify.org" "icanhazip.com" "ifconfig.me" "ifconfig.co" "ident.me"
    "seeip.org" "checkip.amazonaws.com" "checkip.dyndns.org" "pv.sohu.com"

    # Split-routing and IP-quality checks, including their own echo APIs.
    "ip111.cn" "ip.skk.moe" "ping0.cc" "ippure.com" "iplark.com" "ipcheck.ing"
    "net.coffee" "ip.cx" "ipbot.net" "ipjiance.org" "ipkk.com" "cdnping.org"

    # Browser privacy, DNS-leak and IPv6 checks.
    "whoer.net" "browserleaks.com" "ipleak.net" "dnsleaktest.com" "dnsleak.com"
    "dns-detect.alicdn.com" "test-ipv6.com"

    # DNS lookup and network-connectivity tools.
    "dnschecker.org" "itdog.cn" "17ce.com" "boce.com" "tool.lu" "ping.pe" "chinaz.com"

    # IP reputation lookup endpoints.
    "dnsbl.dronebl.org"

    # Cross-network CDN/trace targets embedded by split-routing test sites.
    # Direct connections expose the client's address in the trace response.
    "cloudflare-cn.com" "qualcomm.cn" "perfops.byte-test.com"
    "visa.cn" "perfops.cloudflareperf.com"

    # NetEase CDN: a HEAD response includes the client's address in cdn-user-ip.
    "necaptcha.nosdn.127.net"
  ];

  # Prepend-forced through the proxy, the subscription sends these DIRECT which times out
  healthProbeHosts = lib.filter (host: host != null) (map (url:
    let parts = builtins.match "https?://([a-zA-Z0-9.-]+)(:[0-9]+)?(/.*)?" url;
    in if parts == null then null else builtins.head parts
  ) osConfig.my.proxy.nodeProbeUrls);
  forcedProxyDomains = lib.unique (probeDomains ++ healthProbeHosts ++ osConfig.my.proxy.tunProbeNames ++ [
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

  probeFiltered = lib.any (name: lib.any (p:
    let s = suffixOf p;
    in p == name || (s != p && (name == s || lib.hasSuffix ".${s}" name))
  ) fakeIpFilter) osConfig.my.proxy.tunProbeNames;

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
        # Preserve fake-IP domain mappings across core restarts.
        store-fake-ip: true

      # Fix the mixed port to align with the probe and forwarding ports in system/programs/gost/default.nix.
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
      lf-private-targets: ${builtins.toJSON osConfig.my.proxy.managedProfile.lf-private-targets}

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
        file-descriptor: 0
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
      # system/programs/clash/clash-verge.nix.
        stack: ${osConfig.my.proxy.tunStack}
        device: ${tunDev}
        auto-route: true
        iproute2-table-index: ${toString osConfig.my.proxy.routeTable}
        iproute2-rule-index: ${toString osConfig.my.proxy.tunRouting.iproute2-rule-index}
        auto-redirect-input-mark: ${toString osConfig.my.proxy.tunRouting.auto-redirect-input-mark}
        auto-redirect-output-mark: ${toString osConfig.my.proxy.tunRouting.auto-redirect-output-mark}
        auto-redirect-iproute2-fallback-rule-index: ${toString osConfig.my.proxy.tunRouting.auto-redirect-iproute2-fallback-rule-index}
        auto-redirect: true
        strict-route: true
        # Match the managed policy's explicit DNS interception mode.
        dns-mode: hijack
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

      # Preserve the requested/fake-IP target: ECH's outer SNI is a public
      # cover name and cannot safely replace it, including in Rule mode.
      sniffer:
        enable: false
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
  # (1) drop subscription leaves that disable TLS verification unless
  # my.proxy.insecureProxyExceptions names them; the core applies the same
  # exact-name rule to whatever remains; (2) replace DNS with the managed
  # object; (3) prepend forcedProxyDomains via the catch-all group. The core
  # validates later per-profile extensions too, so a leaf re-added by one of
  # them still fails closed at startup.
  home.file.".local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/Script.js" = {
    text = ''
      // Managed by home/config/clash/cvr-merge.nix -- do not edit in the GUI.
      const FORCED_PROXY_DOMAINS = ${forcedProxyDomainsJs};
      const DELAY_TEST_URL = ${builtins.toJSON osConfig.my.proxy.delayTestUrl};
      const PROXY_ONLY = ${lib.boolToString osConfig.my.proxy.proxyOnly};
      const REMOTE_ONLY_CATCH_ALL = ${lib.boolToString osConfig.my.proxy.remoteOnlyCatchAll};
      // Same source of truth as the core's managed policy: the merge filter
      // deletes an insecure leaf only when this list does not name it.
      const INSECURE_PROXY_EXCEPTIONS = ${builtins.toJSON osConfig.my.proxy.insecureProxyExceptions};

      function httpsHealthChecks(config) {
        const insecure = url => url == null || url === "" ||
          (typeof url === "string" && /^http:\/\//i.test(url));
        const groups = config["proxy-groups"];
        if (Array.isArray(groups)) {
          for (const group of groups) {
            if (group && ["url-test", "fallback", "load-balance"].includes(group.type) && insecure(group.url)) {
              group.url = DELAY_TEST_URL;
            }
          }
        }
        const providers = config["proxy-providers"];
        if (providers && typeof providers === "object") {
          for (const provider of Object.values(providers)) {
            const health = provider && provider["health-check"];
            if (health && health.enable === true && insecure(health.url)) health.url = DELAY_TEST_URL;
          }
        }
      }

      // The managed core refuses the whole profile when any leaf disables TLS
      // certificate validation (skip-cert-verify). Drop such leaves and prune
      // the group members so the rest starts, but keep a leaf whose exact name
      // is in my.proxy.insecureProxyExceptions: that same name is the core's
      // explicit exception, so the two layers cannot disagree. A leaf kept
      // here for any other reason still fails closed in the core.
      function insecureValue(value) {
        if (value === false || value === 0) return false;
        if (typeof value === "string") {
          return ["0", "f", "F", "FALSE", "false", "False"].indexOf(value) === -1;
        }
        return true;
      }

      function disablesTLS(value) {
        if (Array.isArray(value)) {
          for (const item of value) {
            if (disablesTLS(item)) return true;
          }
          return false;
        }
        if (value && typeof value === "object") {
          for (const key of Object.keys(value)) {
            if (key.toLowerCase().replace(/_/g, "-") === "skip-cert-verify" && insecureValue(value[key])) {
              return true;
            }
            if (disablesTLS(value[key])) return true;
          }
        }
        return false;
      }

      function dropInsecureProxies(config) {
        const proxies = config.proxies;
        if (!Array.isArray(proxies)) return;
        const dropped = [];
        const kept = [];
        for (const proxy of proxies) {
          if (proxy && typeof proxy === "object" && disablesTLS(proxy) &&
              !(typeof proxy.name === "string" && INSECURE_PROXY_EXCEPTIONS.indexOf(proxy.name) !== -1)) {
            if (typeof proxy.name === "string") dropped.push(proxy.name);
            continue;
          }
          kept.push(proxy);
        }
        if (dropped.length === 0) return;
        config.proxies = kept;

        const groups = config["proxy-groups"];
        if (!Array.isArray(groups)) return;
        const emptied = [];
        for (const group of groups) {
          if (!group || !Array.isArray(group.proxies) || group.proxies.length === 0) continue;
          group.proxies = group.proxies.filter(name => dropped.indexOf(name) === -1);
          if (group.proxies.length === 0 && !(group.use && group.use.length)) emptied.push(group.name);
        }
        if (emptied.length) {
          throw new Error("Every proxy in these groups disables TLS verification: " + emptied.join(", "));
        }
      }

      // Catch-all group name differs per subscription, so take it from the
      // final MATCH rule instead of hardcoding (a stale name fails the config).
      function catchAllPolicy(rules) {
        for (let i = rules.length - 1; i >= 0; i--) {
          const r = rules[i];
          if (typeof r !== "string") continue;
          const parts = r.split(",").map(part => part.trim());
          if (parts.length === 2 && ["MATCH", "FINAL"].includes(parts[0].toUpperCase())) {
            return parts[1];
          }
        }
        return null;
      }

      function constrainCatchAll(config, policy) {
        const nonProxy = new Set(["DIRECT", "COMPATIBLE", "REJECT", "REJECT-DROP", "PASS", "GLOBAL"]);
        const nonProxyTypes = new Set(["direct", "compatible", "reject", "reject-drop", "pass", "dns"]);
        const groups = new Map((config["proxy-groups"] || []).map(group => [group.name, group]));
        const nodes = new Map((config.proxies || []).map(node => [node.name, node]));
        const done = new Set(), active = new Set();
        function visit(name) {
          if (nonProxy.has(name)) return false;
          if (nodes.has(name)) {
            return !nonProxyTypes.has(String(nodes.get(name).type).toLowerCase());
          }
          if (done.has(name)) return true;
          if (active.has(name)) throw new Error("Catch-all proxy group contains a cycle");
          const group = groups.get(name);
          if (!group) throw new Error("Catch-all proxy group has an invalid reference");
          active.add(name);
          if (group.proxies != null) {
            if (!Array.isArray(group.proxies)) throw new Error("Catch-all proxy group has invalid members");
            group.proxies = group.proxies.filter(visit);
          }
          if (!(group.proxies && group.proxies.length) && !(group.use && group.use.length) &&
              !group["include-all"] && !group["include-all-proxies"] && !group["include-all-providers"]) {
            throw new Error("Catch-all proxy group has no remote members");
          }
          active.delete(name);
          done.add(name);
          return true;
        }
        if (!visit(policy)) throw new Error("Catch-all policy must select a remote proxy");
      }

      function main(config) {
        if (config) httpsHealthChecks(config);
        if (config) dropInsecureProxies(config);
        if (config) config["lf-private-targets"] = ${builtins.toJSON osConfig.my.proxy.managedProfile.lf-private-targets};
        // Replace the DNS object, including nested policy entries. Per-profile
        // extensions still run later; the core's final parser rejects any drift.
        if (config) config.dns = ${builtins.toJSON (osConfig.my.proxy.managedDns // {
          enhanced-mode = if tunMode then "fake-ip" else "redir-host";
          fake-ip-range = fakeIpRange;
        })};

        const rules = config && config.rules;
        if (Array.isArray(rules) && rules.length) {
          const policy = catchAllPolicy(rules);
          if (REMOTE_ONLY_CATCH_ALL) {
            if (!policy) throw new Error("Catch-all proxy policy is missing");
            constrainCatchAll(config, policy);
          }
          if (PROXY_ONLY) {
            if (!policy || ["DIRECT", "COMPATIBLE", "REJECT", "REJECT-DROP", "PASS"].includes(policy)) {
              throw new Error("Strict routing needs a proxy catch-all policy");
            }
            // All destinations receive the same policy. The final core DIRECT
            // guard remains authoritative if a later extension changes rules
            // or a nested group/provider selects a direct adapter.
            config.rules = ["MATCH," + policy];
            return config;
          }
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

