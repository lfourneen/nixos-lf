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
    ++ m.multicastV4 ++ [ "${m.limitedBroadcastV4}/32" ] ++ m.multicastV6
    ++ lib.optionals osConfig.my.proxy.coreNetns (let t = osConfig.my.proxy.coreTopology; in [ t.subnet4 t.subnet6 ]);

  # No trailing newline: the interpolation line supplies it.
  yamlItems = indent: xs: lib.concatStringsSep "\n" (map (x: "${indent}- ${x}") xs);

  # Always-resolve names (never handed a fake IP). The TUN route-exclude covers
  # ranges, not names, so this is what keeps an ISP portal, LAN host or captive
  # probe working by name. It replaces the subscription's list (merge wins by
  # key), so it must stay complete on its own.
  fakeIpFilter = osConfig.my.proxy.managedDns.fake-ip-filter;

  # Shared with the controlled renderer: system/config/network/proxy/probe-domains.nix.
  # Split-routing and IP-quality checks embed these names, so they must not hit
  # GEOIP,CN,DIRECT even while the rest of the rules stay in rule mode.
  probeDomains = osConfig.my.proxy.probeDomains;

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
      listeners: ${builtins.toJSON osConfig.my.proxy.managedProfile.listeners}
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
        exclude-uid: ${builtins.toJSON (osConfig.my.proxy.managedProfile."tun.exclude-uid" or [ ])}
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
  # (1) drop subscription leaves that fail the managed transport policy; the core
  # checks every remaining leaf when it is loaded; (2) replace DNS with the managed
  # object; (3) prepend forcedProxyDomains via the catch-all group. The core
  # validates later per-profile extensions too, so a leaf re-added by one of
  # them still fails closed at startup.
  home.file.".local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/Script.js" = {
    text = ''
      // Managed by home/config/clash/cvr-merge.nix -- do not edit in the GUI.
      const FORCED_PROXY_DOMAINS = ${forcedProxyDomainsJs};
      const DELAY_TEST_URL = ${builtins.toJSON osConfig.my.proxy.delayTestUrl};
      const REQUIRE_PROTECTED_TRANSPORT = ${lib.boolToString (osConfig.my.proxy.managedProfile.lf-private-targets.require-protected-node-transport or false)};
      const PROXY_ONLY = ${lib.boolToString osConfig.my.proxy.proxyOnly};
      const REMOTE_ONLY_CATCH_ALL = ${lib.boolToString osConfig.my.proxy.remoteOnlyCatchAll};
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

      function insecureValue(value) {
        if (REQUIRE_PROTECTED_TRANSPORT) return value !== false;
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
            if (/^skip[-_]cert[-_]verify$/iu.test(key) && insecureValue(value[key])) {
              return true;
            }
            if (disablesTLS(value[key])) return true;
          }
        }
        return false;
      }

      // Keep this check aligned with checkProtectedTransport in Mihomo's guard.
      function protectedTransport(node) {
        const fields = Object.create(null);
        for (const key of Object.keys(node)) {
          const name = key.toLowerCase().replace(/_/g, "-");
          if (/[^\x00-\x7f]/.test(key) || name in fields) return false;
          fields[name] = node[key];
        }
        const present = value => value && (typeof value !== "object" || Object.keys(value).length > 0);
        for (const option of ["dialer-proxy", "interface-name", "routing-mark", "plugin",
          "shadow-tls-opts", "restls-opts", "jls-opts", "tlsmirror-opts"]) {
          if (present(fields[option])) return false;
        }
        for (const option of ["tls", "udp"]) {
          if (option in fields && typeof fields[option] !== "boolean") return false;
        }
        const kind = fields.type;
        const network = "network" in fields ? fields.network : "";
        if (typeof network !== "string") return false;
        if (present(fields["reality-opts"]) &&
            (network === "ws" || !["vmess", "vless", "trojan"].includes(kind))) return false;
        switch (kind) {
          case "http":
          case "socks5":
            return network === "" && fields.tls === true && fields.udp !== true;
          case "ss":
            return network === "" && typeof fields.password === "string" && fields.password.trim() !== "" &&
              ["aes-128-gcm", "aes-192-gcm", "aes-256-gcm", "chacha20-ietf-poly1305",
               "xchacha20-ietf-poly1305", "2022-blake3-aes-128-gcm", "2022-blake3-aes-256-gcm",
               "2022-blake3-chacha20-poly1305"].includes(fields.cipher);
          case "vmess":
          case "vless":
            return ["", "tcp", "ws", "http", "h2", "grpc"].includes(network) &&
              (fields.tls === true || (kind === "vmess" && fields.alterid === 0 &&
                ["auto", "aes-128-gcm", "chacha20-poly1305"].includes(fields.cipher) &&
                typeof fields.uuid === "string" && fields.uuid.length === 36 &&
                /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(fields.uuid) &&
                fields.uuid !== "00000000-0000-0000-0000-000000000000"));
          case "trojan":
            return ["", "tcp", "ws", "grpc"].includes(network);
          case "tuic":
            return network === "" && (!("disable-sni" in fields) || fields["disable-sni"] === false) &&
              !present(fields.ip);
          case "hysteria2":
            return network === "" && !present(fields["realm-opts"]);
          case "anytls":
            return network === "";
          default:
            return false;
        }
      }

      function ruleTarget(rule) {
        if (typeof rule !== "string") return null;
        const parts = rule.split(",").map(part => part.replace(/^ +| +$/g, ""));
        const kind = parts[0].toUpperCase();
        if (kind === "MATCH" || kind === "FINAL") return parts[1];
        if (kind === "SUB-RULE") return null;
        if (["NOT", "OR", "AND", "DOMAIN-REGEX", "PROCESS-NAME-REGEX",
             "PROCESS-PATH-REGEX"].includes(kind)) return parts[parts.length - 1];
        return parts[2];
      }

      function dropInsecureProxies(config) {
        const proxies = config.proxies;
        if (!Array.isArray(proxies)) return;
        const dropped = new Set();
        const kept = [];
        for (const proxy of proxies) {
          if (proxy && typeof proxy === "object" &&
              (disablesTLS(proxy) || (REQUIRE_PROTECTED_TRANSPORT && !protectedTransport(proxy)))) {
            if (typeof proxy.name === "string") dropped.add(proxy.name);
            continue;
          }
          kept.push(proxy);
        }
        if (kept.length === proxies.length) return;
        config.proxies = kept;

        let groups = config["proxy-groups"];
        if (Array.isArray(groups)) {
          let removedGroup;
          do {
            removedGroup = false;
            groups = groups.filter(group => {
              if (!group || !Array.isArray(group.proxies) || group.proxies.length === 0) return true;
              group.proxies = group.proxies.filter(name => !dropped.has(name));
              if (group.proxies.length || (group.use && group.use.length) || group["include-all"] ||
                  group["include-all-proxies"] || group["include-all-providers"]) return true;
              dropped.add(group.name);
              removedGroup = true;
              return false;
            });
          } while (removedGroup);
          config["proxy-groups"] = groups;
        }
        const routes = [config.rules, ...Object.values(config["sub-rules"] || {})];
        for (const rules of routes) {
          if (Array.isArray(rules) && rules.some(rule => dropped.has(ruleTarget(rule)))) {
            throw new Error("A rule requires a proxy removed by the managed transport policy");
          }
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
        if (config) config["lf-dns-transport"] = ${builtins.toJSON osConfig.my.proxy.managedProfile.lf-dns-transport};
        // Replace the DNS object, including nested policy entries. Per-profile
        // extensions still run later; the core's final parser rejects any drift.
        if (config) config.dns = ${builtins.toJSON (osConfig.my.proxy.managedDns // {
          enhanced-mode = if tunMode then "fake-ip" else "redir-host";
          fake-ip-range = fakeIpRange;
        })};

        const rules = config && config.rules;
        if (Array.isArray(rules) && rules.length) {
          const policy = catchAllPolicy(rules);
          if (REMOTE_ONLY_CATCH_ALL || PROXY_ONLY) {
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

