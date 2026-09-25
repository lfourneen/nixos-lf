{ pkgs, config, lib, ... }:

let
  # Trusted LANs allowed to reach host services (private/CGNAT IP != trust).
  # Empty = none; add e.g. "192.168.1.0/24". Tailnet/hotspot/VM are built in.
  # Empty also means the home LAN can't reach hostServices (Sunshine/RDP/VNC/
  # Minecraft); only hotspot/VM/tailnet can.
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
  # HTTP-proxy model. Toggles the per-app guards below. Single source of
  # truth (my.proxy.tunMode) -- home/cvr-merge.nix reads it via osConfig.
  tunMode = config.my.proxy.tunMode;
  # TUN device name; home/cvr-merge.nix pins the same value via osConfig so the
  # firewall rules and the clash Merge template can't drift apart.
  tunDev = config.my.proxy.tunDev;

  # Force QUIC-heavy apps off UDP/443. Non-TUN only (under TUN it breaks
  # QUIC sites); kept for the fallback model.
  blockQuic = !tunMode;

  # Kill switch: non-root apps may only egress to loopback/LAN/DNS/NTP (fail
  # closed). Needs Clash Verge Service Mode (root core exempt via skuid 0).
  proxyKillSwitch = true;

  # Only root is exempt: mihomo egresses as root, so its DIRECT rule keeps working
  # in proxy mode. gost only dials loopback, so it needs no exemption.
  killSwitchUidSet = "{ 0 }";

  # gost stays out of the redirect so that a passthrough relay cannot dial itself;
  # its egress is still dropped by the killswitch, so this is not an egress path.
  redirectExemptUidSet = "{ 0, ${toString config.users.users.gost.uid} }";

  # The 53/853/123 channel is pinned per-process instead of opened to any
  # destination: unbound needs DoT (tcp/853) to its two fixed upstreams -- the
  # only resolver left when Clash is down -- and systemd-timesyncd needs
  # udp/123, whose peers rotate. unbound is a dynamic user, so its eval-time
  # null uid is pinned like gost's literal above.
  dnsDotUpstreams = "{ 223.5.5.5, 223.6.6.6 }";
  unboundUid = 983;
  timesyncUid = config.users.users."systemd-timesync".uid;

  # Exemptions stay static so DNS/NTP keep working in direct mode; the drops and
  # the redirect are loaded only while Clash runs (see proxy-mode.nix).
  killSwitchAccepts = lib.optionalString proxyKillSwitch ''
    # Per-process, per-destination: only unbound's DoT and timesyncd's NTP.
    meta skuid ${toString unboundUid} oifname { "ens1", "wlo1" } ip daddr ${dnsDotUpstreams} tcp dport 853 accept
    meta skuid ${toString timesyncUid} oifname { "ens1", "wlo1" } udp dport 123 accept
  '';

  # Fragment proxy-mode.service applies while Clash runs: LAN/multicast stay
  # link-local, and the tail also covers interfaces not pinned in network.links.
  proxyModeRules = lib.optionalString proxyKillSwitch ''
    flush chain inet filter proxymode_drops
    add rule inet filter proxymode_drops meta skuid != 0 udp dport { 3478, 5349 } drop
    add rule inet filter proxymode_drops meta skuid != 0 tcp dport { 3478, 5349 } drop
    add rule inet filter proxymode_drops meta skuid != ${killSwitchUidSet} oifname { "ens1", "wlo1" } ip daddr != { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 224.0.0.0/4, 255.255.255.255 } counter drop
    add rule inet filter proxymode_drops meta skuid != ${killSwitchUidSet} oifname { "ens1", "wlo1" } ip6 daddr != { fe80::/10, fc00::/7, ff00::/8 } counter drop

    flush chain inet filter proxymode_tail
    add rule inet filter proxymode_tail meta skuid != ${killSwitchUidSet} oifname != { "lo", "${tunDev}" } ip daddr != { 224.0.0.0/4, 255.255.255.255 } counter drop
    add rule inet filter proxymode_tail meta skuid != ${killSwitchUidSet} oifname != { "lo", "${tunDev}" } ip6 daddr != ff00::/8 counter drop

    # `redirect` is an nft statement keyword, so the chain cannot be named that.
    table ip proxymode_nat {
      chain output {
        type nat hook output priority -100; policy accept;
        meta skuid != ${redirectExemptUidSet} ip daddr != { 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 224.0.0.0/4, 255.255.255.255 } tcp dport != { 53, 853 } counter redirect to :33333
      }
    }

    table ip6 proxymode_nat {
      chain output {
        type nat hook output priority -100; policy accept;
        meta skuid != ${redirectExemptUidSet} ip6 daddr != { ::1, fe80::/10, fc00::/7, ff00::/8 } tcp dport != { 53, 853 } counter redirect to :33333
      }
    }
  '';

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
  # The mode-dependent rules are applied by proxy-mode.service (see proxy-mode.nix).
  my.proxy.proxyModeRules = proxyModeRules;

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
        "https://cache.numtide.com"
        "https://noctalia.cachix.org"
      ];

      trusted-public-keys = [
        "cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M="
        "niks3.numtide.com-1:DTx8wZduET09hRmMtKdQDxNNthLQETkc/yaX7M4qK0g="
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
      # dns-pac writes a single upstream line; pin a permanent encrypted second
      # one (unbound's DoT) after conf-file so strict-order still tries its pick
      # first (`all-servers` would double-send every query).
      server = [ "127.0.0.1#1055" ];
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

        # Extra listener for hotspot/tailnet clients (firewall-gated). UDP only:
        # the TCP twin never bound (resolved already holds 127.0.0.53/54:53 and
        # logs EADDRINUSE), so clients get no TCP DNS. Do not re-bind this to a
        # concrete address, which only exists while the hotspot is up.
        DNSStubListenerExtra = [ "udp:0.0.0.0:53" ];
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

        # Filled by proxy-mode.service while Clash runs; empty means no enforcement.
        # Declared before the jumps because a target must exist when the rule loads.
        chain proxymode_drops { }
        chain proxymode_tail { }

        chain output {
          type filter hook output priority 0; policy accept;

          ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } accept
          ip6 daddr { fe80::/10, fc00::/7 } accept
          ip daddr 127.0.0.0/8 accept
          ip6 daddr ::1 accept

          # QUIC drop (non-TUN only; see blockQuic).
          ${lib.optionalString blockQuic "meta skuid != 0 udp dport 443 drop"}

          # Per-process DNS/NTP exemptions; static so direct mode keeps working.
          ${killSwitchAccepts}

          # Enforcement is loaded only while Clash runs; empty chain = no-op.
          jump proxymode_drops

          # Clash mixed-port (loopback-only; already accepted above, listed
          # explicitly for auditing). NOTE: egress-audit uses connect(2)
          # pre-NAT, so transparently redirected flows show their original
          # public IP, not :33333 -- count those as proxied, not bypasses.
          tcp dport { 7897 } accept
          udp dport { 7897 } accept

          # Chain-tail reverse default-deny, loaded only while Clash runs.
          jump proxymode_tail
        }
      }

      # NAT
      table ip nat {
        # Non-TUN only: force plaintext DNS to dnsmasq (1054) so hardcoded
        # resolvers can't leak. Under TUN, dns-hijack owns :53. Exempt only
        # loopback and MagicDNS 100.100.100.100 (dialed by non-root resolved, so
        # the skuid prefix misses it); exempting a resolver just makes it time
        # out against the killswitch while the others are redirected.
        chain output {
          type nat hook output priority -100; policy accept;

          ${lib.optionalString (!tunMode) ''
          meta skuid != 0 ip daddr != { 127.0.0.0/8, 100.100.100.100 } udp dport 53 redirect to :1054
          meta skuid != 0 ip daddr != { 127.0.0.0/8, 100.100.100.100 } tcp dport 53 redirect to :1054
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

  # nftables is a oneshot with Restart=no, so a failed load would leave the host
  # with no firewall at all (resolved holds udp/0.0.0.0:53). Retry on failure;
  # on-failure is the only restart mode systemd allows for oneshot units.
  systemd.services.nftables = {
    serviceConfig = {
      Restart = "on-failure";
      RestartSec = "2s";
    };
    unitConfig.StartLimitBurst = 3;
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

