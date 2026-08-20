{
  home.file.".local/share/io.github.clash-verge-rev.clash-verge-rev/profiles/Merge.yaml" = {
    text = ''
      # Profile Enhancement Merge Template for Clash Verge

      profile:
        store-selected: true

      # Fix the mixed port to align with the probe and forwarding ports in gost-pac.nix.
      mixed-port: 7897

      # Enable and bind the DNS listening port for leak prevention, for systemd-resolved to access.
      dns:
        enable: true
        listen: 127.0.0.1:1053
        ipv6: false
        enhanced-mode: fake-ip
        fake-ip-range: 198.18.0.1/16
        use-hosts: true
        default-nameserver:
          - 223.5.5.5
          - 119.29.29.29
        nameserver:
          - https://dns.cloudflare.com/dns-query
          - tls://1.1.1.1:853
        nameserver-policy:
          'geosite:cn':
            - https://doh.pub/dns-query
            - https://dns.alidns.com/dns-query
          'geosite:geolocation-!cn':
            - https://dns.cloudflare.com/dns-query
        proxy-server-nameserver:
          - https://dns.cloudflare.com/dns-query
          - tls://1.1.1.1:853
        fallback:
          - https://dns.cloudflare.com/dns-query
        fallback-filter:
          geoip: false
          geoip-code: CN
          ipcidr: []
          domain: []
    '';
    force = true;
  };
}
