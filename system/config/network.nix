{ pkgs, config, lib, ... }:

let
  # Machine identity lives in system/config/machine.nix; everything below reads it.
  m = config.my.machine;
  wired = m.wired.name;
  wireless = m.wireless.name;
  port = m.ports;
  join = lib.concatStringsSep ", ";

  # Ranges the TUN leaves alone and the kill switch treats as reachable.
  multicast4 = m.multicastV4 ++ [ m.limitedBroadcastV4 ];
  local4 = join m.privateV4;
  exempt4 = join (m.privateV4 ++ multicast4);
  redirectExempt4 = join ([ "127.0.0.0/8" ] ++ m.privateV4 ++ multicast4);
  local6 = join (m.linkLocalV6 ++ m.ulaV6);
  exempt6 = join (m.linkLocalV6 ++ m.ulaV6 ++ m.multicastV6);
  redirectExempt6 = join ([ "::1" ] ++ m.linkLocalV6 ++ m.ulaV6 ++ m.multicastV6);

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

  # AP clients are only legitimate while the wireless interface *is* the AP: the
  # source subnet is spoofable in client mode, so the accepts are scoped to the AP.
  hotspotEnable = config.my.hardening.hotspot.enable;
  hotspoSrc = ''iifname "${wireless}" ip saddr ${m.hotspot.subnet}'';
  hotspotDst = ''ip daddr ${m.hotspot.address}'';
  # mDNS arrives as multicast, so only the host-service accepts add the group.
  hotspotHostSrc = ''${hotspoSrc} ip daddr { ${m.hotspot.address}, 224.0.0.251 }'';
  tailnetTcp = lib.concatStringsSep ", " config.my.hardening.tailnetTcpPorts;
  tailnetUdp = lib.concatStringsSep ", " config.my.hardening.tailnetUdpPorts;
  vmSrc = ''iifname "${m.vmBridge}" ip saddr ${m.vmSubnet}'';  # libvirt default net
  trustedLanSrc = ''iifname { "${wired}", "${wireless}" } ip saddr { ${join trustedLanCidrs} }'';
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

  clonedMac = config.my.hardening.wifi.clonedMacAddress;

  # Kill switch: non-root apps may only egress to loopback/LAN/DNS/NTP (fail
  # closed). Needs Clash Verge Service Mode (root core exempt via skuid 0).
  proxyKillSwitch = true;

  # Only root is exempt: mihomo egresses as root, so its DIRECT rule keeps working
  # in proxy mode. gost only dials loopback, so it needs no exemption.
  killSwitchUidSet = "{ 0 }";

  # gost stays out of the redirect so that a passthrough relay cannot dial itself;
  # its egress is still dropped by the killswitch, so this is not an egress path.
  redirectExemptUidSet = "{ 0, ${toString config.users.users.gost.uid} }";

  # The :53 redirect must exempt dnsmasq's own upstream sockets or they loop back
  # into its listener; root stays exempt as a manual escape hatch.
  dnsRedirectExemptUidSet = "{ 0, ${toString config.users.users.dnsmasq.uid} }";

  # The 53/853/123 channel is pinned per-process instead of opened to any
  # destination: unbound needs DoT (tcp/853) to its two fixed upstreams -- the
  # only resolver left when Clash is down -- and systemd-timesyncd needs
  # udp/123, whose peers rotate.
  #
  # A literal uid cannot be checked by the evaluator and would silently stop
  # matching the daemon; the pins live in the module body.
  dnsDotUpstreams = "{ ${join m.dotUpstreams} }";
  unboundUid = config.users.users.unbound.uid;
  dnsmasqUid = config.users.users.dnsmasq.uid;
  timesyncUid = config.users.users."systemd-timesync".uid;

  # Exemptions stay static so DNS/NTP keep working in direct mode; the drops and
  # the redirect are loaded only while Clash runs (see proxy-mode.nix).
  killSwitchAccepts = lib.optionalString proxyKillSwitch ''
    # Per-process, per-destination: only unbound's DoT and timesyncd's NTP.
    meta skuid ${toString unboundUid} oifname { "${wired}", "${wireless}" } ip daddr ${dnsDotUpstreams} tcp dport ${toString port.dot} accept
    meta skuid ${toString timesyncUid} oifname { "${wired}", "${wireless}" } udp dport ${toString port.ntp} accept
  '';

  # Fragment proxy-mode.service applies while Clash runs: LAN/multicast stay
  # link-local, and the tail also covers interfaces not pinned in network.links.
  proxyModeRules = lib.optionalString proxyKillSwitch ''
    flush chain inet filter proxymode_drops
    add rule inet filter proxymode_drops meta skuid != 0 udp dport { 3478, 5349 } drop
    add rule inet filter proxymode_drops meta skuid != 0 tcp dport { 3478, 5349 } drop
    add rule inet filter proxymode_drops meta skuid != ${killSwitchUidSet} oifname { "${wired}", "${wireless}" } ip daddr != { ${exempt4} } counter drop
    add rule inet filter proxymode_drops meta skuid != ${killSwitchUidSet} oifname { "${wired}", "${wireless}" } ip6 daddr != { ${exempt6} } counter drop

    flush chain inet filter proxymode_tail
    add rule inet filter proxymode_tail meta skuid != ${killSwitchUidSet} oifname != { "lo", "${tunDev}" } ip daddr != { ${join multicast4} } counter drop
    add rule inet filter proxymode_tail meta skuid != ${killSwitchUidSet} oifname != { "lo", "${tunDev}" } ip6 daddr != ${join m.multicastV6} counter drop

    # Guests. Forwarded traffic never reaches chain output and carries no skuid, so
    # the killswitch cannot see it: public destinations are refused instead.
    flush chain inet filter proxymode_forward
    add rule inet filter proxymode_forward iifname { "${wireless}", "${m.vmBridge}" } oifname { "${wired}", "${wireless}" } ip daddr != { ${local4} } counter drop

    # `redirect` is an nft statement keyword, so the chain cannot be named that.
    table ip proxymode_nat {
      chain output {
        type nat hook output priority -100; policy accept;
        meta skuid != ${redirectExemptUidSet} ip daddr != { ${redirectExempt4} } tcp dport != { 53, ${toString port.dot} } counter redirect to :${toString port.gostRedirect}
      }
    }

    table ip6 proxymode_nat {
      chain output {
        type nat hook output priority -100; policy accept;
        meta skuid != ${redirectExemptUidSet} ip6 daddr != { ${redirectExempt6} } tcp dport != { 53, ${toString port.dot} } counter redirect to :${toString port.gostRedirect}
      }
    }
  '';

  # TPROXY bridges whose egress goes through mihomo (tproxy-port, see
  # cvr-merge.nix). Empty = off; TUN only captures host output. Untested.
  vmTransparentProxyIfaces = [
    # "virbr0"
    # "waydroid0"
  ];
  vmTproxy = vmTransparentProxyIfaces != [ ];
  tproxyMark = "0x233";
  tproxyPrerouting = lib.concatMapStrings (i: ''
    iifname "${i}" ip daddr != { ${local4}, 127.0.0.0/8, ${join m.multicastV4} } meta l4proto { tcp, udp } tproxy to :${toString port.mihomoTproxy} meta mark set ${tproxyMark} accept
  '') vmTransparentProxyIfaces;
  tproxyInputAccept = lib.concatMapStrings (i: ''iifname "${i}" meta mark ${tproxyMark} accept
  '') vmTransparentProxyIfaces;

