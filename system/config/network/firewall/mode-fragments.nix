{ lib, config, proxyKillSwitch, hostDirectGuard, guestGuard, vmTproxy,
  tproxyPrerouting, coreSocket, m, wired, wireless, tailscaleMark,
  exempt4, exempt6, tunDev, join, multicast4, guestPolicy,
  vmTransparentProxyIfaces, nftIfaces, tproxyMark, guestIfaces,
  local4, local6, fakeIpNet, timeNatReturn, timeCidrs4, timeCidrs6,
  coreMark, redirectExemptUidSet, redirectExempt4, redirectExempt6,
  port, strictReplyRules, actorConnmark, appConnmark, tunIpv4Address,
  addRules, guestReplyRules }:

let
  tunnel = config.my.proxy.ipTunnelPolicy;
  tunnelEnabled = config.my.proxy.ipTunnel != null;
  tunnelDevice = ''"${tunnel.device}"'';
  endpointFamily = if lib.hasInfix ":" tunnel.endpointAddress then "ip6" else "ip";
  # WireGuard preserves the inner socket owner during encapsulation.
  outer = ''oifname { "${wired}", "${wireless}" } ${endpointFamily} daddr ${tunnel.endpointAddress} udp sport ${toString tunnel.listenPort} udp dport ${toString tunnel.endpointPort} meta mark ${tunnel.outerMark}'';
  echo = [
    { family = "ip"; icmp = "icmp"; probe = tunnel.probeIpv4; }
    { family = "ip6"; icmp = "icmpv6"; probe = tunnel.probeIpv6; }
  ];
  probe = chain: verdict: lib.concatMapStrings ({ family, icmp, probe }: ''
    add rule inet lf_filter ${chain} oifname ${tunnelDevice} meta skuid 0 meta mark ${tunnel.probeMark} ${family} daddr ${probe} ${icmp} type echo-request ct mark set ${tunnel.probeMark} ${verdict}
  '') echo;
