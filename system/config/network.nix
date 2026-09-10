{ pkgs, config, lib, ... }:

let
  # Interfaces allowed to reach host services. Others still get internet
  # (NM/NAT) but cannot open host services. Don't add broad globs
  # ("wl*"/"usb*"): a rogue USB NIC would re-enter this trust set.
  lanGuard = ''iifname { "ens1", "wlo1", "virbr0", "tailscale0" }'';

in
{
  # Pin NIC names so they survive kernel naming changes. Wired matches by
  # hardware MAC only: Path can change if PCIe bus numbers shift.
  systemd.network.links."10-wired-ens1" = {
    linkConfig.Name = "ens1";

    matchConfig = {
      MACAddress = "fc:5c:ee:c5:db:de";
      Driver = "r8169";
    };
  };

  systemd.network.links."10-wifi-wlo1" = {
    linkConfig.Name = "wlo1";
    
    matchConfig = {
      Path = "pci-0000:03:00.0";
      Driver = "mt7921e";
    };
  };

  # Networking
  networking = {
    hostName = "nixos";
    networkmanager = {
      enable = true;
      # LAN's DHCP offers are broadcast and NM's clients drop them, so dhcpcd
      # owns the wired NIC. Match by name+MAC so NM can never grab it.
      unmanaged = [ "interface-name:ens1" "mac:fc:5c:ee:c5:db:de" ];
      dns = "systemd-resolved";   # Pin NM to resolved
      wifi.powersave = false;

      settings = {
        # Keep checking ON so GNOME can pop the captive-portal login page.
        # response = "" expects an empty 204 body (Cloudflare probe).
        connectivity = {
          uri = "http://cp.cloudflare.com/";
          response = "";
          interval = 300;
        };

        # Opportunistic 802.11w default (mitigates rogue-AP deauth).
        connection = {
          "wifi-sec.pmf" = 2;
        };

        ipv4 = {
          "ignore-auto-dns" = true;
        };

        ipv6 = {
          "ignore-auto-dns" = true;
        };
      };
    };

    resolvconf.enable = false;

    # dhcpcd owns ens1; keep its hooks out of resolv.conf (resolved owns DNS).
    interfaces.ens1.useDHCP = true;

    dhcpcd = {
      enable = true;
      extraConfig = ''
        nohook resolv.conf
        nohook ntp.conf
      '';
    };

    proxy = {
      default = "http://127.0.0.1:33332/";
      noProxy = "127.0.0.1,localhost,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,192.168.1.1,*.local";
    };
  };

  # Uppercase proxy vars for tools that ignore the lowercase *_proxy ones
  environment.sessionVariables = {
    HTTP_PROXY = "http://127.0.0.1:33332/";
    HTTPS_PROXY = "http://127.0.0.1:33332/";
    ALL_PROXY = "http://127.0.0.1:33332/";
    NO_PROXY = "127.0.0.1,localhost,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,192.168.1.1,*.local";
  };

  # Substituters mirrors
  nix = {
    settings = {
      substituters = [
        #"https://mirror.tuna.tsinghua.edu.cn/nix-channels/store"
        "https://mirrors.ustc.edu.cn/nix-channels/store"
        "https://cache.nixos.org"
        "https://cache.nixos-cuda.org"
        "https://noctalia.cachix.org"
      ];

      trusted-public-keys = [
        "cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M="
        "noctalia.cachix.org-1:pCOR47nnMEo5thcxNDtzWpOxNFQsBRglJzxWPp3dkU4="
      ];
    };
  };

  # Tailscale (encrypted tailnet; run `sudo tailscale up` once after install to login)
  services.tailscale = {
    enable = true;
    package = pkgs.unstable.tailscale;
  };

  # Avahi / mDNS (local discovery)
  services.avahi = {
    enable = true;
    nssmdns4 = true;
    nssmdns6 = true;
  };

  # Zapret
  services.zapret = {
    enable = true;
    configureFirewall = false;
    httpSupport = true;
    udpSupport = true;
    udpPorts = [ "443" ];

    params = [
      # Minimal DPI bypass, less aggressive
      "--dpi-desync=fake"
      "--dpi-desync-ttl=4"
      "--dpi-desync-split-pos=1,midsld"

      # --- risky options (can cause lag/packet loss) ---
      # "--dpi-desync=fake,multisplit"
      # "--dpi-desync-fooling=badseq"
      # "--dpi-desync-repeats=5"
    ];
  };

  # DNS PAC: dnsmasq (1054, always up) forwards to mihomo (1053) when clash is up,
  # public DNS otherwise; upstream switched by dns-pac.service (see dns-pac.nix).
  services.dnsmasq = {
    enable = true;
    settings = {
      port = 1054;
      bind-interfaces = true;
      interface = "lo";
      no-resolv = true;       # upstream only from conf-file
      no-hosts = true;
      strict-order = true;    # try DoT/mihomo first, plaintext only as last resort
      cache-size = 4096;
      "neg-ttl" = "30";       # cap negative caching (e.g. mihomo's empty AAAA) to 30s
      conf-file = "/run/dns-pac/servers.conf";  # written by dns-pac.service
    };
  };

  # Encrypted fallback resolver (DoT via AliDNS) used while clash is down, so
  # the "direct" DNS path is not plaintext. dns-pac points dnsmasq here.
  services.unbound = {
    enable = true;
    resolveLocalQueries = false;
    enableRootTrustAnchor = false;   # upstream DoT/TLS only, like mihomo's DoH
    settings = {
      server = {
        interface = [ "127.0.0.1@1055" ];
        "tls-upstream" = true;
      };
      "forward-zone" = [
        {
          name = ".";
          "forward-addr" = [
            "223.5.5.5@853#dns.alidns.com"
            "223.6.6.6@853#dns.alidns.com"
          ];
        }
      ];
    };
  };

  # NixOS disables build-time checkconf when remote-control is present, so
  # validate at start: a bad config fails fast with a clear error.
  systemd.services.unbound.preStart = lib.mkAfter ''
    ${config.services.unbound.package}/bin/unbound-checkconf /etc/unbound/unbound.conf
  '';

  # Resolved
  services.resolved = {
    enable = true;

    settings = {
      Resolve = {
        Domains = ["~."];

        MulticastDNS = "no";

        # One always-alive gateway; public DNS is only a safety net if it dies.
        DNS = [ "127.0.0.1:1054" ];
        FallbackDNS = [ "223.5.5.5" "119.29.29.29" "1.1.1.1" ];

        # Must be "no": opportunistic DoT tries cert validation against IPs and kills fallback
        DNSOverTLS = "no";

        # DNSSEC off: mihomo answers carry no DNSSEC signature -> SERVFAIL otherwise
        DNSSEC = "no";
        LLMNR = "no";   # Disable LLMNR (LAN poisoning surface)

        DNSStubListenerExtra = "udp:0.0.0.0:53";  # gated by firewall (hotspot only)
      };
    };
  };

  # Extra TCP/53 listener for hotspot clients (one address per directive in resolved)
  environment.etc."systemd/resolved.conf.d/10-tcp-stub.conf".text = ''
    [Resolve]
    DNSStubListenerExtra=tcp:0.0.0.0:53
  '';

  # nixos-rebuild reloads resolved (incomplete, "Reload operation timed out"); restart it instead
  systemd.services.systemd-resolved.restartIfChanged = true;
  # Restart (not reload) when the TCP/53 stub drop-in changes, for the same reason
  systemd.services.systemd-resolved.restartTriggers = [
    config.environment.etc."systemd/resolved.conf.d/10-tcp-stub.conf".source
  ];

  # Shutdown: stop NM before the user session, else user apps block on its
  # D-Bus and "Stopping User Manager" spins out its 90s timeout.
  systemd.services.NetworkManager.after = [ "user@1000.service" ];

  # Nftables (FireWall)
  networking.firewall.enable = false;
  
  networking.nftables = {
    enable = true;

    ruleset = ''
      table inet filter {
        chain input {
          type filter hook input priority 0; policy drop;

          ct state invalid drop
          iif lo accept
          ct state established,related accept

          # Obvious spoofing on the wired WAN (loopback/multicast/reserved sources)
          iifname "ens1" ip saddr { 127.0.0.0/8, 224.0.0.0/4, 240.0.0.0/4 } drop

          # Tailscale WireGuard endpoint (direct connections; ts-input does ACLs)
          udp dport 41641 accept

          # Tailnet: only Moonlight/Sunshine ports
          iifname "tailscale0" tcp dport { 47984, 47989, 47990, 48010 } accept
          iifname "tailscale0" udp dport 47998-48010 accept

          # ICMPv6 essentials before the public-IPv6 drop
          ip6 nexthdr icmpv6 icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit, nd-router-advert, packet-too-big, echo-request, destination-unreachable, time-exceeded } accept

          # Block all public IPv6 inbound
          ip6 saddr != { ::1, fe80::/10, fc00::/7 } drop

          ip protocol icmp icmp type { destination-unreachable, time-exceeded } accept
          ip protocol icmp icmp type echo-request limit rate 10/second accept

          # Hotspot AP
          iifname "wlo1" udp dport 67 accept
          iifname "wlo1" ip saddr 10.42.0.0/24 udp dport 53 accept
          iifname "wlo1" ip saddr 10.42.0.0/24 tcp dport 53 accept

          # Host services: trusted interfaces only (see lanGuard)
          ${lanGuard} ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } udp dport 5353 accept
          ${lanGuard} ip6 saddr { fe80::/10, fc00::/7 } udp dport 5353 accept

          ${lanGuard} ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } tcp dport 53317 accept
          ${lanGuard} ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } udp dport 53317 accept

          ${lanGuard} ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } tcp dport { 3389, 5900 } accept
          ${lanGuard} ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } tcp dport { 47984, 47989, 47990, 48010 } accept   # Sunshine
          ${lanGuard} ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } udp dport 47998-48010 accept

          # SSH — enable with system/programs/ssh.nix
          # ${lanGuard} ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } tcp dport 22 accept
          # iifname "tailscale0" tcp dport 22 accept

          iifname "virbr0" accept

          ${lanGuard} ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } tcp dport 25565 accept   # Minecraft

          drop
        }

        chain forward {
          type filter hook forward priority 0; policy drop;

          ct state established,related accept
          ct state invalid drop

          # Hotspot clients -> wired uplink only
          iifname "wlo1" oifname "ens1" ip saddr 10.42.0.0/24 accept
          oifname "wlo1" ip daddr 10.42.0.0/24 ct state established,related accept

          # Libvirt VM egress -> real uplinks only
          iifname "virbr0" oifname { "ens1", "wlo1" } accept
          oifname "virbr0" ct state established,related accept

          drop
        }

        chain output {
          type filter hook output priority 0; policy accept;

          ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } accept
          ip6 daddr { fe80::/10, fc00::/7 } accept
          ip daddr 127.0.0.0/8 accept
          ip6 daddr ::1 accept

          # Block WebRTC/STUN IP leaks from user apps. tailscaled runs as root,
          # so its endpoint discovery (direct connections) still works.
          meta skuid != 0 udp dport { 3478, 5349 } drop
          meta skuid != 0 tcp dport { 3478, 5349 } drop

          tcp dport { 7897 } accept
          udp dport { 7897 } accept

          # Zapret diversion for real internet traffic only
          ip daddr != { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } tcp dport { 80, 443 } counter queue num 200 bypass
          ip daddr != { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } udp dport 443 counter queue num 200 bypass
          ip6 daddr != { fe80::/10, fc00::/7 } tcp dport { 80, 443 } counter queue num 200 bypass
          ip6 daddr != { fe80::/10, fc00::/7 } udp dport 443 counter queue num 200 bypass
        }
      }

      # NAT
      table ip nat {
        chain postrouting {
          type nat hook postrouting priority 100;

          # Libvirt VMs -> real uplinks
          oifname { "ens1", "wlo1" } ip saddr 192.168.122.0/24 masquerade

          # Hotspot clients -> wired uplink
          oifname "ens1" ip saddr 10.42.0.0/24 masquerade
        }
      }
    '';
  };

  # CrowdSec — SSH brute-force protection (needs ssh.nix + the SSH rules above)
  #services.crowdsec = {
    #enable = true;

    #hub.collections = [
      #"crowdsecurity/sshd"
      #"crowdsecurity/linux"
    #];

    #localConfig.acquisitions = [
      #{
        #source = "journald";
        #journalctl_filter = [ "_SYSTEMD_UNIT=sshd.service" ];
        #labels.type = "syslog";
      #}
    #];
  #};

  #services.crowdsec-firewall-bouncer = {
    #enable = true;
    #registerBouncer.enable = true;
    #settings.mode = "nftables";
  #};

  # Fail2ban — simpler alternative to CrowdSec. NOTE: our input chain (priority 0,
  # policy drop) runs FIRST, so bans can only affect the ACCEPTED rules (i.e. the
  # SSH rules) — which is exactly what we want; verify chain order with `nft list ruleset`.
  #services.fail2ban = {
    #enable = true;

    #jails.sshd = {
      #filter = "sshd";
      #action = "nftables-allports";
      #maxretry = 3;
      #bantime = 3600;
      #findtime = 600;
      #settings.backend = "systemd";
    #};
  #};

  # BCC
  programs.bcc.enable = true;

  # Kernel modules
  boot.extraModprobeConfig = ''
    options mt7921e disable_aspm=1
  '';

  # Kernel settings
  boot.kernelModules = [ "tcp_bbr" ];

  boot.kernelParams = [
    # Disable USB auto-suspend
    "usbcore.autosuspend=-1"
  ];

  boot.kernel.sysctl = {
    # BBR + fq for better throughput on lossy links
    "net.core.default_qdisc" = "fq";
    "net.ipv4.tcp_congestion_control" = "bbr";

    # Cap buffers at 16 MiB to avoid bufferbloat
    "net.core.rmem_max" = 16777216;
    "net.core.wmem_max" = 16777216;
    "net.core.rmem_default" = 262144;
    "net.core.wmem_default" = 262144;

    # TCP auto-tuning: max 16 MiB
    "net.ipv4.tcp_rmem" = "4096 87380 16777216";
    "net.ipv4.tcp_wmem" = "4096 65536 16777216";

    # TCP Fast Open (client only, disable if unstable)
    "net.ipv4.tcp_fastopen" = 1;

    # Keep cwnd after idle (good for interactive use)
    "net.ipv4.tcp_slow_start_after_idle" = 0;

    # Slightly larger UDP buffers for QUIC/WebRTC
    "net.ipv4.udp_rmem_min" = 16384;
    "net.ipv4.udp_wmem_min" = 16384;

    # ECN
    "net.ipv4.tcp_ecn" = 1;

    # Netdev
    "net.core.netdev_max_backlog" = 16384;
    "net.core.netdev_budget" = 600;

    # Optmem
    "net.core.optmem_max" = 65536;
  };

  environment.systemPackages = with pkgs; [
    traceroute
  ];
}