in
{
  # The nft rules match these daemons by uid, so the accounts are pinned.
  users.users.unbound.uid = m.uids.unbound;
  users.users.dnsmasq.uid = m.uids.dnsmasq;

  # A null or shared uid makes an exemption match nothing or the wrong daemon;
  # uniqueness is what the evaluator can check.
  assertions = [
    {
      assertion = lib.count (u: u.uid == unboundUid) (lib.attrValues config.users.users) == 1;
      message = ''
        network.nix: the pinned uid for unbound (${toString unboundUid}) must belong
        to exactly one account. Set my.machine.uids.unbound to a free number.
      '';
    }
    {
      assertion = lib.count (u: u.uid == dnsmasqUid) (lib.attrValues config.users.users) == 1;
      message = ''
        network.nix: the pinned uid for dnsmasq (${toString dnsmasqUid}) must belong
        to exactly one account. Set my.machine.uids.dnsmasq to a free number.
      '';
    }
  ];

  # The mode-dependent rules are applied by proxy-mode.service (see proxy-mode.nix).
  my.proxy.proxyModeRules = proxyModeRules;

  # Pin NIC names so they survive kernel naming changes. Wired matches by
  # hardware MAC only: Path can change if PCIe bus numbers shift.
  systemd.network.links."10-wired-${wired}" = {
    linkConfig.Name = wired;

    matchConfig = {
      MACAddress = m.wired.mac;
      Driver = m.wired.driver;
    };
  };

  systemd.network.links."10-wifi-${wireless}" = {
    linkConfig.Name = wireless;
    
    matchConfig = {
      Path = m.wireless.path;
      Driver = m.wireless.driver;
    };
  };

  # Networking
  networking = {
    hostName = m.hostName;
    networkmanager = {
      enable = true;
      # LAN's DHCP offers are broadcast and NM's clients drop them, so dhcpcd
      # owns the wired NIC. Match by name+MAC so NM can never grab it.
      unmanaged = [ "interface-name:${wired}" "mac:${m.wired.mac}" ];
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
        }
        # Emitted only when opted in; leaving the key out keeps NM's own default.
        // lib.optionalAttrs (clonedMac != "preserve") {
          "wifi.cloned-mac-address" = clonedMac;
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

    # dhcpcd owns the wired uplink; its hooks stay out of resolv.conf.
    interfaces.${wired}.useDHCP = true;

    dhcpcd = {
      enable = true;
      extraConfig = ''
        nohook resolv.conf
        nohook ntp.conf
      '';
    };

    proxy = {
      default = "http://127.0.0.1:${toString port.gostHttp}/";
      noProxy = "127.0.0.1,localhost,::1,${join m.privateV4},192.168.1.1,*.local";
    };
  };

  # Uppercase proxy vars for tools that ignore the lowercase *_proxy ones
  environment.sessionVariables = {
    HTTP_PROXY = "http://127.0.0.1:${toString port.gostHttp}/";
    HTTPS_PROXY = "http://127.0.0.1:${toString port.gostHttp}/";
    ALL_PROXY = "http://127.0.0.1:${toString port.gostHttp}/";
    NO_PROXY = "127.0.0.1,localhost,::1,${join m.privateV4},192.168.1.1,*.local";
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

  # Avahi / mDNS. Skip the untrusted physical LAN (${wired}); hotspot + tailnet only.
  services.avahi = {
    enable = true;
    nssmdns4 = true;
    nssmdns6 = true;
    allowInterfaces = [ "lo" wireless "tailscale0" ];
  };

  # DNS PAC: dnsmasq forwards to mihomo while clash runs and to the encrypted
  # fallback otherwise; dns-pac.service switches the upstream (see dns-pac.nix).
  services.dnsmasq = {
    enable = true;
    settings = {
      port = port.dnsmasq;
      bind-interfaces = true;
      interface = "lo";
      no-resolv = true;       # upstream only from conf-file
      no-hosts = true;
      strict-order = true;    # order within the mode's own list; see the note below
      cache-size = 4096;
      "neg-ttl" = "30";       # cap negative caching (e.g. mihomo's empty AAAA) to 30s
      conf-file = "/run/dns-pac/servers.conf";  # written by dns-pac.service
      # No static second server: a SERVFAIL from the validating upstream would fall
      # through to the non-validating DoT one under strict-order.
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
        interface = [ "127.0.0.1@${toString port.unbound}" ];
        "tls-upstream" = true;
      };
      "forward-zone" = [
        {
          name = ".";
          "forward-addr" = map (ip: "${ip}@${toString port.dot}#${m.dotAuthName}") m.dotUpstreams;
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
        DNS = [ "127.0.0.1:${toString port.dnsmasq}" ];
        FallbackDNS = [ "127.0.0.1:${toString port.unbound}" ];

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

    # Never flush the whole ruleset: it removes mihomo's own tables too, and only a
    # TUN restart recreates them. Our tables are torn down below instead.
    flushRuleset = false;

    ruleset = ''
      # Our own tables only. `destroy` is delete-if-exists, so this is idempotent
      # and no stale table survives a reload; foreign tables are never touched.
      # `ip mangle` is listed for the generations that enable vmTransparentProxyIfaces,
      # which is also what cleans it up when that option is turned off.
      destroy table inet filter
      destroy table ip nat
      destroy table ip6 nat
      destroy table ip mangle

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

          # Martian sources on the wired WAN. 100.64.0.0/10 is absent on purpose:
          # this uplink is CGNAT, so those are the ISP's own subscribers.
          iifname "${wired}" ip saddr { 0.0.0.0/8, 127.0.0.0/8, 169.254.0.0/16, 198.18.0.0/15, 224.0.0.0/4, 240.0.0.0/4, 255.255.255.255/32 } counter drop

          # Tailscale WireGuard endpoint (ts-input does the ACLs), scoped to the
          # uplinks so it cannot match public IPv6 inbound.
          iifname { "${wired}", "${wireless}" } udp dport 41641 accept

          # Tailnet: authenticated, but a blanket accept would expose every
          # 0.0.0.0-bound listener. List ports in my.hardening.tailnet{Tcp,Udp}Ports.
          iifname "tailscale0" tcp dport { ${tailnetTcp} } accept
          iifname "tailscale0" udp dport { ${tailnetUdp} } accept

          # ICMPv6 essentials before the public-IPv6 drop
          ip6 nexthdr icmpv6 icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit, nd-router-advert, packet-too-big, echo-request, destination-unreachable, time-exceeded } accept

          # Block all public IPv6 inbound
          ip6 saddr != { ::1, fe80::/10, fc00::/7 } drop

          ip protocol icmp icmp type { destination-unreachable, time-exceeded } accept
          ip protocol icmp icmp type echo-request limit rate 10/second accept

          # Hotspot AP (opt-in); the destination role test is ${m.hotspot.address}.
          ${lib.optionalString hotspotEnable ''
          iifname "${wireless}" udp dport 67 accept
          iifname "${wireless}" ip saddr ${m.hotspot.subnet} ${hotspotDst} udp dport 53 accept
          ''}

          # Own devices on the local hotspot AP (opt-in, same role test).
          ${lib.optionalString hotspotEnable (hostServices hotspotHostSrc)}
          ${lib.optionalString hotspotEnable ''iifname "${wireless}" ip6 saddr ${join m.linkLocalV6} udp dport 5353 accept''}

          # Explicitly trusted LAN prefixes (empty by default).
          ${trustedLanRules}

          # libvirt VMs -> host only.
          ${hostServices vmSrc}

          # Counted: dmesg is restricted, so this is the only view of what is refused.
          counter drop
        }

        chain forward {
          type filter hook forward priority 0; policy drop;

          # Guest policy, filled by proxy-mode while Clash runs and empty otherwise.
          # Before the conntrack accept so an established guest flow cannot leak.
          jump proxymode_forward

          ct state established,related accept
          ct state invalid drop

          # Hotspot clients -> wired uplink only (opt-in).
          ${lib.optionalString hotspotEnable ''
          iifname "${wireless}" oifname "${wired}" ip saddr ${m.hotspot.subnet} accept
          oifname "${wireless}" ip daddr ${m.hotspot.subnet} ct state established,related accept
          ''}

          # Libvirt VM egress -> real uplinks only
          iifname "${m.vmBridge}" oifname { "${wired}", "${wireless}" } accept
          oifname "${m.vmBridge}" ct state established,related accept

          counter drop
        }

        # Filled by proxy-mode.service while Clash runs; empty means no enforcement.
        # Declared before the jumps because a target must exist when the rule loads.
        chain proxymode_forward { }
        chain proxymode_drops { }
        chain proxymode_tail { }

        chain output {
          type filter hook output priority 0; policy accept;

          ip daddr { ${local4} } accept
          ip6 daddr { ${local6} } accept
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
          # public IP, not :${toString port.gostRedirect} -- count those as proxied,
          # not bypasses.
          tcp dport { ${toString port.mihomoMixed} } accept
          udp dport { ${toString port.mihomoMixed} } accept

          # Chain-tail reverse default-deny, loaded only while Clash runs.
          jump proxymode_tail
        }
      }

      # NAT
      table ip nat {
        # Force :53 out an uplink into dnsmasq: those destinations are the ones the
        # TUN excludes, so mihomo's dns-hijack never sees them. A redirect, not a
        # drop, so an in-range resolver still answers.
        chain output {
          type nat hook output priority -100; policy accept;

          meta skuid != ${dnsRedirectExemptUidSet} oifname { "${wired}", "${wireless}" } ip daddr != { 127.0.0.0/8, ${m.magicDns} } udp dport 53 redirect to :${toString port.dnsmasq}
          meta skuid != ${dnsRedirectExemptUidSet} oifname { "${wired}", "${wireless}" } ip daddr != { 127.0.0.0/8, ${m.magicDns} } tcp dport 53 redirect to :${toString port.dnsmasq}
        }

        chain postrouting {
          type nat hook postrouting priority 100;

          # Libvirt VMs -> real uplinks
          oifname { "${wired}", "${wireless}" } ip saddr ${m.vmSubnet} masquerade

          # Hotspot clients -> wired uplink (opt-in with the AP itself)
          ${lib.optionalString hotspotEnable ''oifname "${wired}" ip saddr ${m.hotspot.subnet} masquerade''}
        }
      }

      # IPv6 twin of the redirect above: otherwise a ULA or link-local resolver is
      # reached in the clear. Same selector; ::1 and the tailnet are exempt.
      table ip6 nat {
        chain output {
          type nat hook output priority -100; policy accept;

          meta skuid != ${dnsRedirectExemptUidSet} oifname { "${wired}", "${wireless}" } ip6 daddr != { ::1 } udp dport 53 redirect to :${toString port.dnsmasq}
          meta skuid != ${dnsRedirectExemptUidSet} oifname { "${wired}", "${wireless}" } ip6 daddr != { ::1 } tcp dport 53 redirect to :${toString port.dnsmasq}
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
    # Three failed loads leave the host with no ruleset; the failure has to be visible.
    unitConfig.OnFailure = [ "netsec-alert@%n.service" ];
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
    # The GUI owns tproxy-port, so enabling this without the GUI's TProxy switch
    # would send every VM flow to a closed port; refuse instead.
    preStart = ''
      ${pkgs.iproute2}/bin/ss -tln | ${pkgs.gnugrep}/bin/grep -q ':${toString port.mihomoTproxy} ' \
        || { echo "vmTransparentProxyIfaces is set but nothing listens on :${toString port.mihomoTproxy} (enable TProxy in the Clash Verge GUI)" >&2; exit 1; }
    '';
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
    options ${m.wireless.driver} disable_aspm=1
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

