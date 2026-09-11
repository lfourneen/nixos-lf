{ lib, ... }:

let
  # Mirror `tunMode` in system/config/network.nix (keep both in sync). When
  # true, adds the TUN-specific merge bits; false leaves the plain-proxy merge.
  tunMode = true;

in
{
  home.file.".local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/Merge.yaml" = {
    text = ''
      # Profile Enhancement Merge Template for Clash Verge

      profile:
        store-selected: true

      # Fix the mixed port to align with the probe and forwarding ports in gost-pac.nix.
      mixed-port: 7897

      # allow-lan:false keeps the plain proxy ports on loopback. bind-address
      # must stay "*" so the optional TPROXY listener (vmTransparentProxy) can
      # accept transparent traffic; the controller is loopback via config.yaml.
      allow-lan: false
      bind-address: "*"

      # TPROXY inbound for the optional VM transparent proxy (vmTransparentProxy).
      tproxy-port: 7896
      ${lib.optionalString tunMode ''
      # TUN: stack/auto-redirect/dns-hijack/strict-route are authoritative in the
      # Clash Verge GUI (Stack=Mixed, Auto Redirect=ON, DNS Hijack=any:53,
      # Strict Route=ON). strict-route is required because auto-route's
      # `from ::/1 iif lo` rule otherwise lets locally-generated IPv6 bypass TUN.
      tun:
        stack: mixed
        auto-route: true
        auto-redirect: true
        strict-route: true
        dns-hijack:
          - any:53
          - tcp://any:53
        mtu: 1500
      ''}
      # Foreign DoH (1.1.1.1/8.8.8.8) is blocked when dialed directly, but
      # `respect-rules` sends it through the proxy, so ipleak sees the proxy's
      # resolver instead of the local one. CN names stay on domestic DoH.
      dns:
        enable: true
        listen: 127.0.0.1:1053
        ipv6: false
        enhanced-mode: redir-host
        use-hosts: true
        respect-rules: true
        default-nameserver:
          - 223.5.5.5
          - 119.29.29.29
        proxy-server-nameserver:
          - https://223.5.5.5/dns-query
        nameserver:
          - https://1.1.1.1/dns-query
          - https://8.8.8.8/dns-query
        nameserver-policy:
          'geosite:cn':
            - https://223.5.5.5/dns-query
            - https://223.6.6.6/dns-query
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
    '';
    force = true;
  };
}

