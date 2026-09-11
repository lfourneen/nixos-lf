{ pkgs, config, lib, ... }:

let
  # Trusted LANs allowed to reach host services (private/CGNAT IP != trust).
  # Empty = none; add e.g. "192.168.1.0/24". Tailnet/hotspot/VM are built in.
  trustedLanCidrs = [
  ];

  # Ports a trusted peer may reach.
  hostServices = src: ''
    ${src} udp dport 5353 accept                            # mDNS/Avahi
    ${src} tcp dport 53317 accept                           # LocalSend
    ${src} udp dport 53317 accept
    ${src} tcp dport { 3389, 5900 } accept                  # RDP / VNC
    ${src} tcp dport { 47984, 47989, 47990, 48010 } accept  # Sunshine
    ${src} udp dport 47998-48010 accept
    ${src} tcp dport 25565 accept                           # Minecraft
    # ${src} tcp dport 22 accept                            # SSH (ssh.nix)
  '';

  hotspoSrc = ''iifname "wlo1" ip saddr 10.42.0.0/24'';
  vmSrc = ''iifname "virbr0" ip saddr 192.168.122.0/24'';  # libvirt default net
  trustedLanSrc = ''iifname { "ens1", "wlo1" } ip saddr { ${lib.concatStringsSep ", " trustedLanCidrs} }'';
  trustedLanRules = lib.optionalString (trustedLanCidrs != []) (hostServices trustedLanSrc);

  # TUN captures all L3 traffic (mihomo owns DNS/QUIC); false = plain
  # HTTP-proxy model. Toggles the per-app guards below.
  tunMode = true;
  tunDev = "Mihomo";   # GUI > TUN > Device Name

  # Force QUIC-heavy apps off UDP/443. Non-TUN only (under TUN it breaks
  # QUIC sites); kept for the fallback model.
  blockQuic = !tunMode;

  # Kill switch: non-root apps may only egress to loopback/LAN/DNS/NTP (fail
  # closed). Needs Clash Verge Service Mode (root core exempt via skuid 0).
  proxyKillSwitch = false;

  # Extra exempt UIDs (root 0 is always exempt).
  killSwitchExemptUids = [
    # 993
  ];
  killSwitchUidSet = "{ ${lib.concatStringsSep ", " (map toString ([ 0 ] ++ killSwitchExemptUids))} }";

  killSwitchRules = lib.optionalString proxyKillSwitch ''
    meta skuid != ${killSwitchUidSet} oifname { "ens1", "wlo1" } udp dport { 53, 123 } accept
    meta skuid != ${killSwitchUidSet} oifname { "ens1", "wlo1" } tcp dport { 53, 853 } accept
    meta skuid != ${killSwitchUidSet} oifname { "ens1", "wlo1" } ip daddr != { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } drop
    meta skuid != ${killSwitchUidSet} oifname { "ens1", "wlo1" } ip6 daddr != { fe80::/10, fc00::/7 } drop
  '';

  # TUN-bound packets must not be queued; zapret is for physical egress only.
  zapretGuard = lib.optionalString tunMode ''oifname { "ens1", "wlo1" } '';

  # Never queue mihomo's own packets for zapret: nfqws drops the auto-route
  # fwmark on reinjection, so auto-route feeds them back into TUN (DIRECT loop).
  mihomoMarkMask = "0xff0000";
  mihomoMarkValue = "0x80000";
  zapretMarkAccept = lib.optionalString tunMode ''
    meta mark and ${mihomoMarkMask} == ${mihomoMarkValue} accept
    meta skuid 0 accept'';

  # TPROXY bridges whose egress goes through mihomo (tproxy-port 7896, see
  # cvr-merge.nix). Empty = off; TUN only captures host output. Untested.
  vmTransparentProxyIfaces = [
    # "virbr0"
    # "waydroid0"
  ];
  vmTproxy = vmTransparentProxyIfaces != [ ];
  tproxyMark = "0x233";
  tproxyPrerouting = lib.concatMapStrings (i: ''
    iifname "${i}" ip daddr != { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 127.0.0.0/8, 224.0.0.0/4 } meta l4proto { tcp, udp } tproxy to :7896 meta mark set ${tproxyMark} accept
  '') vmTransparentProxyIfaces;
  tproxyInputAccept = lib.concatMapStrings (i: ''iifname "${i}" meta mark ${tproxyMark} accept
  '') vmTransparentProxyIfaces;

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

  # Avahi / mDNS. Skip the untrusted physical LAN (ens1); hotspot + tailnet only.
  services.avahi = {
    enable = true;
    nssmdns4 = true;
    nssmdns6 = true;
    allowInterfaces = [ "lo" "wlo1" "tailscale0" ];
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

        # Fallback is encrypted (unbound DoT); no plaintext leak.
        DNS = [ "127.0.0.1:1054" ];
        FallbackDNS = [ "127.0.0.1:1055" ];

        # Must be "no": opportunistic DoT tries cert validation against IPs and kills fallback
        DNSOverTLS = "no";

        # DNSSEC off: mihomo answers carry no DNSSEC signature -> SERVFAIL otherwise
        DNSSEC = "no";
        LLMNR = "no";   # Disable LLMNR (LAN poisoning surface)

        # Extra listeners for hotspot clients (firewall-gated). Both must live
        # in the same file: resolved only honors one across drop-ins.
        DNSStubListenerExtra = [ "udp:0.0.0.0:53" "tcp:0.0.0.0:53" ];
      };
    };
  };

  # nixos-rebuild reloads resolved (incomplete, "Reload operation timed out"); restart it instead
  systemd.services.systemd-resolved.restartIfChanged = true;

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
          # Clash Verge TUN device (local, root-owned): accept its replies.
          iifname "${tunDev}" accept
          ct state established,related accept
          # TPROXY'd bridge traffic, if enabled.
          ${tproxyInputAccept}

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

          # Tailnet is authenticated -> trust it (covers SSH too).
          iifname "tailscale0" accept

          # Own devices on the local hotspot AP.
          ${hostServices hotspoSrc}
          iifname "wlo1" ip6 saddr fe80::/10 udp dport 5353 accept

          # Explicitly trusted LAN prefixes (empty by default).
          ${trustedLanRules}

          # libvirt VMs -> host only.
          ${hostServices vmSrc}

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

          # WebRTC/STUN leak block (tailscaled is root, so unaffected).
          meta skuid != 0 udp dport { 3478, 5349 } drop
          meta skuid != 0 tcp dport { 3478, 5349 } drop

          # QUIC drop (non-TUN only; see blockQuic).
          ${lib.optionalString blockQuic "meta skuid != 0 udp dport 443 drop"}

          # Proxy kill switch (see let).
          ${killSwitchRules}

          # Bypass audit: `egress-audit` (ignore :33332 / node IPs).

          tcp dport { 7897 } accept
          udp dport { 7897 } accept

          # Zapret diversion (mihomo's own packets exempt, see zapretMarkAccept).
          ${zapretMarkAccept}
          ${zapretGuard}ip daddr != { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } tcp dport { 80, 443 } counter queue num 200 bypass
          ${zapretGuard}ip daddr != { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } udp dport 443 counter queue num 200 bypass
          ${zapretGuard}ip6 daddr != { fe80::/10, fc00::/7 } tcp dport { 80, 443 } counter queue num 200 bypass
          ${zapretGuard}ip6 daddr != { fe80::/10, fc00::/7 } udp dport 443 counter queue num 200 bypass
        }
      }

      # NAT
      table ip nat {
        # Non-TUN only: force plaintext DNS to dnsmasq (1054) so hardcoded
        # resolvers can't leak. Under TUN, dns-hijack owns :53. Exempt MagicDNS
        # and the mihomo/unbound bootstrap resolvers.
        chain output {
          type nat hook output priority -100; policy accept;

          ${lib.optionalString (!tunMode) ''
          meta skuid != 0 ip daddr != { 127.0.0.0/8, 100.100.100.100, 223.5.5.5, 223.6.6.6, 119.29.29.29 } udp dport 53 redirect to :1054
          meta skuid != 0 ip daddr != { 127.0.0.0/8, 100.100.100.100, 223.5.5.5, 223.6.6.6, 119.29.29.29 } tcp dport 53 redirect to :1054
          ''}
        }

        chain postrouting {
          type nat hook postrouting priority 100;

          # Libvirt VMs -> real uplinks
          oifname { "ens1", "wlo1" } ip saddr 192.168.122.0/24 masquerade

          # Hotspot clients -> wired uplink
          oifname "ens1" ip saddr 10.42.0.0/24 masquerade
        }
      }

      ${lib.optionalString vmTproxy ''
      # Divert listed bridges' public TCP/UDP into mihomo's tproxy port.
      table ip mangle {
        chain prerouting {
          type filter hook prerouting priority mangle; policy accept;
          ${tproxyPrerouting}
        }
      }
      ''}
    '';
  };

  # Deliver marked bridge packets locally to mihomo's tproxy socket.
  systemd.services.vm-transparent-proxy = lib.mkIf vmTproxy {
    description = "TPROXY routing for libvirt VMs";
    after = [ "network.target" "nftables.service" ];
    wants = [ "nftables.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = pkgs.writeShellScript "vm-tproxy-up" ''
        ${pkgs.iproute2}/bin/ip rule add fwmark ${tproxyMark} lookup 100 2>/dev/null || true
        ${pkgs.iproute2}/bin/ip route add local default dev lo table 100 2>/dev/null || true
      '';
      ExecStop = pkgs.writeShellScript "vm-tproxy-down" ''
        ${pkgs.iproute2}/bin/ip rule del fwmark ${tproxyMark} lookup 100 2>/dev/null || true
        ${pkgs.iproute2}/bin/ip route del local default dev lo table 100 2>/dev/null || true
      '';
    };
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

  # Kernel modules
  boot.extraModprobeConfig = ''
    options mt7921e disable_aspm=1
  '';

  # Kernel settings
  boot.kernelModules = [ "tcp_bbr" ] ++ lib.optionals vmTproxy [ "nft_tproxy" "nf_tproxy_ipv4" ];

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
    bpftrace
    traceroute
  ];
}