in {
  proxyModeRules = lib.optionalString proxyKillSwitch ''
    # One atomic load: the mode-dependent NAT tables are torn down here, inside the
    # same `nft -f`, so the fragment is applied or not (no partial window).
    destroy table ip lf_proxymode_nat
    destroy table ip6 lf_proxymode_nat

    # Leaving Mode C: the guard must be empty or its drop would refuse everything.
    flush chain inet lf_filter lf_blocked_guard
    ${lib.optionalString config.my.proxy.runtimeRouting ''
    flush set inet lf_filter source_recovery4
    flush set inet lf_filter source_recovery6
    ''}
    flush chain inet lf_filter lf_tailnet_guard
    flush chain inet lf_filter lf_strict_reply_guard
    ${hostDirectGuard}
    flush set inet lf_filter dns_plaintext4
    ${guestGuard "proxy"}
    ${lib.optionalString vmTproxy ''
    flush chain ip lf_mangle proxymode_tproxy
    ${tproxyPrerouting}
    ''}

    flush chain inet lf_filter proxymode_drops
    ${lib.optionalString tunnelEnabled ''
    ${probe "proxymode_drops" "accept"}
    add rule inet lf_filter proxymode_drops ${outer} accept
    ''}
    # The proxy core marks its own outbound sockets (routing-mark): allow it, it
    # must reach the nodes directly.
    add rule inet lf_filter proxymode_drops ${coreSocket} accept
    # DHCP clients on the uplinks: NM is root, dhcpcd has its pinned UID.
    add rule inet lf_filter proxymode_drops meta skuid { 0, ${toString m.uids.dhcpcd} } oifname { "${wired}", "${wireless}" } udp sport 68 udp dport 67 accept
    add rule inet lf_filter proxymode_drops meta skuid { 0, ${toString m.uids.dhcpcd} } oifname { "${wired}", "${wireless}" } udp sport 546 udp dport 547 accept
    # Tailscale transports require root ownership and its socket/packet mark.
    add rule inet lf_filter proxymode_drops meta skuid 0 oifname { "${wired}", "${wireless}" } meta l4proto udp socket mark ${tailscaleMark} meta mark ${tailscaleMark} counter accept
    # Everything else leaving an uplink for a non-local destination is dropped,
    # root included.
    add rule inet lf_filter proxymode_drops oifname { "${wired}", "${wireless}" } ip daddr != { ${exempt4} } counter drop
    add rule inet lf_filter proxymode_drops oifname { "${wired}", "${wireless}" } ip6 daddr != { ${exempt6} } counter drop

    flush chain inet lf_filter proxymode_tail
    ${lib.optionalString tunnelEnabled ''
    add rule inet lf_filter proxymode_tail oifname ${tunnelDevice} meta l4proto { icmp, icmpv6 } accept
    ''}
    add rule inet lf_filter proxymode_tail ${coreSocket} accept
    add rule inet lf_filter proxymode_tail oifname != { "lo", "${tunDev}" } ip daddr != { ${join multicast4} } counter drop
    add rule inet lf_filter proxymode_tail oifname != { "lo", "${tunDev}" } ip6 daddr != ${join m.multicastV6} counter drop

    # Guests. Forwarded traffic never reaches chain output and carries no skuid, so
    # the killswitch cannot see it: public destinations are refused instead (v4
    # and v6).
    flush chain inet lf_filter proxymode_forward
    ${lib.optionalString (config.my.proxy.coreNetns && vmTransparentProxyIfaces != [ ]) ''
    ${lib.concatMapStrings (n: lib.optionalString (builtins.elem n.interface vmTransparentProxyIfaces && n.source4 != [ ]) ''
    add rule inet lf_filter proxymode_forward iifname "${n.interface}" oifname "${tunDev}" ip saddr { ${join n.source4} } meta mark ${tproxyMark} accept
    '') guestPolicy.networks}
    add rule inet lf_filter proxymode_forward iifname "${tunDev}" oifname ${nftIfaces vmTransparentProxyIfaces} ct direction reply ct state established,related accept
    ''}
    add rule inet lf_filter proxymode_forward iifname ${nftIfaces guestIfaces} ip daddr != { ${local4} } counter drop
    add rule inet lf_filter proxymode_forward iifname ${nftIfaces guestIfaces} ip6 daddr != { ${local6} } counter drop
    add rule inet lf_filter proxymode_forward oifname ${nftIfaces guestIfaces} ip saddr != { ${local4} } counter drop
    add rule inet lf_filter proxymode_forward oifname ${nftIfaces guestIfaces} ip6 saddr != { ${local6} } counter drop

    # `redirect` is an nft statement keyword, so the chain cannot be named that.
    table ip lf_proxymode_nat {
      chain output {
        type nat hook output priority -100; policy accept;
        # Fake-IP traffic belongs to the TUN / auto-redirect path (including the
        # core's reply to the self-test, whose destination is the TUN address).
        # Never hand it to gost, which would re-source it and break the probe.
        ip daddr ${fakeIpNet} return
        # Retain the declared Chrony direct exception in the Gost fallback;
        # the earlier time guard checks the original target when constrained.
        ${timeNatReturn timeCidrs4}
        meta mark != ${coreMark} meta skuid != ${redirectExemptUidSet} ip daddr != { ${redirectExempt4} } tcp dport != { 53, ${toString port.dot} } counter redirect to :${toString port.gostRedirect}
      }
    }

    table ip6 lf_proxymode_nat {
      chain output {
        type nat hook output priority -100; policy accept;
        ${timeNatReturn timeCidrs6}
        meta mark != ${coreMark} meta skuid != ${redirectExemptUidSet} ip6 daddr != { ${redirectExempt6} } tcp dport != { 53, ${toString port.dot} } counter redirect to :${toString port.gostRedirect}
      }
    }
  '' + config.my.proxy.authorizationRules;

  # Mode C revokes application traffic and preserves bounded recovery paths.
  blockedModeRules = lib.optionalString proxyKillSwitch ''
    ${config.my.proxy.authorizationReset}
    # Tear the normal fragment down inside the same load: no stale redirect and
    # no stale drop can survive next to the guard.
    destroy table ip lf_proxymode_nat
    destroy table ip6 lf_proxymode_nat
    flush chain inet lf_filter proxymode_drops
    flush chain inet lf_filter proxymode_tail
    flush chain inet lf_filter proxymode_forward
    flush chain inet lf_filter lf_host_egress_guard
    flush chain inet lf_filter lf_host_direct
    flush chain inet lf_filter lf_tailnet_guard
    flush chain inet lf_filter lf_strict_reply_guard
    ${lib.optionalString config.my.proxy.strictBrowser ''
    ${lib.concatMapStrings (rule: "add rule inet lf_filter lf_strict_reply_guard ${rule}\n") strictReplyRules}
    ''}
    ${lib.optionalString config.services.tailscale.enable ''
    add rule inet lf_filter lf_tailnet_guard iifname "${config.services.tailscale.interfaceName}" counter drop
    ''}
    ${lib.optionalString (guestPolicy.socketUids != [ ]) ''
    add rule inet lf_filter lf_tailnet_guard ${actorConnmark} ${guestPolicy.socketMarks} ct direction reply counter drop
    ''}
    # Application replies are revoked with the same transaction as output.
    # Root/core/relay-originated transport stays unmarked for recovery probes.
    add rule inet lf_filter lf_tailnet_guard ${actorConnmark} ${appConnmark} ct direction reply iifname != "lo" counter drop
    add rule inet lf_filter lf_tailnet_guard ${actorConnmark} ${appConnmark} ct direction reply ct original ip daddr != 127.0.0.0/8 counter drop
    add rule inet lf_filter lf_tailnet_guard ${actorConnmark} ${appConnmark} ct direction reply ct original ip6 daddr != ::1 counter drop
    add rule inet lf_filter lf_tailnet_guard ${actorConnmark} ${appConnmark} ct direction reply meta l4proto { tcp, udp } ct original proto-dst { ${toString port.mihomoMixed}, ${toString port.mihomoStrict}, ${toString port.mihomoTproxy}, ${toString port.gostHttp}, ${toString port.gostRedirect} } counter drop
    ${lib.optionalString tunnelEnabled ''
    add rule inet lf_filter lf_tailnet_guard iifname ${tunnelDevice} ct mark != ${tunnel.probeMark} counter drop
    ''}
    flush set inet lf_filter dns_plaintext4
    ${guestGuard "blocked"}
    ${lib.optionalString vmTproxy "flush chain ip lf_mangle proxymode_tproxy"}

    flush chain inet lf_filter lf_blocked_guard
    ${lib.optionalString config.my.proxy.runtimeRouting "add rule inet lf_filter lf_blocked_guard meta mark 0x4c460003 ct mark 0x4c460003 accept"}
    ${lib.optionalString tunnelEnabled ''
    ${probe "lf_blocked_guard" "accept"}
    add rule inet lf_filter lf_blocked_guard ${outer} accept
    ''}
    # Refuse non-root access to local proxy inlets while recovery probes run.
    add rule inet lf_filter lf_blocked_guard meta skuid != 0 ip daddr 127.0.0.0/8 meta l4proto { tcp, udp } th dport { ${toString port.mihomoMixed}, ${toString port.mihomoStrict}, ${toString port.mihomoDns}, ${toString port.mihomoTproxy}, ${toString port.gostHttp}, ${toString port.gostRedirect} } counter drop
    add rule inet lf_filter lf_blocked_guard meta skuid != 0 ip6 daddr ::1 meta l4proto { tcp, udp } th dport { ${toString port.mihomoMixed}, ${toString port.mihomoStrict}, ${toString port.mihomoDns}, ${toString port.mihomoTproxy}, ${toString port.gostHttp}, ${toString port.gostRedirect} } counter drop
    # Local IPC (and the local resolver) keeps working.
    add rule inet lf_filter lf_blocked_guard ip daddr 127.0.0.0/8 accept
    add rule inet lf_filter lf_blocked_guard ip6 daddr ::1 accept
    # Cut in-flight transparent guest replies before the marked core exception.
    ${guestPolicy.blockedReplies}
    ${lib.optionalString config.my.proxy.coreNetns (lib.concatMapStrings (entry: ''
    add rule inet lf_filter lf_blocked_guard oifname "${tunDev}" ${entry.family} daddr ${entry.address} ct original ${entry.family} saddr ${entry.address} meta l4proto ${entry.icmp} ct direction reply ct state related accept
    '') [
      { family = "ip"; address = config.my.proxy.coreTopology.core4; icmp = "icmp"; }
      { family = "ip6"; address = config.my.proxy.coreTopology.core6; icmp = "icmpv6"; }
    ])}
    # The core keeps its egress so it can reach its nodes and recover.
    add rule inet lf_filter lf_blocked_guard ${coreSocket} accept
    # Root may reach the fake-ip network for the TUN self-test. Do not require
    # a particular oifname: the shared route checks and the probe's exact-source
    # log prove the effective path. Auto-redirect runs later at -150.
    add rule inet lf_filter lf_blocked_guard meta skuid 0 ip daddr ${fakeIpNet} meta l4proto { tcp, udp } th dport { 53, 80, 443 } accept
    # Retain the loopback-source reply allowance for the PREROUTING redirect
    # path. Reverse NAT runs after this chain, so replies still carry the
    # redirect listener's local source here.
    add rule inet lf_filter lf_blocked_guard meta skuid { 0, ${toString m.uids.clashCore} } ip saddr 127.0.0.1 ip daddr ${fakeIpNet} accept
    # The patched OUTPUT DNAT uses the TUN's local address, preserving sockets
    # bound to that interface. Permit only root TCP replies to those DNAT flows
    # so the fake-IP self-test can recover while C still refuses client egress.
    add rule inet lf_filter lf_blocked_guard meta skuid { 0, ${toString m.uids.clashCore} } ip saddr ${tunIpv4Address} ip daddr ${fakeIpNet} meta l4proto tcp ct direction reply ct status dnat ct original ip daddr ${fakeIpNet} ct original proto-dst { 80, 443 } accept
    # Retain pinned DHCP clients, including unicast renewals. Server replies
    # are only needed for the explicitly enabled AP and its own subnet.
    add rule inet lf_filter lf_blocked_guard meta skuid { 0, ${toString m.uids.dhcpcd} } oifname { "${wired}", "${wireless}" } udp sport 68 udp dport 67 accept
    ${addRules "lf_blocked_guard" guestReplyRules}
    add rule inet lf_filter lf_blocked_guard meta skuid { 0, ${toString m.uids.dhcpcd} } oifname { "${wired}", "${wireless}" } udp sport 546 udp dport 547 accept
    # Link maintenance has no application payload; without ND an IPv6-only
    # uplink cannot recover once its neighbour cache expires in Mode C.
    add rule inet lf_filter lf_blocked_guard oifname { "${wired}", "${wireless}" } ip6 hoplimit 255 meta l4proto icmpv6 icmpv6 type { nd-router-solicit, nd-neighbor-solicit, nd-neighbor-advert } accept
    # Everything else, on any interface, to any other destination: gone.
    add rule inet lf_filter lf_blocked_guard counter drop

    # Guests/VMs are forwarded traffic: the output guard never sees them, so they
    # are refused public destinations here (LAN/portal stays reachable; the
    # forward chain's early `jump proxymode_forward` runs before its accepts).
    add rule inet lf_filter proxymode_forward iifname ${nftIfaces guestIfaces} ip daddr != { ${local4} } counter drop
    add rule inet lf_filter proxymode_forward iifname ${nftIfaces guestIfaces} ip6 daddr != { ${local6} } counter drop
    add rule inet lf_filter proxymode_forward oifname ${nftIfaces guestIfaces} ip saddr != { ${local4} } counter drop
    add rule inet lf_filter proxymode_forward oifname ${nftIfaces guestIfaces} ip6 saddr != { ${local6} } counter drop
  '';
}
