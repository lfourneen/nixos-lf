{ config, lib, tproxyMark ? "0x233" }:

let
  h = config.my.hardening;
  m = config.my.machine;
  p = config.my.proxy;
  join = lib.concatStringsSep ", ";
  quote = x: ''"${x}"'';
  family = cidr: if lib.hasInfix ":" cidr then "ip6" else "ip";
  byFamily = six: lib.filter (x: lib.hasInfix ":" x == six);
  known = [ m.vmBridge m.waydroidBridge ] ++ lib.optional h.hotspot.enable m.hotspot.interface;
  private4 = lib.unique (m.privateV4 ++ [ m.vmSubnet m.waydroidSubnet ] ++ lib.optional h.hotspot.enable m.hotspot.subnet);
  private6 = m.ulaV6 ++ m.linkLocalV6;

  unsafe4 = [ "0.0.0.0/8" "127.0.0.0/8" "169.254.169.254/32" "169.254.170.2/32"
    "100.100.100.200/32" "192.0.0.0/24" "198.18.0.0/15" "224.0.0.0/4" "240.0.0.0/4" p.fakeIpNet ];

  unsafe6 = lib.unique ([ "::/96" "::1/128" "fd00:ec2::254/128" "ff00::/8"
    "64:ff9b::/96" "64:ff9b:1::/48" "2001::/32" "2002::/16" ] ++ p.nat64Prefixes);

  protected4 = lib.unique (private4 ++ unsafe4);
  protected6 = lib.unique (private6 ++ unsafe6);
  uidOf = name: config.users.users.${name}.uid or null;

  socketActors = map (name: let
    uid = uidOf name;
    uidRanges = config.users.users.${name}.subUidRanges or [ ];
  in {
    inherit name uid uidRanges;
    owner = "{ ${join ([ (toString uid) ] ++ map (r: "${toString r.startUid}-${toString (r.startUid + r.count - 1)}") uidRanges)} }";
  }) h.socketGuestUsers;

  socketUids = lib.filter builtins.isInt (map uidOf h.socketGuestUsers);
  socketOwners = "{ ${join (lib.concatMap (a: [ (toString a.uid) ] ++ map (r: "${toString r.startUid}-${toString (r.startUid + r.count - 1)}") a.uidRanges) socketActors)} }";

  # Stable per-account conntrack identities, independent of declaration order.
  # Managed sing-tun uses non-mark mode; packet/socket routing marks stay intact.
  socketMark = uid: toString (1196425216 + uid); # 0x47500000 + fixed UID
  socketMarks = "{ ${join (map socketMark socketUids)} }";

  legacyHostPorts = protocol: iface: lib.optionals (h.guestHostServices && iface == m.vmBridge)
    (map lib.toInt (if protocol == "tcp" then h.guestTcpPorts else h.guestUdpPorts));

  networks = lib.imap0 (index: iface: let
    n = h.guestNetworks.${iface} or { trust = null; sourceCidrs = [ ]; gateways = [ ]; hostTcpPorts = [ ]; hostUdpPorts = [ ]; };
  in n // {
    interface = iface;
    inherit index;
    trust = if n.trust != null then n.trust else
      if h.guestPrivateEgress && builtins.elem iface [ m.vmBridge m.waydroidBridge ] then "trusted" else "restricted";
    hostTcpPorts = lib.unique (n.hostTcpPorts ++ legacyHostPorts "tcp" iface);
    hostUdpPorts = lib.unique (n.hostUdpPorts ++ legacyHostPorts "udp" iface);
    source4 = byFamily false n.sourceCidrs;
    source6 = byFamily true n.sourceCidrs;
    gateway4 = byFamily false n.gateways;
    gateway6 = byFamily true n.gateways;
  }) p.guestInterfaces;

  ndp = ''ip6 hoplimit 255 meta l4proto icmpv6 icmpv6 type { nd-neighbor-solicit, nd-neighbor-advert, nd-router-solicit }'';

  gatewayRules = n: verdict: lib.concatMapStrings (six: let
    gw = if six then n.gateway6 else n.gateway4;
    f = if six then "ip6" else "ip";
  in lib.optionalString (gw != [ ]) ''
    ${f} daddr { ${join gw} } meta l4proto { tcp, udp } th dport 53 ${verdict}
    ${lib.optionalString (n.hostTcpPorts != [ ]) ''${f} daddr { ${join gw} } tcp dport { ${join (map toString n.hostTcpPorts)} } ${verdict}''}
    ${lib.optionalString (n.hostUdpPorts != [ ]) ''${f} daddr { ${join gw} } udp dport { ${join (map toString n.hostUdpPorts)} } ${verdict}''}
  '') [ false true ];

  dhcp = n: verdict: lib.optionalString (n.gateway4 != [ ])
    ''ip saddr { 0.0.0.0, ${join n.source4} } ip daddr { 255.255.255.255, ${join n.gateway4} } udp sport 68 udp dport 67 ${verdict}'';

  sourceCheck = n: lib.concatMapStrings (six: let
    sources = if six then n.source6 else n.source4;
    f = if six then "ip6" else "ip";
  in if sources == [ ] then "meta nfproto ${if six then "ipv6" else "ipv4"} counter drop\n"
    else "${f} saddr != { ${join sources} } counter drop\n") [ false true ];

  entries = iface: lib.filter (e: e.interface == iface) h.guestPrivateExceptions;

  grants = n: reply: lib.concatMapStrings (e: lib.concatMapStrings (protocol: let
    ports = if protocol == "tcp" then e.tcpPorts else e.udpPorts;
  in lib.optionalString (ports != [ ]) (if reply then
    ''ct original ${family e.cidr} daddr ${e.cidr} ct protocol ${protocol} ct original proto-dst { ${join (map toString ports)} } return
    '' else ''${family e.cidr} daddr ${e.cidr} ${protocol} dport { ${join (map toString ports)} } return
    '')) [ "tcp" "udp" ]) (entries n.interface);

  unsafe = field: ''
    ${field "ip"} { ${join unsafe4} } counter drop
    ${field "ip6"} { ${join unsafe6} } counter drop
  '';

  rules = lib.concatMapStrings (n: ''
    chain lf_guest_from_${toString n.index} {
      ${dhcp n "accept"}
      ip6 saddr { ::, fe80::/10${lib.optionalString (n.source6 != [ ]) ", ${join n.source6}"} } ${ndp} accept
      ${sourceCheck n}
      ${gatewayRules n "return"}
      # A live B-only TPROXY grant marks this routing identity at -201.
      ip daddr ${p.fakeIpNet} meta mark ${tproxyMark} meta l4proto { tcp, udp } accept
      ${unsafe (f: "${f} daddr")}
      # Public guest IPv6 has no forwarding permission, including in A.
      ip6 daddr != { ${join private6} } counter drop
      ${grants n false}
      ${lib.optionalString (n.trust == "restricted") ''
      ip daddr { ${join protected4} } counter drop
      ip6 daddr { ${join protected6} } counter drop
      ''}
    }

    chain lf_guest_host_${toString n.index} {
      # A reply to a host-initiated connection is already authorized by the
      # conntrack entry; accept it here instead of returning into the parent
      # chain, whose final counter drop would otherwise swallow it.
      ct direction reply ct state established,related accept
      ${lib.optionalString (builtins.elem n.interface p.transparentGuestInterfaces) ''meta mark ${tproxyMark} accept''}
      ${dhcp n "accept"}
      ${ndp} accept
      ${gatewayRules n "accept"}
      counter drop
    }

    chain lf_guest_return_${toString n.index} {
      ${unsafe (f: "ct original ${f} daddr")}
      ct original ip6 daddr != { ${join private6} } counter drop
      ${grants n true}
      ${lib.optionalString (n.trust == "restricted") ''
      ct original ip daddr { ${join protected4} } counter drop
      ct original ip6 daddr { ${join protected6} } counter drop
      ''}
      return
    }

    chain lf_guest_to_${toString n.index} {
      ${lib.optionalString (n.source4 != [ ]) ''ct direction reply ct state established,related ct original ip saddr { ${join n.source4} } jump lf_guest_return_${toString n.index}
      ct direction reply ct state established,related ct original ip saddr { ${join n.source4} } return''}
      ${lib.optionalString (n.source6 != [ ]) ''ct direction reply ct state established,related ct original ip6 saddr { ${join n.source6} } jump lf_guest_return_${toString n.index}
      ct direction reply ct state established,related ct original ip6 saddr { ${join n.source6} } return''}
      counter drop
    }
  '') networks;

  socketGrants = lib.concatMapStrings (e: lib.concatMapStrings (protocol: let
    ports = if protocol == "tcp" then e.tcpPorts else e.udpPorts;
  in lib.optionalString (ports != [ ]) ''
    meta skuid ${(lib.findFirst (a: a.name == e.user) { owner = "{ 0 }"; } socketActors).owner} oifname ${quote e.interface} ${family e.cidr} daddr ${e.cidr} ${protocol} dport { ${join (map toString ports)} } return
  '') [ "tcp" "udp" ]) h.socketGuestPrivateExceptions;

  socketReplyGrants = uid: lib.concatMapStrings (e: lib.concatMapStrings (protocol: let
    ports = if protocol == "tcp" then e.tcpPorts else e.udpPorts;
  in lib.optionalString (uidOf e.user == uid && ports != [ ]) ''
    iifname ${quote e.interface} ct original ${family e.cidr} daddr ${e.cidr} ct protocol ${protocol} ct original proto-dst { ${join (map toString ports)} } return
  '') [ "tcp" "udp" ]) h.socketGuestPrivateExceptions;
  hostReplyRules = n: lib.concatMapStrings (six: let
    gateways = if six then n.gateway6 else n.gateway4;
    f = if six then "ip6" else "ip";
  in lib.optionalString (gateways != [ ]) ''
    ct original ${f} daddr { ${join gateways} } meta l4proto { tcp, udp } ct original proto-dst 53 return
    ${lib.optionalString (!six) ''ct original ip daddr { ${join gateways} } ct protocol udp ct original proto-src 68 ct original proto-dst 67 return''}
    ${lib.optionalString (n.hostTcpPorts != [ ]) ''ct original ${f} daddr { ${join gateways} } ct protocol tcp ct original proto-dst { ${join (map toString n.hostTcpPorts)} } return''}
    ${lib.optionalString (n.hostUdpPorts != [ ]) ''ct original ${f} daddr { ${join gateways} } ct protocol udp ct original proto-dst { ${join (map toString n.hostUdpPorts)} } return''}
  '') [ false true ];

