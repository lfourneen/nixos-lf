{ lib, osConfig, ... }:

let
  # Single source of truth in system/config/network.nix (my.proxy.tunMode).
  tunMode = osConfig.my.proxy.tunMode;

  # Same single source as the firewall rules; the template must pin the same
  # device name or TUN traffic stops matching them.
  tunDev = osConfig.my.proxy.tunDev;

  m = osConfig.my.machine;
  port = m.ports;
  # Portal/LAN/CGNAT bypass the TUN; same ranges as the firewall.
  routeExclude = m.privateV4 ++ m.multicastV4 ++ [ "${m.limitedBroadcastV4}/32" ]
    ++ m.ulaV6 ++ m.linkLocalV6 ++ m.multicastV6;
  # No trailing newline: the interpolation line supplies it.
  yamlItems = indent: xs: lib.concatStringsSep "\n" (map (x: "${indent}- ${x}") xs);

in
{
  home.file.".local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/Merge.yaml" = {
    text = ''
      # Profile Enhancement Merge Template for Clash Verge

      profile:
        store-selected: true

      # Fix the mixed port to align with the probe and forwarding ports in gost-pac.nix.
      mixed-port: ${toString port.mihomoMixed}

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
      tproxy-port: ${toString port.mihomoTproxy}
      # `enable` must always be listed here: the GUI owns tun.enable, so without
      # this key a rebuild with tunMode = false leaves the TUN up with an empty
      # exclusion list. This template wins per key, and nftables-verify reports the
      # case where Verge injects the key after the merge instead.
      tun:
        enable: ${lib.boolToString tunMode}
      ${lib.optionalString tunMode ''
      # TUN: stack/dns-hijack/strict-route are authoritative in the Clash Verge
      # GUI (Stack=Mixed, DNS Hijack=any:53, Strict Route=ON). strict-route is
      # required because auto-route's `from ::/1 iif lo` rule otherwise lets
      # locally-generated IPv6 bypass TUN.
      #
      # auto-redirect needs the sing-tun patch applied in
      # system/programs/clash-verge.nix.
        stack: mixed
        device: ${tunDev}
        auto-route: true
        auto-redirect: true
        strict-route: true
        dns-hijack:
          - any:53
          - tcp://any:53
        mtu: 1500
        # Portal/LAN/CGNAT targets must bypass the TUN, else the ISP login page is
        # unreachable.
        route-exclude-address:
      ${yamlItems "    " routeExclude}
      ''}
      # Foreign DoH (1.1.1.1/8.8.8.8) is blocked when dialed directly, but
      # `respect-rules` sends it through the proxy, so ipleak sees the proxy's
      # resolver instead of the local one. CN names stay on domestic DoH.
      dns:
        enable: true
        listen: 127.0.0.1:${toString port.mihomoDns}
        ipv6: false
        enhanced-mode: redir-host
        use-hosts: true
        respect-rules: true
        # Encrypted bootstrap (both are IP literals, so no bootstrap recursion).
        default-nameserver:
          - tls://${lib.head m.dotUpstreams}
          - tls://119.29.29.29
        proxy-server-nameserver:
          - https://${lib.head m.dotUpstreams}/dns-query
        nameserver:
          - https://1.1.1.1/dns-query
          - https://8.8.8.8/dns-query
        nameserver-policy:
          'geosite:cn':
      ${yamlItems "      " (map (ip: "https://${ip}/dns-query") m.dotUpstreams)}
          'geosite:geolocation-!cn':
            - https://1.1.1.1/dns-query
            - https://8.8.8.8/dns-query
          # Subscription hardcodes a blocked Cloudflare DoH for these.
          '+.google.com': [ https://1.1.1.1/dns-query ]
          '+.googleapis.com': [ https://1.1.1.1/dns-query ]
          '+.googleapis.cn': [ https://1.1.1.1/dns-query ]
          '+.googlevideo.com': [ https://1.1.1.1/dns-query ]
          '+.gstatic.com': [ https://1.1.1.1/dns-query ]
          '+.youtube.com': [ https://1.1.1.1/dns-query ]
          '+.youtu.be': [ https://1.1.1.1/dns-query ]
          '+.facebook.com': [ https://1.1.1.1/dns-query ]
          '+.twitter.com': [ https://1.1.1.1/dns-query ]
          '+.x.com': [ https://1.1.1.1/dns-query ]
          '+.github.com': [ https://1.1.1.1/dns-query ]
          '+.githubusercontent.com': [ https://1.1.1.1/dns-query ]
          '+.openai.com': [ https://1.1.1.1/dns-query ]
          '+.chatgpt.com': [ https://1.1.1.1/dns-query ]
          '+.anthropic.com': [ https://1.1.1.1/dns-query ]

      # redir-host loses the domain once the client dials a real IP; sniffing
      # restores it so DOMAIN/GEOSITE rules match TUN traffic.
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

      # Force domains through the proxy group: prepend-rules wins by order, so its
      # entries beat GEOIP,CN,DIRECT. Fill in, e.g. DOMAIN-SUFFIX,your.example,<代理组名>
      prepend-rules: []

      # Keep the TCP controller off and never ship the default secret; replace the
      # placeholder with `openssl rand -hex 16`. Verge may override both keys.
      external-controller: ""
      secret: "<在此填入随机值>"
    '';
    force = true;
  };
}

