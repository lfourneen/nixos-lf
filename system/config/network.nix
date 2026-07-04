{ pkgs, ... }:

{
  # Networking
  networking = {
    hostName = "nixos";
    networkmanager = {
      enable = true;
      #dhcp = "internal";
      wifi.powersave = false;
      settings = {
        connectivity = {
          interval = 0;
        };
      };
    };
    resolvconf.enable = false;
    proxy = {
      default = "http://127.0.0.1:33332/";
      noProxy = "127.0.0.1,localhost,::1,10.0.0.0/8,192.168.0.0/16,172.16.0.0/12,192.168.1.1,*.local";
    };
  };

  # BCC
  programs.bcc.enable = true;

  # Magic DNS on Tailscale
  services.tailscale = {
    enable = true;
    useRoutingFeatures = "both";
  };

  # Resolved
  services.resolved = {
    enable = true;
    settings = {
      Resolve = {
        Domains = ["~."];
        #DNS = [ "1.1.1.1" "1.0.0.1" ];
        DNSStubListenerExtra = "udp:0.0.0.0:53";
      };
    };
  };

  # Substituters mirrors
  nix = {
    settings = {
      substituters = [
        #"https://mirror.tuna.tsinghua.edu.cn/nix-channels/store"
        "https://mirrors.ustc.edu.cn/nix-channels/store"
        "https://cache.nixos.org"
      ];
    };
  };

  # Nftables (FireWall)
  networking.firewall.enable = false;
  networking.nftables = {
    enable = true;
    ruleset = ''
      table inet filter {
        chain input {
            type filter hook input priority 0; policy drop;

            # Loopback interface
            iif lo accept

            # Established and related connections
            ct state established,related accept

            # ICMP / ICMPv6
            ip protocol icmp accept
            ip6 nexthdr icmpv6 accept

            # DHCP server (required for hotspot)
            udp dport 67 accept

            # DNS server (required by hotspot clients)
            udp dport 53 accept
            tcp dport 53 accept

            # mDNS / Avahi
            udp dport 5353 accept

            # Tailscale
            udp dport 41641 accept

            # P2P
            tcp dport 53317 accept
            udp dport 53317 accept

            # Remote desktop protocols
            tcp dport { 3389, 5900, 47989 } accept  # RDP, VNC, Sunshine WebUI
            udp dport { 47998, 47999, 48000, 48010 } accept  # Sunshine streaming ports

            # SSH (uncomment if needed)
            # tcp dport 22 accept

            # Libvirt
            iifname "virbr0" accept

            # Minecraft-Server
            tcp dport 25565 accept

            # Default reject
            reject with icmpx type admin-prohibited
        }

        chain forward {
          type filter hook forward priority 0; policy drop;

          # Hotspot
          iifname "wlo1" ip saddr 10.42.0.0/24 accept
          oifname "wlo1" ip daddr 10.42.0.0/24 ct state established,related accept

          # Libvirt
          iifname "virbr0" accept
          oifname "virbr0" ct state established,related accept
          iifname "virbr0" oifname { "enp4s0", "wlo1" } accept

          # Default Reject
          reject with icmpx type admin-prohibited
        }

        chain output {
          type filter hook output priority 0; policy accept;

          # Direct to router
          ip daddr 192.168.1.1 accept
        
          # All private networks
          ip daddr { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 } accept
          ip6 daddr { fe80::/10, fc00::/7 } accept

          # Localhost and link-local
          ip daddr 127.0.0.0/8 accept
          ip6 daddr ::1 accept

          # Zapret diversion ONLY for real internet traffic
          ip daddr != { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 } tcp dport { 80, 443 } counter queue num 200 bypass
          ip daddr != { 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 } udp dport 443 counter queue num 200 bypass
          ip6 daddr != { fe80::/10, fc00::/7 } tcp dport { 80, 443 } counter queue num 200 bypass
          ip6 daddr != { fe80::/10, fc00::/7 } udp dport 443 counter queue num 200 bypass
        }
      }

      # NAT
      table ip nat {
        chain postrouting {
          type nat hook postrouting priority 100;
          oifname "enp4s0" ip saddr 192.168.122.0/24 masquerade
          oifname "wlo1"  ip saddr 192.168.122.0/24 masquerade
          oifname "enp4s0" masquerade   # hotspot clients
        }
      }
    '';
  };

  # CrowdSec
  #services.crowdsec = {
    #enable = true;
    #hub = {
      #collections = [
        #"crowdsecurity/sshd"
        #"crowdsecurity/linux"
      #];
    #};
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
    #settings = {
      #mode = "nftables";
    #};
  #};

  # Fail2ban: intended for exposed SSH / servers
  #services.fail2ban = {
    #enable = true;
    #jails = {
      #sshd = {
        #filter = "sshd";
        #action = "nftables-allports";
        #maxretry = 3;
        #bantime = 3600;
        #findtime = 600;
        #settings = {
          #backend = "systemd";
        #};
      #};
    #};
  #};

  # Avahi / mDNS (local discovery)
  services.avahi = {
    enable = true;
    nssmdns4 = true;
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

