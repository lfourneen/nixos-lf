{ pkgs, ... }:

{
  # Networking
  networking = {
    hostName = "nixos";
    networkmanager = {
      enable = true;
      # "internal"/"dhclient" use UDP sockets and never get the broadcast DHCP
      # offers (dst 255.255.255.255) this LAN's server (100.67.0.1) sends:
      # the kernel drops them as martian while the iface has no IP, so NM's
      # connection dies at every cold boot until `dhcpcd` is run manually
      # (dhcpcd reads them via packet socket). Using dhcpcd as the NM backend
      # fixes it without changing anything else.
      dhcp = "dhcpcd";
      dns = "systemd-resolved";   # Pin NM to resolved
      wifi.powersave = false;
      settings = {
        connectivity = {
          interval = 0;
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
    proxy = {
      default = "http://127.0.0.1:33332/";
      noProxy = "127.0.0.1,localhost,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,192.168.1.1,*.local";
    };
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
  services.tailscale.enable = true;

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

  # Resolved
  services.resolved = {
    enable = true;
    settings = {
      Resolve = {
        Domains = ["~."];
        MulticastDNS = "no";
        # 223.5.5.5 in PRIMARY list: resolved's FallbackDNS list is BROKEN when a primary
        # DNS exists (verified: clash-off -> "All attempts failed" even though 223.5.5.5
        # answers fine; works only as primary). Same-list failover is reliable.
        DNS = [ "127.0.0.1:1053" "223.5.5.5" ];
        FallbackDNS = [ "1.1.1.1" "1.0.0.1" ];
        # Must be "no": opportunistic DoT tries cert validation against IPs and kills fallback
        DNSOverTLS = "no";
        # DNSSEC MUST be off: mihomo fake-ip answers carry no DNSSEC signature; even
        # allow-downgrade fails validation -> SERVFAIL for ALL system DNS (only proxy apps survive)
        DNSSEC = "no";
        LLMNR = "no";   # Disable LLMNR (LAN poisoning surface)
        DNSStubListenerExtra = "udp:0.0.0.0:53";  # gated by firewall (hotspot only)
      };
    };
  };

  # nixos-rebuild reloads resolved (incomplete, "Reload operation timed out"); restart it instead
  systemd.services.systemd-resolved.restartIfChanged = true;

  # Shutdown: stop NetworkManager BEFORE the user session (user@1000). With a
  # hotspot/shared connection active, NM's deactivation takes a while and user
  # apps (nm-applet, etc.) block in D-Bus calls to it, which makes the user
  # manager spin in "Stopping User Manager" for its full 90s stop timeout.
  # Reverse start-order => unit in After= stops first.
  systemd.services.NetworkManager.after = [ "user@1000.service" ];

  # Nftables (FireWall)
  networking.firewall.enable = false;
  networking.nftables = {
    enable = true;
    ruleset = ''
      table inet filter {
        chain input {
          type filter hook input priority 0; policy drop;

          # Drop untrackable packets
          ct state invalid drop

          # Loopback interface
          iif lo accept

          # Established and related connections (our outbound replies)
          ct state established,related accept

          # Tailscale tailnet: encrypted, ACL-gated at tailscaled level; ONLY RustDesk ports pass (DISABLED)
          # iifname "tailscale0" tcp dport { 21115, 21116, 21117, 21118, 21119 } accept
          # iifname "tailscale0" udp dport 21116 accept

          # Tailscale tailnet: encrypted, ACL-gated at tailscaled level; ONLY Moonlight/Sunshine ports pass
          iifname "tailscale0" tcp dport { 47984, 47989, 47990, 48010 } accept
          iifname "tailscale0" udp dport { 47998, 47999, 48000, 48002, 48010 } accept

          # ICMPv6 essentials (NDP + PMTUD + ping + traceroute) before the public-IPv6 drop
          ip6 nexthdr icmpv6 icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit, nd-router-advert, packet-too-big, echo-request, destination-unreachable, time-exceeded } accept

          # Block all public IPv6 inbound (link-local/ULA unaffected)
          ip6 saddr != { ::1, fe80::/10, fc00::/7 } drop

          # ICMP essentials (ping / traceroute)
          ip protocol icmp icmp type { echo-request, destination-unreachable, time-exceeded } accept

          # DHCP server (hotspot only)
          iifname "wlo1" udp dport 67 accept

          # DNS server (hotspot clients only)
          iifname "wlo1" ip saddr 10.42.0.0/24 udp dport 53 accept

          # mDNS / Avahi (local discovery)
          udp dport 5353 accept

          # P2P (LocalSend)
          tcp dport 53317 accept
          udp dport 53317 accept

          # Remote desktop protocols (LAN-only)
          ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } tcp dport { 3389, 5900, 47989 } accept  # RDP, VNC, Sunshine WebUI
          ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } udp dport { 47998, 47999, 48000, 48010 } accept  # Sunshine streaming ports

          # RustDesk server (DISABLED, keep uncommented only when services.rustdesk-server is re-enabled)
          # ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 } tcp dport { 21115, 21116, 21117, 21118, 21119 } accept
          # ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 } udp dport 21116 accept

          # Sunshine (LAN-only, RFC1918): 47984-47989 control/pairing, 47990 web UI, 48010 HTTPS streaming, 47998-48010 UDP A/V+WebRTC
          ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 } tcp dport { 47984, 47989, 47990, 48010 } accept
          ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 } udp dport { 47998, 47999, 48000, 48002, 48010 } accept

          # SSH — enable together with system/programs/ssh.nix (LAN + tailnet only, never public)
          # ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } tcp dport 22 accept
          # iifname "tailscale0" tcp dport 22 accept

          # Libvirt VMs (trusted local)
          iifname "virbr0" accept

          # Minecraft-Server (LAN-only)
          ip saddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } tcp dport 25565 accept

          # Everything else: silent drop
          drop
        }

        chain forward {
          type filter hook forward priority 0; policy drop;

          # Global conntrack for all forwarded flows
          ct state established,related accept
          ct state invalid drop

          # Hotspot
          iifname "wlo1" ip saddr 10.42.0.0/24 accept
          oifname "wlo1" ip daddr 10.42.0.0/24 ct state established,related accept

          # Libvirt
          iifname "virbr0" accept
          oifname "virbr0" ct state established,related accept
          iifname "virbr0" oifname { "ens1", "enp4s0", "wlo1" } accept

          # Default drop
          drop
        }

        chain output {
          type filter hook output priority 0; policy accept;

          # All private networks (incl. CGNAT)
          ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10 } accept
          ip6 daddr { fe80::/10, fc00::/7 } accept

          # Localhost and link-local
          ip daddr 127.0.0.0/8 accept
          ip6 daddr ::1 accept

          # Prevent Direct WebRTC STUN/TURN Requests Without a Proxy
          udp dport { 3478, 5349 } drop

          # Allow traffic from proxy software to the node server to bypass the Zapret queue
          tcp dport { 7897 } accept
          udp dport { 7897 } accept

          # Zapret diversion ONLY for real internet traffic
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
          oifname { "ens1", "enp4s0" } ip saddr 192.168.122.0/24 masquerade   # libvirt VM (interface renamed to ens1 by newer kernel)
          oifname "wlo1"  ip saddr 192.168.122.0/24 masquerade    # libvirt VM
          oifname != "wlo1" ip saddr 10.42.0.0/24 masquerade      # hotspot clients (wired or WiFi uplink)
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
    # Disable Active State Power Management (ASPM) for the onboard wired network card (PCIe)
    "pcie_aspm=off"

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
    crowdsec
    traceroute
  ];
}