in
{
  inherit known networks socketUids socketOwners socketMarks private4 private6 unsafe4 unsafe6 protected4 protected6;

  blockedReplies = lib.concatMapStrings (n: lib.concatMapStrings (six: let
    sources = if six then n.source6 else n.source4;
    scope = if six then private6 else private4;
    f = if six then "ip6" else "ip";
  in lib.optionalString (sources != [ ]) ''
    add rule inet lf_filter lf_blocked_guard oifname ${quote n.interface} ct direction reply ct original ${f} saddr { ${join sources} } ct original ${f} daddr != { ${join scope} } counter drop
  '') [ false true ]) networks;

  data = {
    inherit known;
    interfaces = p.guestInterfaces;
    declaredInterfaces = builtins.attrNames h.guestNetworks;
    networks = map (n: { inherit (n) interface trust sourceCidrs gateways hostTcpPorts hostUdpPorts; }) networks;
    exceptions = h.guestPrivateExceptions;
    socketUsers = map (a: { inherit (a) name uid uidRanges; }) socketActors;
    socketExceptions = h.socketGuestPrivateExceptions;
    users = lib.mapAttrs (_: u: u.uid) config.users.users;
    subUidRanges = lib.mapAttrs (_: u: u.subUidRanges or [ ]) config.users.users;
    protectedUsers = lib.unique ([ "root" "nobody" m.desktopUser "clash-core" "gost" "dnsmasq" "unbound" "chrony" "systemd-resolve" "dhcpcd" ]);
    tunInterface = p.tunDev;
    forbiddenInterfaces = [ "lo" m.wired.name m.wireless.name p.tunDev "tailscale0" ];
    privateScopes = private4 ++ m.ulaV6 ++ m.linkLocalV6;
    sourceScopes = lib.filter (cidr: cidr != "169.254.0.0/16") m.privateV4 ++ m.ulaV6;
    inherit unsafe4 unsafe6;

    reservedPorts = [ 53 m.ports.mihomoMixed m.ports.mihomoDns m.ports.dnsmasq m.ports.unbound
      m.ports.socketGuestDns m.ports.mihomoTproxy m.ports.gostHttp m.ports.gostRedirect ];
  };

  nft = rules + ''
    ${lib.concatMapStrings (n: ''
    chain lf_guest_host_reply_${toString n.index} {
      ${hostReplyRules n}
      # Current mode guards govern public TPROXY replies before core recovery.
      ct original ip daddr != { ${join protected4} } return
      ct original ip daddr ${p.fakeIpNet} return
      counter drop
    }
    '') networks}
    chain lf_guest_host_reply_guard {
      type filter hook output priority -198; policy accept;
      ${lib.concatMapStrings (n: lib.concatMapStrings (six: let
        sources = if six then n.source6 else n.source4;
        f = if six then "ip6" else "ip";
      in lib.optionalString (sources != [ ]) ''oifname ${quote n.interface} ct direction reply ct state established,related ct original ${f} saddr { ${join sources} } jump lf_guest_host_reply_${toString n.index}
      '') [ false true ]) networks}
    }
    chain lf_guest_private_guard {
      type filter hook prerouting priority -199; policy accept;
      ${lib.concatMapStrings (n: ''iifname ${quote n.interface} jump lf_guest_from_${toString n.index}
      '') networks}
    }

    chain lf_guest_host {
      ${lib.concatMapStrings (n: ''iifname ${quote n.interface} jump lf_guest_host_${toString n.index}
      '') networks}
      counter drop
    }

    chain lf_guest_forward_guard {
      type filter hook forward priority -1; policy accept;
      ${lib.concatMapStrings (n: ''oifname ${quote n.interface} jump lf_guest_to_${toString n.index}
      '') networks}
    }

    ${lib.optionalString (socketUids != [ ]) ''
    chain lf_socket_guest_private {
      ${unsafe (f: "${f} daddr")}
      ${socketGrants}
      counter drop
    }

    chain lf_socket_guest_guard {
      type filter hook output priority -202; policy accept;
      # Every ordinary :53 query is forced to the filtered real-address stub.
      meta skuid ${socketOwners} meta l4proto { tcp, udp } th dport 53 accept
      meta skuid ${socketOwners} ip daddr { ${join protected4} } jump lf_socket_guest_private
      meta skuid ${socketOwners} ip6 daddr { ${join protected6} } jump lf_socket_guest_private
    }

    chain lf_socket_guest_dns_after_nat {
      ip daddr 127.0.0.1 meta l4proto { tcp, udp } th dport ${toString m.ports.socketGuestDns} accept
      ip6 daddr ::1 meta l4proto { tcp, udp } th dport ${toString m.ports.socketGuestDns} accept
      counter drop
    }

    chain lf_socket_guest_dns_guard {
      # NAT lookup priorities order NAT chains inside the kernel's shared
      # dstnat hook (-100), not against filter hooks. Check after that hook.
      type filter hook output priority -90; policy accept;
      # NAT only initializes on a connection's first packet. A stale conntrack
      # entry must not keep an older DNS path after this identity is restricted.
      meta skuid ${socketOwners} ct direction original meta l4proto { tcp, udp } ct original proto-dst 53 jump lf_socket_guest_dns_after_nat
      meta skuid ${socketOwners} meta l4proto { tcp, udp } th dport 53 counter drop
    }

    chain lf_socket_guest_track {
      type filter hook output priority -199; policy accept;
      ${lib.concatMapStrings (a: ''meta skuid ${a.owner} ct direction original ct mark set ${socketMark a.uid}
      '') socketActors}
    }

    ${lib.concatMapStrings (uid: ''
    chain lf_socket_guest_reply_${toString uid} {
      ct status dnat ct original proto-dst 53 ct reply ip saddr 127.0.0.1 ct reply proto-src ${toString m.ports.socketGuestDns} return
      ct status dnat ct original proto-dst 53 ct reply ip6 saddr ::1 ct reply proto-src ${toString m.ports.socketGuestDns} return
      ${unsafe (f: "ct original ${f} daddr")}
      ${socketReplyGrants uid}
      ct original ip daddr { ${join protected4} } counter drop
      ct original ip6 daddr { ${join protected6} } counter drop
    }
    '') socketUids}

    chain lf_socket_guest_input {
      type filter hook input priority -2; policy accept;
      ${lib.concatMapStrings (uid: ''ct mark ${socketMark uid} ct direction reply ct state established,related jump lf_socket_guest_reply_${toString uid}
      '') socketUids}
    }
    ''}
  '';
}
