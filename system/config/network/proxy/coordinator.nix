{ pkgs, config, lib, ... }:

let
  # The two mode-dependent fragments. `rules` is the normal Mode-B refusal (the
  # core is exempt by its mark and may reach its nodes); `blockedRules` is the
  # strict application cut; the core keeps its marked recovery sockets.
  rules = pkgs.writeText "proxymode-rules.nft" config.my.proxy.proxyModeRules;
  blockedRules = pkgs.writeText "blockedmode-rules.nft" config.my.proxy.blockedModeRules;
  staticRules = pkgs.writeText "lf-static-rules.nft" config.networking.nftables.ruleset;
  policyGuard = import ../firewall/policy-guard.nix {
    inherit lib;
    leaseEnabled = config.my.proxy.authorizationLeaseSeconds != null;
  };

  # These unnamespaced names belonged to older generations, but can now belong
  # to other software. Only an explicit operator migration may delete them.
  legacyRules = pkgs.writeText "lf-legacy-firewall-migration.nft" ''
    destroy table inet filter
    destroy table ip proxymode_nat
    destroy table ip6 proxymode_nat
  '';

  verifyRequest = pkgs.writeShellScript "netsec-request" ''
    umask 022
    mkdir -p /run/netsec/requests
    ${pkgs.coreutils}/bin/mktemp /run/netsec/requests/request.XXXXXXXXXX >/dev/null
  '';

  rulesCheck = pkgs.writeShellScript "netsec-rules" ''
    exec ${pkgs.python3}/bin/python3 ${../firewall/rules.py} "$@"
  '';

  # Parse each complete mode with the same nft implementation in an isolated
  # userspace kernel. `-e` echoes applied objects (a bare `list ruleset` inside
  # the same batch would not). That echo still carries rules a fragment later
  # removes with `flush chain`, so rules.py folds the flush targets against a
  # static-only run before publishing the expected document; otherwise a chain
  # that is static in Mode A but emptied by the B/C fragment could never compare
  # equal to the live dump.
  expectedRules = pkgs.runCommand "lf-expected-rules" { } ''
    mkdir -p "$out"
    cat ${staticRules} > static.nft
    LD_PRELOAD="${pkgs.lklWithFirewall.lib}/lib/liblkl-hijack.so" ${pkgs.nftables}/bin/nft -e -j -f static.nft > "$out/static.raw.json"
    for mode in direct proxy blocked; do
      cat ${staticRules} > rules.nft
      fragment=/dev/null
      case "$mode" in
        proxy) cat ${rules} >> rules.nft; fragment=${rules} ;;
        blocked) cat ${blockedRules} >> rules.nft; fragment=${blockedRules} ;;
      esac
      LD_PRELOAD="${pkgs.lklWithFirewall.lib}/lib/liblkl-hijack.so" ${pkgs.nftables}/bin/nft -e -j -f rules.nft > "$out/$mode.raw.json"
      ${rulesCheck} expected "$out/static.raw.json" "$out/$mode.raw.json" "$fragment" "$out/$mode.json"
    done
    rm -f "$out"/*.raw.json static.nft rules.nft
  '';

  m = config.my.machine;
  port = m.ports;

  # Same canonical form nft prints (and nftables-verify greps for), so the
  # "core exemption present?" test reads the live rules consistently.
  coreMark = "0x" + lib.toLower (lib.fixedWidthString 8 "0" (lib.toHexString m.mihomoMark));

  # The TUN path is only required with tunMode; the plain HTTP-proxy model
  # (tunMode=false) deliberately runs Clash without a TUN, so it must not be
  # blackholed for lacking one. With tunMode the TUN is mandatory.
  requireTun = config.my.proxy.tunMode;

  tunSettings = pkgs.writeText "proxy-tun.json" (builtins.toJSON {
    ip = "${pkgs.iproute2}/bin/ip";
    device = config.my.proxy.tunDev;
    table = config.my.proxy.routeTable;
    ipv6 = config.networking.enableIPv6;
    uids = lib.unique [ 0 config.users.users.${m.desktopUser}.uid ];
    observer = if config.my.proxy.coreNetns then config.my.proxy.coreTopology.observer else null;
    observer_module = ./core_network.py;
  });

  tunCheck = pkgs.writeShellScript "proxy-tun-check" ''
    exec ${pkgs.python3}/bin/python3 ${./tun.py} ${tunSettings}
  '';

  tunProbeName = config.my.proxy.tunProbeName;
  domesticProbe = config.my.proxy.rulesDomesticProbeUrl;
  applicationDnsProbe = name: {
    description = "Check the system DNS stub with an application identity";
    serviceConfig = {
      Type = "oneshot";
      User = config.my.machine.desktopUser;
      Slice = "untrusted.slice";
      TimeoutStartSec = 3;
      NoNewPrivileges = true;
      CapabilityBoundingSet = "";
      ProtectSystem = "strict";
      ProtectHome = true;
      IPAddressDeny = "any";
      IPAddressAllow = "localhost";
      RestrictAddressFamilies = [ "AF_INET" "AF_UNIX" ];
    };
    script = ''
      set -o pipefail
      ${pkgs.dnsutils}/bin/dig @127.0.0.53 +time=2 +tries=1 +short ${lib.escapeShellArg name} A \
        | ${pkgs.gnugrep}/bin/grep -E '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' >/dev/null
    '';
  };

  # The core runs as clash-core and owns its control socket outside the desktop
  # user's 0700 runtime directory. An unprivileged forwarder exposes the legacy
  # /run/user/<uid>/clash-verge-rev/verge-mihomo.sock path for the GUI, so the
  # supervisors watch the socket the core actually creates.
  clashCoreUid = m.uids.clashCore;
  coreUids = if config.programs.clash-verge.enable then [ clashCoreUid ] else [ 0 clashCoreUid ];
  mihomoDir = "/run/clash-verge-rev/core";
  mihomoSock = "${mihomoDir}/mihomo.sock";
  frontSock = "/run/user/${toString config.users.users.${config.my.machine.desktopUser}.uid}/clash-verge-rev/verge-mihomo.sock";

  listener = service: uids: protocol: address: port: {
    inherit uids protocol address port;
    namespace = config.my.proxy.coreNetns && service == "clash-verge";
    cgroup = "/system.slice/${service}.service";
  };

  dnsListeners = service: uids: port:
    map (protocol: listener service uids protocol "127.0.0.1" port) [ "tcp" "udp" ];

  validatorListeners = lib.optionals config.my.proxy.localDnssec
    (map (value: value // { namespace = true; })
      (dnsListeners "clash-dns-application" [ config.users.users.unbound.uid ] port.unbound
       ++ dnsListeners "clash-dns-infrastructure" [ config.users.users.unbound.uid ] port.socketGuestDns
       ++ dnsListeners "clash-verge" coreUids port.coreDnsTransport
       ++ dnsListeners "clash-verge" coreUids port.coreDnsBootstrapTransport));

  listenerSettings = pkgs.writeText "proxy-listeners.json" (builtins.toJSON {
    ss = "${pkgs.iproute2}/bin/ss";
    observer = config.my.proxy.coreTopology.observer;
    observer_module = ./core_network.py;
    groups = {
      core-dns = dnsListeners "clash-verge" coreUids port.mihomoDns ++ validatorListeners;
      core-mixed = [ (listener "clash-verge" coreUids "tcp" "127.0.0.1" port.mihomoMixed) ];

      core = [ (listener "clash-verge" coreUids "tcp" "127.0.0.1" port.mihomoMixed) ]
        ++ dnsListeners "clash-verge" coreUids port.mihomoDns ++ validatorListeners
        ++ lib.optionals (config.my.proxy.transparentGuestInterfaces != [ ])
          (map (protocol: listener "clash-verge" coreUids protocol "0.0.0.0" port.mihomoTproxy) [ "tcp" "udp" ]);

      dns = dnsListeners "dnsmasq" [ 0 config.users.users.dnsmasq.uid ] port.dnsmasq
        ++ dnsListeners "unbound" [ 0 config.users.users.unbound.uid ] port.unbound
        ++ lib.optionals config.my.proxy.runtimeRouting
          (dnsListeners "proxy-real-dns" [ m.uids.socketGuestDns ] port.coreRealDns
            ++ dnsListeners "proxy-real-dns" [ m.uids.socketGuestDns ] port.coreInfrastructureDns
            ++ dnsListeners "socket-guest-dns" [ m.uids.socketGuestDns ] port.socketGuestDns
            ++ lib.optionals config.networking.enableIPv6 (lib.concatMap (protocol: [
              (listener "proxy-real-dns" [ m.uids.socketGuestDns ] protocol "::1" port.coreRealDns)
              (listener "proxy-real-dns" [ m.uids.socketGuestDns ] protocol "::1" port.coreInfrastructureDns)
              (listener "socket-guest-dns" [ m.uids.socketGuestDns ] protocol "::1" port.socketGuestDns)
            ]) [ "tcp" "udp" ]))
        ++ lib.optionals config.networking.enableIPv6
          (map (protocol: listener "dnsmasq" [ 0 config.users.users.dnsmasq.uid ] protocol "::1" port.dnsmasq) [ "tcp" "udp" ]);

      socket-guest-dns = lib.optionals (config.my.hardening.socketGuestUsers != [ ])
        (dnsListeners "socket-guest-dns" [ m.uids.socketGuestDns ] port.socketGuestDns
          ++ lib.optionals config.networking.enableIPv6
            (map (protocol: listener "socket-guest-dns" [ m.uids.socketGuestDns ] protocol "::1" port.socketGuestDns) [ "tcp" "udp" ]));

      relay = [
        (listener "gost-relay" [ m.uids.gost ] "tcp" "127.0.0.1" port.gostHttp)
        (listener "gost-relay" [ m.uids.gost ] "tcp" "127.0.0.1" port.gostRedirect)
        (listener "gost-relay" [ m.uids.gost ] "tcp" "::1" port.gostRedirect)
      ];
    };
  });

  listenerCheck = pkgs.writeShellScript "proxy-listeners" ''
    exec ${pkgs.python3}/bin/python3 ${./listeners.py} ${listenerSettings} "$@"
  '';

  healthConfig = {
    source_address = if config.my.proxy.coreNetns then config.my.proxy.coreTopology.host4 else "127.0.0.1";
    curl = "${pkgs.curl}/bin/curl";
    dig = "${pkgs.dnsutils}/bin/dig";
    socket = mihomoSock;
    snapshot_tool = "${../../../programs/clash/service/clash-verge-stage.py}";
    effective_directory = "/run/clash-verge-rev/effective";
    snapshot_required = config.programs.clash-verge.enable;
    digest_file = "/run/proxy-mode/config-digest";
    authorization_file = "/run/proxy-mode/authorized-config";
    listener_check = config.my.proxy.listenerCheck;
    runtime = "/run/proxy-mode";
    budget_seconds = config.my.proxy.healthBudgetSeconds;
    request_timeout_seconds = config.my.proxy.healthRequestTimeoutSeconds;
    dns_leaf_group = config.my.proxy.controlledSources && config.my.proxy.localDnssec && !config.my.proxy.proxyOnly
      && lib.any (lib.hasSuffix "#lf-dns") config.my.proxy.managedDnsTransport.dns.nameserver;
    session_urls = lib.optionals (config.my.proxy.controlledSources && config.my.proxy.localDnssec && !config.my.proxy.proxyOnly)
      (map (lib.removeSuffix "#lf-dns") (lib.filter (lib.hasSuffix "#lf-dns") config.my.proxy.managedDnsTransport.dns.nameserver));
    application_dns_check = lib.optionals config.services.resolved.enable [
      "${pkgs.systemd}/bin/systemctl" "start" "proxy-application-dns-probe.service"
    ];
    domestic_probe_url = domesticProbe;
    authorization_module = ./authorization.py;
    domestic_dns_check = lib.optionals (config.services.resolved.enable && domesticProbe != null) [
      "${pkgs.systemd}/bin/systemctl" "start" "proxy-domestic-dns-probe.service"
    ];
    dns_timeout_seconds = config.my.proxy.healthDnsTimeoutSeconds;
    urls = config.my.proxy.nodeProbeUrls;
    delay_primary = config.my.proxy.delayTestUrl;
    delay_fallback = config.my.proxy.delayTestFallbackUrl;
    mixed_port = port.mihomoMixed;
    dns_port = port.mihomoDns;
    require_tun = requireTun;
    fake_ip_net = config.my.proxy.fakeIpNet;
    tun_probe_name = tunProbeName;
    tun_probe_names = config.my.proxy.tunProbeNames;
    tun_route_classes = config.my.proxy.subscriptionAuditProtectedRoutes;

    expected = {
      managed-policy = builtins.hashString "sha256" (builtins.toJSON config.my.proxy.managedProfile);
      routing-mark = m.mihomoMark;
      mixed-port = port.mihomoMixed;
      port = 0;
      socks-port = 0;
      redir-port = 0;
      tproxy-port = if config.my.proxy.transparentGuestInterfaces == [ ] then 0 else port.mihomoTproxy;
      allow-lan = false;
      bind-address = "*";
      ipv6 = config.networking.enableIPv6;
      log-level = "info";

      tun = { enable = requireTun; file-descriptor = 0; }
        // lib.optionalAttrs requireTun (config.my.proxy.tunRouting // {
        device = config.my.proxy.tunDev;
        stack = config.my.proxy.tunStack;
        mtu = config.my.proxy.tunMtu;
        iproute2-table-index = config.my.proxy.routeTable;
        auto-route = true;
        auto-redirect = true;
        strict-route = true;
        dns-mode = "hijack";
        dns-hijack = lib.sort builtins.lessThan [ "any:53" "tcp://any:53" ];
        inet6-address = lib.optionals config.networking.enableIPv6 [ config.my.proxy.tunIpv6Address ];

        route-exclude-address = lib.sort builtins.lessThan (
          lib.optionals config.my.hardening.directEgress.privateRoutes (m.privateV4 ++ m.ulaV6 ++ m.linkLocalV6)
          ++ m.multicastV4 ++ [ "${m.limitedBroadcastV4}/32" ] ++ m.multicastV6
          ++ lib.optionals config.my.proxy.coreNetns (let t = config.my.proxy.coreTopology; in [ t.subnet4 t.subnet6 ]));

        route-address-set = [ ];
        route-exclude-address-set = [ ];
        route-address = [ ];
        include-interface = [ ];
        exclude-interface = [ ];
        include-uid = [ ];
        exclude-uid = lib.optional config.my.proxy.localDnssec m.uids.unbound;
        include-uid-range = [ ];
        exclude-uid-range = [ ];
      });
    };
  };

  healthSettings = pkgs.writeText "proxy-health.json" (builtins.toJSON healthConfig);
  activeHealthSettings = if config.my.proxy.runtimeRouting then "/run/proxy-mode/routing-active/health.json"
    else toString healthSettings;
  routingExpected = mode: healthConfig.expected // {
    managed-policy = builtins.hashString "sha256" (builtins.toJSON config.my.proxy.routingPolicyValues.${mode});
  };
  routingHealthSettings = lib.genAttrs [ "rules" "proxy-only" ] (mode:
    pkgs.writeText "routing-${mode}-health.json" (builtins.toJSON (healthConfig // {
      routing_state = "/run/proxy-mode/routing.json";
      routing_mode = mode;
      dns_leaf_group = mode == "rules" && healthConfig.dns_leaf_group;
      session_urls = lib.optionals (mode == "rules") healthConfig.session_urls;
      real_dns_port = if mode != "proxy-only" then null
        else if config.my.proxy.ipTunnel != null then port.coreRealDns else port.socketGuestDns;
      ip_tunnel = if mode == "proxy-only" then config.my.proxy.ipTunnelSettings else null;
      tun_route_classes = if mode == "proxy-only"
        then lib.mapAttrs (_: _: "proxy") healthConfig.tun_route_classes else healthConfig.tun_route_classes;
      expected = routingExpected mode;
    })));

  # Desktop consumers require the root supervisor's fresh readiness verdict,
  # then check live API invariants through their own forwarder. Snapshot files
  # and the internal controller remain private; only the supervisor verifies them.
  desktopHealthSettings = lib.genAttrs
    (if config.my.proxy.runtimeRouting then [ "rules" "proxy-only" ] else [ "default" ]) (mode:
      pkgs.writeText "proxy-desktop-${mode}-health.json" (builtins.toJSON {
        inherit (healthConfig) budget_seconds listener_check;
        expected = if mode == "default" then healthConfig.expected else routingExpected mode;
        socket = frontSock;
      }));

  # Single source of truth for "Clash is on": ../dns/upstream.nix calls the same script, so
  # the criterion cannot drift between the two supervisors.
  isClashOn = pkgs.writeShellScript "is-clash-on" ''
    # clash-verge.service also runs an on-demand root helper, so its state alone
    # is not "Clash is on": require the core inside the unit's cgroup, which a local
    # process cannot forge. Matching a process name would let any user process pin
    # the host in proxy mode with a dead core. The cgroup is read directly -- an
    # `is-active` would only add a D-Bus round-trip the cgroup already answers.
    for p in $(${pkgs.coreutils}/bin/cat /sys/fs/cgroup/system.slice/clash-verge.service/cgroup.procs 2>/dev/null); do
      ${pkgs.gnugrep}/bin/grep -qa -e 'bin/verge-mihomo' -e 'bin/.verge-mihomo-wrapped' "/proc/$p/cmdline" 2>/dev/null && exit 0
    done
    exit 1
  '';

  # One pass: decide the mode from the Clash/TUN/node state, then make nft match it.
  modeScript = pkgs.writeShellScript "proxy-mode-sync" ''
    RULES=${rules}
    B_RULES=${blockedRules}
    IS_CLASH_ON=${isClashOn}
    CORE_ROUTES=${if config.my.proxy.coreNetns then config.my.proxy.coreNetworkCommand else "${pkgs.coreutils}/bin/true"}
    STATUS=/run/proxy-mode/status
    REASON=/run/proxy-mode/reason
    MARKER=/run/proxy-mode/core-seen
    CAPTURE=/run/proxy-mode/probe.log
    NFT=${pkgs.nftables}/bin/nft
    IP=${pkgs.iproute2}/bin/ip
    SYSTEMCTL=${pkgs.systemd}/bin/systemctl
    GREP=${pkgs.gnugrep}/bin/grep
    SED=${pkgs.gnused}/bin/sed
    JQ=${pkgs.jq}/bin/jq
    CAT=${pkgs.coreutils}/bin/cat
    TIMEOUT=${pkgs.coreutils}/bin/timeout
    SLEEP=${pkgs.coreutils}/bin/sleep
    CURL=${pkgs.curl}/bin/curl
    DIG=${pkgs.dnsutils}/bin/dig
    FLOCK=${pkgs.util-linux}/bin/flock
    RULE_CHECK=${rulesCheck}
    EXPECTED=${expectedRules}
    INTENT=/run/proxy-mode/intent
    VERSION=/run/proxy-mode/version
    STATIC_RULES=${staticRules}
    LEGACY_RULES=${legacyRules}
    AUTHORIZATION=${config.my.proxy.authorizationCommand}
    AUTHORIZATION_ENABLED=${if config.my.proxy.authorizationLeaseSeconds == null then "0" else "1"}
    LEASE_SECONDS=${builtins.toJSON config.my.proxy.authorizationLeaseSeconds}
    ARP_SYNC=${lib.escapeShellArg (if config.my.hardening.arp.command == null then "" else toString config.my.hardening.arp.command)}

    TUNDEV=${config.my.proxy.tunDev}
    REQUIRE_TUN=${if requireTun then "1" else "0"}
    MIHOMO_MIXED=${toString port.mihomoMixed}
    MIHOMO_DNS=${toString port.mihomoDns}
    GOST_REDIRECT=${toString port.gostRedirect}
    MIHOMO_SOCK=${mihomoSock}
    CORE_MARK=${coreMark}
    # Range the probe's source port is taken from. A single fixed port cannot be
    # reused: once curl closes, that port sits in TIME_WAIT (~60 s) and the next
    # bind fails, which would blackhole a healthy Mode B. Each probe takes a fresh
    # port from the range, and `%{local_port}` reports which so the log line can be
    # matched exactly. Kept below the ephemeral range and away from vetted ports.
    PROBE_LOCAL_RANGE=15010-15090
    TUN_PROBE_NAME=${tunProbeName}
    FAKE_IP_NET=${config.my.proxy.fakeIpNet}
    ROUTE_TABLE=${toString config.my.proxy.routeTable}
    HEALTH=${pkgs.python3}/bin/python3
    HEALTH_SCRIPT=${./health.py}
    HEALTH_SETTINGS=${activeHealthSettings}
    ${lib.optionalString config.my.proxy.runtimeRouting ''ROUTING=${config.my.proxy.routingCommand}''}
    SETSID=${pkgs.util-linux}/bin/setsid

    umask 022
    mkdir -p /run/proxy-mode
    exec 9>/run/proxy-mode/lock
    atomic_write() {
      printf '%s\n' "$2" > "$1.$$" && mv -f "$1.$$" "$1"
    }
    # Publish before mode decisions or helper startup, outside the helper's
    # restricted /proc view. A missing identity must not be silently invented.
    ${pkgs.python3}/bin/python3 ${../../../programs/clash/service/clash-verge-stage.py} initialize-boot /run/proxy-mode || exit 1
    lock_state() { $FLOCK -x -w 10 9 || exit 1; }
    unlock_state() { forget_snapshot; $FLOCK -u 9; }
    read_intent() {
      local requested
      requested=$($CAT "$INTENT" 2>/dev/null) || requested=auto
      ${lib.optionalString config.my.proxy.runtimeRouting ''
      # An interrupted recovery from an older generation retains proxy intent.
      if [ "$requested" = source-recovery ]; then requested=proxy; fi
      ''}
      echo "$requested"
    }
    ${lib.optionalString config.my.proxy.controlledSources ''
    source_schedule() {
      ${pkgs.python3}/bin/python3 ${../../../programs/clash/service/source_refresh.py} schedule \
        ${config.programs.clash-verge.package.stageSettings} "$@" || true
    }
    ''}
    read_version() { $CAT "$VERSION" 2>/dev/null || echo 0; }
    core_stamp() { $CAT /sys/fs/cgroup/system.slice/clash-verge.service/cgroup.procs 2>/dev/null || true; }
    publish_state() {
      version=$(read_version)
      if [ "''${1:-policy}" = policy ]; then version=$((version + 1)); fi
      revision=$($JQ -r '.revision // 0' /run/proxy-mode/state.json 2>/dev/null || echo 0)
      atomic_write "$VERSION" "$version" || return 1
      atomic_write "$STATUS" "$mode" || return 1
      atomic_write "$REASON" "$reason" || return 1
      $JQ -n --arg mode "$mode" --arg reason "$reason" --argjson version "$version" \
        --argjson routing "$([ -z "''${ROUTING:-}" ] && echo '{}' || "$ROUTING" view)" \
        --argjson lease "''${LEASE_SECONDS:-null}" \
        --argjson revision "$((revision + 1))" --arg updated "$(${pkgs.coreutils}/bin/date -Iseconds)" \
        '$routing + {mode:$mode,reason:$reason,version:$version,policy_version:$version,revision:$revision,updated_at:$updated,authorization_lease_seconds:$lease}' > /run/proxy-mode/state.json.$$
      mv -f /run/proxy-mode/state.json.$$ /run/proxy-mode/state.json
      [ "''${1:-policy}" != policy ] || request_verify
    }

    mode=""
    reason=""           # "no-tun" | "direct-mode" | "node" | "core-gone" | "obs" | ""
    obs_fails=0         # consecutive `obs` / `dns` health failures (3-strike hysteresis)
    fails=0
    once=0
    changed=1
    nap_pid=""
    interval=2          # start fast; the loop backs off once the state is stable
    MIN=2
    last_full_repair=-30
    # Backstop cap: short while enforcement is (or should be) loaded -- a missed
    # event (or a base-ruleset reload, which recreates the fragment chains empty)
    # must not leave the guard off for long -- and long when settled in direct,
    # where nothing is loaded and events still drive transitions.
    cap_for() {
      if [ "$1" = direct ] && [ -z "$ARP_SYNC" ]; then
        echo 60
      else
        echo 5
      fi
    }
    [ "''${1:-}" = "--once" ] && once=1

    log() { echo "proxy-mode: $*" >&2; }
    loud() {
      echo "proxy-mode: $*" >&2
      ${pkgs.systemd}/bin/systemd-cat -t proxy-mode -p err ${pkgs.coreutils}/bin/echo "proxy-mode: $*" || true
    }

    # Event-driven sleep: `proxy-net-wake.service` sends SIGWINCH when the Clash
    # core appears or disappears, and a trapped SIGWINCH interrupts `wait`, so the
    # loop reacts at once instead of waiting out the backstop interval. SIGWINCH
    # (not SIGUSR1) because its default action is to be ignored: a wake that lands
    # before this trap is installed cannot kill the process. The interval adapts
    # while the mode is settled, bounded by cap_for (5 s enforced / 60 s direct).
    trap 'woken=1' WINCH
    # woken=1 when a wake arrived (during a nap OR while a pass was running). The
    # wait's exit status is not discarded, and the flag is consumed at the next
    # decision round, so a wake landing inside a pass is not lost.
    woken=1
    nap() {
      ${pkgs.coreutils}/bin/sleep "$1" &
      nap_pid=$!
      if [ "$woken" = 0 ]; then wait "$nap_pid" 2>/dev/null; rc=$?; else rc=129; fi
      kill "$nap_pid" 2>/dev/null || true
      nap_pid=""
      [ "$rc" -gt 128 ] && woken=1
    }

    # Ask for an immediate verification. A start issued here rides the activation
    # transaction when a rebuild restarts proxy-mode, but that is harmless now:
    # nftables-verify reports through the journal + /run/netsec/failed and always
    # exits 0 (see ../firewall/verify.nix), so it can never fail the rebuild.
    # Immutable queue entries survive requests made while the verifier is busy.
    # Its .path rechecks DirectoryNotEmpty after every completion.
    request_verify() { ${verifyRequest}; }

    # A mode change must reach the other two supervisors at once: gost keys its
    # whole state off /run/proxy-mode/status and backs off to 30 s in the steady
    # state, so without this it could keep forwarding through a core that just
    # went blocked (a leak from a direct-mode core) or stay closed through a
    # recovery. SIGWINCH interrupts each loop's sleep (same mechanism as
    # proxy-net-wake), and each loop still re-decides on its own.
    request_wake() {
      for u in gost-relay.service dns-upstream.service; do
        $SYSTEMCTL kill -s WINCH --kill-whom=main "$u" 2>/dev/null || true
      done
    }

    # --- live state readers -------------------------------------------------
    # Table existence is not enough: `nft flush table` leaves an empty table that
    # would pass, so require the redirect rule itself.
    rules_loaded() { $NFT list table ip lf_proxymode_nat 2>/dev/null | $GREP -q "redirect to :$GOST_REDIRECT"; }
    rules_loaded6() { $NFT list table ip6 lf_proxymode_nat 2>/dev/null | $GREP -q "redirect to :$GOST_REDIRECT"; }
    chains_loaded() { $NFT list chain inet lf_filter proxymode_drops 2>/dev/null | $GREP -q 'counter'; }
    # The Mode-C guard is filled iff its terminal drop is present. nft prints it
    # as "counter packets N bytes N drop", so match the verdict, not the counter.
    guard_active() { $NFT list chain inet lf_filter lf_blocked_guard 2>/dev/null | $GREP -q 'drop'; }
    # Guest/VM refusal lives in proxymode_forward (both fragments fill it); an
    # emptied chain would silently drop the refusal, so probe its content too.
    forward_loaded() { $NFT list chain inet lf_filter proxymode_forward 2>/dev/null | $GREP -q 'counter'; }
    # Percent-encode a Clash name for a URL path: a group/node name is profile
    # data and may contain '/', '?', '#', '%' or a space, any of which would
    # otherwise make the request fail and be misread as a dead node.
    urlenc() { printf '%s' "$1" | $JQ -sRr @uri; }

    # Anything that would still redirect or drop after a tear-down.
    rules_present() {
      guard_active && return 0
      rules_loaded && return 0
      rules_loaded6 && return 0
      chains_loaded && return 0
      forward_loaded && return 0
      return 1
    }
    # Live-policy snapshot: taken at most once per pass and reused until an
    # apply invalidates it. The same dump decides both "already correct" and the
    # post-condition, so a stable pass no longer dumps the ruleset twice.
    source ${../firewall/rules-snapshot.sh}
    SNAPSHOT=""
    snapshot_ok=0
    take_snapshot() {
      new_rules_snapshot /run/proxy-mode check || return 1
      if $NFT -j list ruleset > "$SNAPSHOT"; then
        snapshot_ok=1
      else
        forget_snapshot
        return 1
      fi
    }
    forget_snapshot() { snapshot_ok=0; remove_rules_snapshot; }
    matches() {
      case "$1" in proxy|blocked|direct) ;; *) return 1 ;; esac
      [ "$snapshot_ok" = 1 ] || take_snapshot
      [ "$snapshot_ok" = 1 ] || return 1
      "$RULE_CHECK" compare "$EXPECTED/$1.json" "$SNAPSHOT" ${lib.optionalString config.my.proxy.runtimeRouting ''&& "$ROUTING" check "$1"''}
    }
    static_matches() {
      [ "$snapshot_ok" = 1 ] || take_snapshot
      [ "$snapshot_ok" = 1 ] || return 1
      "$RULE_CHECK" compare-static "$EXPECTED/direct.json" "$SNAPSHOT"
    }
    trap remove_rules_snapshot EXIT

    # A silent teardown failure leaves the user offline with Clash closed, so every
    # step is checked: absent tables are fine, a failed delete or flush is not.
    rules_off() {
      $NFT -f - <<'NFT_OFF'
destroy table ip lf_proxymode_nat
destroy table ip6 lf_proxymode_nat
${policyGuard.reset}
flush chain inet lf_filter proxymode_drops
flush chain inet lf_filter proxymode_tail
flush chain inet lf_filter proxymode_forward
flush chain inet lf_filter lf_blocked_guard
flush chain inet lf_filter lf_guest_guard
flush chain inet lf_filter lf_host_egress_guard
flush chain inet lf_filter lf_host_direct
flush chain inet lf_filter lf_tailnet_guard
flush set inet lf_filter dns_plaintext4
${config.my.proxy.authorizationReset}
${lib.optionalString (config.my.proxy.transparentGuestInterfaces != [ ]) "flush chain ip lf_mangle proxymode_tproxy"}
NFT_OFF
      $CORE_ROUTES routes-off
    }
    # The fragments tear down and re-create their own NAT tables, so each is a
    # single atomic transaction.
    rules_on_proxy() { $NFT -f "$RULES" ${lib.optionalString config.my.proxy.runtimeRouting ''&& "$ROUTING" enforce proxy''}; }
    rules_on_blocked() { $NFT -f "$B_RULES" ${lib.optionalString config.my.proxy.runtimeRouting ''&& "$ROUTING" enforce blocked''}; }

    # Caller holds the policy lock: never recursively run --reload here.
    load_complete() {
      local target="$1" transaction=/run/proxy-mode/reload.$$.nft rc=0
      local -a sources=("$STATIC_RULES")
      ${lib.optionalString config.my.proxy.runtimeRouting ''
      "$ROUTING" initialize "$target" || log "routing selection unavailable during $target policy load"
      ''}
      # Keep the blocked policy until direct routing is restored. The static
      # nftables transaction cannot remove the host's policy-routing rules.
      if [ "$target" = direct ]; then
        $CORE_ROUTES routes-off || return 1
      fi
      case "$target" in
        blocked) sources+=("$B_RULES") ;;
        proxy) sources+=("$RULES") ;;
      esac
      # Never persist migration intent: later reloads/repairs must not remove a
      # foreign table that happens to reuse an old name. Cleanup and replacement
      # policy commit together, so rejection retains both the old and owned rules.
      if [ "''${2:-}" = migrate-legacy ]; then sources+=("$LEGACY_RULES"); fi
      if $CAT "''${sources[@]}" > "$transaction"; then
        $NFT -f "$transaction" || rc=$?
      else
        rc=$?
      fi
      rm -f "$transaction"
      forget_snapshot
      ${lib.optionalString config.my.proxy.runtimeRouting ''[ "$rc" != 0 ] || "$ROUTING" enforce "$target" || rc=$?''}
      [ "$rc" = 0 ] && matches "$target" || return 1
      if [ -n "$ARP_SYNC" ]; then "$ARP_SYNC" sync >/dev/null || true; fi
      return 0
    }
    safety_mode() {
      if $IS_CLASH_ON || [ "$(read_intent)" = proxy ] \
         || { [ "$(read_intent)" != direct ] && [ -f "$MARKER" ]; }; then
        echo blocked
      else
        echo direct
      fi
    }

    # Report readiness after verifying A or C. Full B health can depend on
    # listeners ordered after this unit.
    initial_policy() {
      lock_state
      forget_snapshot
      mode=$(safety_mode); reason=reload
      if [ "$mode" = direct ]; then reason=""; fi
      if ! load_complete "$mode"; then unlock_state; return 1; fi
      publish_state
      verified_version=$(read_version)
      unlock_state
      request_wake
      pass_verified=1
      changed=1
    }

    # Shared with the verifier: include actual unmarked IPv6 route selection,
    # not just the presence of the TUN table and a rule referring to it.
    tun_ready() {
      ${tunCheck}
    }
    # Full health gate. All three must hold:
    #  (1) the core answers a proxied HTTP request through its mixed port, and
    #      its exact-source log or live connection record proves a remote leaf --
    #      this catches direct mode, a rule profile that is effectively
    #      all-DIRECT, and a group/slot selected to DIRECT;
    #  (2) the successful request's recorded leaf matches the current policy
    #      graph. No delay API call may mutate node health or automatic selection;
    #  (3) a fake-ip answer is reachable through the TUN, proving the TUN really
    #      carries packets (shape-only checks cannot see a stuck sing-tun).
    # health_gate names the failing layer for the reason file:
    #   obs   = an HTTP request or its route observation could not be completed
    #   route = the probe was routed DIRECT or REJECT (no real node)
    #   tun   = the TUN does not carry packets
    stop_health() {
      [ -n "''${health_pid:-}" ] || return 0
      kill -TERM -- "-$health_pid" 2>/dev/null || true
      for _ in 1 2 3 4 5; do
        kill -0 "$health_pid" 2>/dev/null || break
        $SLEEP 0.1
      done
      kill -KILL -- "-$health_pid" 2>/dev/null || true
      wait "$health_pid" 2>/dev/null || true
      health_pid=""
    }
    health_ok() {
      local output=/run/proxy-mode/health.$$.json next_fast=$SECONDS
      local health_digest health_at proof_time proof_id proof_epoch
      read -r proof_time _ < /proc/uptime
      proof_id=$($CAT /proc/sys/kernel/random/uuid)
      proof_epoch=$($CAT /run/proxy-mode/authorization-epoch 2>/dev/null || echo 0)
      health_digest=$decision_digest
      health_at=$(${pkgs.coreutils}/bin/date +%s)
      health_gate=obs
      ${lib.optionalString config.my.proxy.runtimeRouting ''
      if ! "$ROUTING" probe-ready; then health_gate=config; return 1; fi
      ''}
      # The operator lock covers core replacement. Sample it without holding
      # it during the probe so an off or reload request can still interrupt us.
      if ! $FLOCK -sn /run/proxy-mode/operator.lock ${pkgs.coreutils}/bin/true; then
        $SLEEP 0.5
        health_gate=interrupted; return 1
      fi
      $SETSID "$HEALTH" "$HEALTH_SCRIPT" "$HEALTH_SETTINGS" > "$output" 9>&- &
      health_pid=$!
      while kill -0 "$health_pid" 2>/dev/null; do
        if [ "$woken" = 1 ]; then
          stop_health; rm -f "$output"
          health_gate=interrupted; return 1
        fi
        if [ "$SECONDS" -ge "$next_fast" ]; then
          next_fast=$((SECONDS + 1))
          if ! $FLOCK -sn /run/proxy-mode/operator.lock ${pkgs.coreutils}/bin/true; then
            stop_health; rm -f "$output"
            health_gate=interrupted; return 1
          fi
          if ! $IS_CLASH_ON; then
            stop_health; rm -f "$output"
            health_gate=core-gone; return 1
          fi
          if [ "$REQUIRE_TUN" = 1 ] && ! tun_ready; then
            stop_health; rm -f "$output"
            health_gate=no-tun; return 1
          fi
          fast=$($TIMEOUT 2 "$HEALTH" "$HEALTH_SCRIPT" "$HEALTH_SETTINGS" --fast 9>&- 2>/dev/null || true)
          fast_reason=$(printf '%s' "$fast" | $JQ -r '.reason // "obs"')
          case "$fast_reason" in direct-mode|config)
            stop_health; rm -f "$output"
            health_gate="$fast_reason"; return 1 ;;
          esac
        fi
        $SLEEP 0.2
      done
      wait "$health_pid" 2>/dev/null || true
      health_pid=""
      if [ "$woken" = 1 ] || [ "$(core_stamp)" != "$decision_core" ] ||
         ! $FLOCK -sn /run/proxy-mode/operator.lock ${pkgs.coreutils}/bin/true; then
        rm -f "$output"
        health_gate=interrupted; return 1
      fi
      if ! $JQ -e 'has("ok") and has("duration_ms")' "$output" >/dev/null 2>&1; then
        rm -f "$output"; return 1
      fi
      if [ "''${health_streak_digest:-}" != "$health_digest" ] || [ "''${health_streak_version:-}" != "$decision_version" ]; then
        health_failure_streak=0
        startup_data_failure_streak=0
        health_streak_digest="$health_digest"
        health_streak_version="$decision_version"
      fi
      if $JQ -e '.ok == true' "$output" >/dev/null; then
        health_failure_streak=0
      else
        health_failure_streak=$(( ''${health_failure_streak:-0} + 1 ))
      fi
      if $JQ -e '.startup_data_failure == true' "$output" >/dev/null; then
        startup_data_failure_streak=$(( ''${startup_data_failure_streak:-0} + 1 ))
      else
        startup_data_failure_streak=0
      fi
      $JQ --arg digest "$health_digest" --argjson at "$health_at" \
        --argjson monotonic "$proof_time" --arg proof "$proof_id" --argjson version "$decision_version" \
        --argjson streak "$health_failure_streak" --argjson startup_streak "$startup_data_failure_streak" \
        --arg epoch "$proof_epoch" \
        --arg boot "$($CAT /run/proxy-mode/boot-id)" \
        '. + {digest:$digest, at:$at, boot_id:$boot,monotonic_seconds:$monotonic,proof_id:$proof,policy_version:$version,authorization_epoch:$epoch,failure_streak:$streak,startup_data_failure_streak:$startup_streak}' "$output" > "$output.stamped"
      mv -f "$output.stamped" /run/proxy-mode/health.json
      rm -f "$output"
      health_gate=$($JQ -r '.reason' /run/proxy-mode/health.json)
      $JQ -e '.ok == true' /run/proxy-mode/health.json >/dev/null
    }
    # mihomo "direct" mode routes everything DIRECT even with the TUN up, so the
    # core-mark exemption would let every flow leave from the real address. Read
    # the live mode from the control socket (loopback, so it works while blocked)
    # and refuse to trust it; "rule"/"global" are fine ("global" still uses the
    # group). A read failure is not judged here, so a starting core is not cut.
    mode_direct() {
      cfg=$($TIMEOUT 2 $CURL --noproxy "*" --max-time 1 -s --unix-socket "$MIHOMO_SOCK" http://localhost/configs 2>/dev/null || true)
      case "$cfg" in
        *'"mode":"direct"'*) return 0 ;;
        *) return 1 ;;
      esac
    }

    sync_pass() {
      pass_verified=0
      proof_renew=0
      lock_state
      # A snapshot belongs to this lock window, never to the process lifetime.
      forget_snapshot
      mode=$($CAT "$STATUS" 2>/dev/null || true)
      reason=$($CAT "$REASON" 2>/dev/null || true)
      prev="$mode"
      intent=$(read_intent)
      decision_core=$(core_stamp)
      decision_digest=""
      prearmed=0

      if ! static_matches; then
        if [ "$((SECONDS - last_full_repair))" -lt 30 ]; then
          mode=unenforced; reason=policy-repair-backoff
          publish_state observation
          unlock_state
          changed=1
          return 1
        fi
        last_full_repair=$SECONDS
        mode=$(safety_mode); reason=policy-repair
        if ! load_complete "$mode"; then
          mode=unenforced; reason=policy-repair-failed
          publish_state observation
          request_verify; request_wake
          unlock_state
          changed=1
          return 1
        fi
        publish_state
        request_wake
        prearmed=1
      fi

      # No fragment at all (proxyKillSwitch off): the host is unenforced direct.
      if [ ! -s "$RULES" ]; then
        rules_off
        mode=direct; reason=""
        publish_state
        unlock_state
        changed=1
        return 0
      fi

      local arp_before arp_after arp_ok=1
      if [ -n "''${ARP_SYNC:-}" ]; then arp_before=$("$ARP_SYNC" sync) || arp_ok=0; fi

      if $IS_CLASH_ON; then
        # Remember that the core ran this boot; the marker is what turns "core
        # vanished" into a block instead of a silent drop to Mode A.
        : > "$MARKER" 2>/dev/null || true
        # Enforcement vanished (a base-ruleset reload destroys lf_filter): fail
        # closed at once, before the health gate, so the host is never left with
        # no guard, no killswitch and no :33333 redirect while the gate decides.
        # The gate below then upgrades to proxy if the path is healthy.
        if ! rules_present; then
          if rules_on_blocked; then
            mode=blocked; reason="reload"; prearmed=1
            publish_state
            request_wake
          fi
        fi
        decision_version=$(read_version)
        # tun_ok is 1 unless we require a TUN and it is not ready. In the plain
        # HTTP-proxy model (REQUIRE_TUN=0) a missing TUN is not a failure.
        # Serialize host route changes with off and reload; a discarded health
        # observation must not reinstall proxy routing after A was committed.
        tun_ok=1
        if [ "$intent" = proxy ] && ! $CORE_ROUTES routes-on; then tun_ok=0; fi
        ${lib.optionalString config.my.proxy.runtimeRouting ''
        if [ "$intent" = proxy ]; then
          # A staged snapshot can change the transport boundary while C is
          # already active. Verify/reinstall it before any network probe.
          transport_mode=blocked
          if [ "$mode" = proxy ]; then transport_mode=proxy; fi
          if ! "$ROUTING" enforce "$transport_mode"; then tun_ok=0; fi
        fi
        ''}
        decision_digest=$($CAT /run/proxy-mode/config-digest 2>/dev/null || true)
        unlock_state
        if [ "$REQUIRE_TUN" = "1" ] && ! tun_ready; then tun_ok=0; fi

        if [ "$intent" = direct ]; then
          want=blocked; want_reason="stopping"
        elif [ "$intent" != proxy ]; then
          # Mode B needs an explicit clash-on. A core started by the GUI (or
          # restored by the helper) while the boot default is Mode A stays
          # blocked instead of silently turning the host into Mode B.
          want=blocked; want_reason="unauthorized-core"
        elif [ "$arp_ok" != 1 ]; then
          want=blocked; want_reason="arp-binding"
        elif [ "$tun_ok" = "1" ]; then
          # A TUN-up core in "direct" mode leaks exactly like a node-dead one:
          # everything leaves DIRECT under the exempt mark. The mode is read over
          # loopback, so it can be checked whether or not the guard is up.
          if mode_direct; then
            want=blocked; want_reason="direct-mode"
          else
            # The core keeps its egress in blocked, so this runs every pass and
            # the node is picked up the moment it recovers. The reason names the
            # failing layer (a/b/c) for `proxy-status`.
            #
            # Hysteresis for the `obs` and `dns` layers: a transient inability
            # to read the route, or a cold DNSSEC lookup that outlives one
            # probe, must not blackhole the whole host; require three
            # consecutive soft failures. Route/config/TUN failures keep an
            # immediate cut. HTTP timeouts are observations; a full successful
            # request and its exact-source route proof establish the selected node
            # without /delay.
            if health_ok; then
              proof_renew=1
              obs_fails=0
              want=proxy; want_reason=""
            else
              if [ "$health_gate" = interrupted ]; then
                changed=1; woken=1
                return 0
              fi
              case "$health_gate" in
                obs|dns|node|application|controller-unavailable) obs_fails=$((obs_fails + 1)) ;;
                *) obs_fails=0 ;;
              esac
              want_reason="$health_gate"
              if [ "$health_gate" = rules-degraded ]; then
                # Validate the old lease again below, under the commit lock.
                want=proxy
              elif [ "$mode" = "proxy" ] && [ "$obs_fails" -gt 0 ] && [ "$obs_fails" -lt 3 ]; then
                want=proxy; want_reason="$health_gate:$obs_fails/3"
              else
                want=blocked
                [ "$health_gate" != obs ] || want_reason="obs:3/3"
              fi
            fi
          fi
        else
          want=blocked; want_reason="no-tun"
        fi
      else
        decision_version=$(read_version)
        # Only an explicit off request clears boot-scoped proxy intent. Losing
        # the whole helper/core cgroup must never be mistaken for that request.
        if [ "$intent" = proxy ] || { [ "$intent" != direct ] && [ -f "$MARKER" ]; }; then
          want=blocked; want_reason="core-gone"
        else
          want=direct; want_reason=""
        fi
        unlock_state
      fi

      # Probes run without the commit lock. A reload, an operator request or a
      # new core invalidates their result; retry against the new state instead.
      lock_state
      # Probes released the lock; foreign writers need not update our version.
      forget_snapshot
      if [ "$(read_version)" != "$decision_version" ] || [ "$(read_intent)" != "$intent" ] \
         || { [ "$proof_renew" = 1 ] && [ "$($CAT /run/proxy-mode/config-digest 2>/dev/null || true)" != "$decision_digest" ]; } \
         || [ "$(core_stamp)" != "$decision_core" ]; then
        unlock_state
        changed=1; woken=1
        return 0
      fi
      if [ "$want" = proxy ] && [ "$REQUIRE_TUN" = 1 ] && ! tun_ready; then
        want=blocked; want_reason=no-tun
      fi
      if [ "$want_reason" = rules-degraded ] &&
         { [ "$mode" != proxy ] || [ "$AUTHORIZATION_ENABLED" != 1 ] || ! "$AUTHORIZATION" --existing-rules-lease; }; then
        want=blocked; want_reason=authorization-expired
      fi
      if [ "$want" = proxy ] && [ -n "''${ARP_SYNC:-}" ]; then
        if ! arp_after=$("$ARP_SYNC" sync) || [ "$arp_after" != "$arp_before" ]; then
          want=blocked; want_reason=arp-binding; proof_renew=0
        fi
      fi
      ${lib.optionalString config.my.proxy.runtimeRouting ''
      if [ "$want" = proxy ] && [ "$proof_renew" = 1 ] && ! "$ROUTING" applied; then
        want=blocked; want_reason=config; proof_renew=0
      fi
      ''}

      # Apply the fragment the want requires whenever it differs or its
      # post-condition does not hold (e.g. a manual nft flush emptied it).
      # The pre-arm already changed `mode`; force one apply so STATUS/REASON are
      # written and verify/wake fire even when the gate reaches the same verdict.
      if [ "$prearmed" = "1" ] || [ "$want" != "$mode" ] || ! matches "$want"; then
        applied=0
        case "$want" in
          proxy)
            if rules_on_proxy; then applied=1; fi
            ;;
          blocked)
            if rules_on_blocked; then applied=1; fi
            ;;
          direct)
            # Direct is the static policy, not "whatever is left after a
            # teardown": `rules_off` flushes chains that also carry static
            # rules (lf_host_egress_guard), so the verified document would
            # never match. Reinstall the static text and compare it instead.
            if load_complete direct; then applied=1; fi
            ;;
        esac
        # Any apply (even a failed one) may have changed the kernel; the next
        # comparison must read it afresh instead of reusing the stale snapshot.
        forget_snapshot
        if [ "$applied" = "1" ]; then
          mode="$want"; reason="$want_reason"
          if [ -n "$prev" ] && [ "$mode" != "$prev" ]; then
            log "mode $prev -> $mode''${reason:+ ($reason)}"
          elif [ "$want" = "blocked" ] && [ -n "$reason" ]; then
            log "blocked ($reason)"
          fi
        else
          # Never record a mode that was not entered: `unenforced` is the state
          # for "Clash is on, enforcement is not loaded".
          loud "could not apply the $want fragment; enforcement absent"
          mode=unenforced; reason="$want_reason"
        fi
        if [ "$applied" = 1 ]; then publish_state; else publish_state observation; fi
        if [ "$mode" != "$prev" ] || [ "$prearmed" = 1 ]; then
          request_verify
          request_wake
        fi
      elif [ "$reason" != "$want_reason" ]; then
        reason="$want_reason"
        publish_state observation
      fi

      # Hysteresis never renews a lease. A complete fresh proof is consumed
      # once under the same lock as the policy and operator generation.
      if [ "''${AUTHORIZATION_ENABLED:-0}" = 1 ] && [ "$mode" = proxy ] && [ "$proof_renew" = 1 ]; then
        if ! "$AUTHORIZATION" "$decision_version"; then
          rules_on_blocked
          forget_snapshot
          mode=blocked; reason=authorization-failed
          publish_state
          request_wake
        fi
      fi

      # Post-condition of the recorded mode; a mismatch keeps the user offline
      # after closing Clash, so fail visibly through the verifier unit.
      ok=1
      if [ "$mode" = "proxy" ] || [ "$mode" = "blocked" ]; then
        matches "$mode" || ok=0
      elif [ "$mode" = "unenforced" ]; then
        # Always loud: Clash is on and our enforcement is not loaded.
        ok=0
      else
        rules_present && ok=0
      fi
      if [ "$ok" = "1" ]; then
        fails=0
        pass_verified=1
        verified_version=$(read_version)
      else
        # A successful fragment transaction is not proof of a complete policy.
        if [ "$mode" != unenforced ]; then
          mode=unenforced; reason=policy-mismatch
          publish_state observation
          request_wake
        fi
        fails=$((fails + 1))
        if [ "$fails" = "1" ] || [ $((fails % 6)) -eq 0 ]; then
          loud "nft state does not match mode $mode (attempt $fails)"
          request_verify
        fi
      fi

      # Back off only when the mode is stable and the post-condition holds; any
      # change or mismatch keeps the loop at the fast interval.
      if [ "$mode" != "$prev" ] || [ "$ok" != "1" ] || [[ "$reason" = *:[12]/3 || "$reason" = rules-degraded ]]; then
        changed=1
      else
        changed=0
      fi
      unlock_state
      if [ "$want_reason" = stopping ]; then
        # Queue a helper stop for direct intent without holding the policy lock.
        (
          umask 077
          exec 8>>/run/proxy-mode/operator.lock
          if $FLOCK -n 8 && [ "$(read_intent)" = direct ]; then
            $SYSTEMCTL stop --no-block clash-verge.service
          fi
        ) || true
      fi
      ${lib.optionalString config.my.proxy.controlledSources ''
      if [ -f /run/proxy-mode/source-refresh-pending ] || [ -f /run/proxy-mode/source-gui-request.json ] \
         || [ -f /run/proxy-mode/source-desktop-refresh.json ] \
          || [ -f /run/proxy-mode/source-refresh-stable.json ]; then
        if [ "$ok" = 1 ]; then
          source_schedule
        else
          source_schedule unverified
        fi
      fi
      ''}
      [ "$ok" = 1 ]
    }

    case "''${1:-}" in
      --reload|--migrate-legacy)
        lock_state
        mode=$(safety_mode); reason=reload
        migration=""
        if [ "$1" = --migrate-legacy ]; then migration=migrate-legacy; fi
        if ! load_complete "$mode" "$migration"; then
          unlock_state
          exit 1
        fi
        publish_state
        ${pkgs.python3}/bin/python3 ${../firewall/alerts.py} clear nftables.service
        unlock_state
        request_wake
        $SYSTEMCTL kill -s WINCH --kill-whom=main proxy-mode.service 2>/dev/null || true
        exit 0
        ;;
      --request-proxy|--request-direct|--revoke-source)
        lock_state
        if [ "$1" = --request-direct ]; then intent=direct; else intent=proxy; fi
        atomic_write "$INTENT" "$intent" || exit 1
        # Arm C before starting/stopping the core. GUI/start failures leave the
        # requested proxy state protected; off becomes A only after core exit.
        if ! load_complete blocked; then unlock_state; exit 1; fi
        mode=blocked; reason=request
        if [ "$1" = --revoke-source ]; then
          # The source broker holds operator, source and authority locks.
          # Invalidate health's authorization while still holding the policy
          # lock, so a new probe cannot reopen B before the old core stops.
          rm -f /run/proxy-mode/authorize-window /run/proxy-mode/authorized-config \
            /run/proxy-mode/config-digest /run/proxy-mode/effective-config-sha256 \
            /run/proxy-mode/effective-config.json /run/proxy-mode/health.json || exit 1
          reason=source-revoked
        fi
        publish_state
        ${lib.optionalString config.my.proxy.runtimeRouting ''
        if [ "$1" = --revoke-source ]; then "$ROUTING" stop-ip-tunnel || exit 1; fi
        ''}
        unlock_state
        request_wake
        exit 0
        ;;
    esac

    ready_sent=0
    while true; do
      woken=0
      pass_verified=0
      if [ "$ready_sent" = 0 ] && [ "$once" = 0 ] && [ -n "''${NOTIFY_SOCKET:-}" ]; then
        initial_policy
      else
        sync_pass
      fi
      pass_rc=$?
      [ "$once" = "1" ] && exit "$pass_rc"
      if [ "$pass_verified" = 1 ]; then
        lock_state
        if [ "$(read_version)" != "$verified_version" ]; then
          unlock_state
          continue
        fi
        read -r monotonic _ < /proc/uptime
        $JQ -n --argjson monotonic "$monotonic" --arg mode "$mode" \
          --argjson version "$(read_version)" \
          '{monotonic_seconds:$monotonic,mode:$mode,policy_version:$version}' > /run/proxy-mode/heartbeat.json.$$
        mv -f /run/proxy-mode/heartbeat.json.$$ /run/proxy-mode/heartbeat.json
        unlock_state
        if [ -n "''${NOTIFY_SOCKET:-}" ]; then
          if [ "$ready_sent" = 0 ] && ${pkgs.systemd}/bin/systemd-notify --ready --status="Policy verified: $mode"; then
            ready_sent=1
            woken=1
          fi
          ${pkgs.systemd}/bin/systemd-notify WATCHDOG=1
        fi
      fi
      if [ "$changed" = 1 ]; then
        interval=$MIN
      else
        cap=$(cap_for "$mode")
        [ "$interval" -lt "$cap" ] && interval=$(( interval * 2 > cap ? cap : interval * 2 ))
      fi
      # A wake that arrived during the pass must not wait a whole interval for the
      # next decision: re-run at once (the flag is pre-cleared so this repeats at
      # most once per event).
      if [ "$woken" = 1 ]; then
        woken=0
        continue
      fi
      nap "$interval"
    done
  '';

  proxyControl = pkgs.writeShellApplication {
    name = "proxy-control";
    runtimeInputs = [ pkgs.coreutils pkgs.systemd pkgs.jq pkgs.util-linux pkgs.python3 ];
    text = ''
      wait_policy() {
        local target="$1" deadline="$2" attempt=0 observation
        shift 2
        local -a routing_args=()
        if [ "$#" = 2 ]; then routing_args=(--routing "$1" --routing-epoch "$2"); fi
        while true; do
          ${lib.optionalString config.my.proxy.runtimeRouting ''
          if [ "$#" = 2 ] && [ "$2" != "$(${config.my.proxy.routingCommand} view | jq -r .routing_epoch)" ]; then
            echo 'Routing request was superseded by another transaction' >&2
            return 1
          fi
          ''}
          if observation=$(${pkgs.python3}/bin/python3 ${./state.py} "$target" --max-age ${toString config.my.proxy.stateMaxAgeSeconds} --verification-max-age ${toString config.my.proxy.verificationMaxAgeSeconds} "''${routing_args[@]}"); then
            return 0
          fi
          if (( SECONDS >= deadline )); then return 124; fi
          if [ "''${watch_startup_recovery:-0}" = 1 ] && jq -e '.startup_recovery_needed' <<< "$observation" >/dev/null; then
            return 75
          fi
          if (( attempt % 10 == 0 )); then
            echo "$observation" >&2
          fi
          sleep 1
          attempt=$((attempt + 1))
        done
      }

      case "''${1:-}" in
        ${lib.optionalString config.my.proxy.runtimeRouting ''
        egress)
          [ "$EUID" = 0 ] || { echo 'Use sudo proxy-control egress list|ID' >&2; exit 1; }
          [ "$#" = 2 ] || { echo 'Expected egress list or an approved node ID' >&2; exit 2; }
          if [ "$2" = list ]; then
            exec ${config.my.proxy.routingCommand} egresses
          fi
          [[ "$2" =~ ^[a-f0-9]{64}$ ]] || { echo 'Invalid egress ID' >&2; exit 2; }
          exec "$0" routing proxy-only "$2"
          ;;
        routing|recover|on)
          [ "$EUID" = 0 ] || { echo "Use sudo proxy-control $*" >&2; exit 1; }
          recovery=0
          recovery_source=recover
          keep_selection=0
          explicit_start=0
          if [ "$1" = recover ]; then
            case "$#:''${2:-}" in
              1:) ;;
              2:--cached) recovery_source=recover-cached ;;
              *) echo 'Usage: sudo proxy-control recover [--cached]' >&2; exit 2 ;;
            esac
            recovery=1
            keep_selection=1
          elif [ "$1" = on ]; then
            [ "$#" = 1 ] || { echo 'Usage: sudo proxy-control on' >&2; exit 2; }
            keep_selection=1
            explicit_start=1
          else
            [ "$#" = 2 ] || { [ "$#" = 3 ] && [ "$2" = proxy-only ] && [[ "$3" =~ ^[a-f0-9]{64}$ ]]; } \
              || { echo 'Routing switch failed: expected rules or proxy-only'; exit 2; }
            case "$2" in rules|proxy-only) ;; *) echo 'Routing switch failed: invalid target'; exit 2 ;; esac
          fi
          mkdir -p /run/proxy-mode
          exec 8>/run/proxy-mode/operator.lock
          flock -x -w 30 8 || { echo 'Routing switch failed: another operation holds the control lock'; exit 1; }
          ${config.my.proxy.routingCommand} initialize
          initial_mode=$(cat /run/proxy-mode/status 2>/dev/null || true)
          if [ "$recovery" = 1 ]; then
            [ "$initial_mode" = blocked ] && [ "$(cat /run/proxy-mode/intent)" = proxy ] \
              || { echo 'Source recovery requires Mode C with proxy intent'; exit 1; }
            systemctl is-active --quiet proxy-mode.service \
              || { echo 'Start proxy-mode.service before recovering a source'; exit 1; }
            (umask 077; : >> /run/proxy-mode/source-refresh-job.lock)
            exec 7>>/run/proxy-mode/source-refresh-job.lock
            flock -x -w 5 7 || { echo 'Source download is busy. Retry recovery after it finishes.'; exit 1; }
          fi
          if [ "$keep_selection" = 1 ]; then
            ${config.my.proxy.routingCommand} recover
            requested=$(${config.my.proxy.routingCommand} requested)
            set -- routing "$requested"
          fi
          if [ -n "''${LF_SOURCE_GENERATION:-}" ] && [ "$(cat /run/proxy-mode/status)" != direct ]; then
            echo 'Prepared source startup requires Mode A' >&2
            exit 1
          fi
          transaction_epoch=""
          transaction_target="$2"
          transaction_step=initialize
          watch_startup_recovery=0
          startup_deadline=0
          recovery_args=()
          egress_args=()
          if [ "$#" = 3 ]; then egress_args=("$3"); fi
          health_diagnostic() {
            ${pkgs.python3}/bin/python3 ${./state.py} proxy --routing "$transaction_target" --routing-epoch "$transaction_epoch" \
              | jq -r --argjson epoch "$transaction_epoch" '
                  if .routing_epoch != $epoch then "superseded"
                  elif .last_proxy_health.ok then .reasons[:3] | join(", ")
                  else .last_proxy_health |
                    ((.failure_stages // []) | if length == 0 then "" else join(" -> ") end) as $chain
                    | (if $chain != "" then $chain else (.failure_stage // "no completed probe") end)
                      + (if .dns_leaf then " · lf-dns " + .dns_leaf + " (" + (.dns_leaf_session // "unmeasured") + ")" else "" end)
                  end' || true
          }
          recovery_hint() {
            local cache
            cache=$(jq -r --slurpfile routing /run/proxy-mode/routing.json '
              select(.boot_id == $routing[0].boot_id and .routing_epoch == $routing[0].routing_epoch
                and .routing_policy_digest == $routing[0].routing_policy_digest) | .result // empty
            ' /run/proxy-mode/source-recovery-cache.json 2>/dev/null || true)
            if [ "$cache" = eligible ]; then
              echo 'Apply the compatible download with sudo proxy-control recover --cached.'
            else
              echo 'Fetch a fresh candidate with sudo proxy-control recover, or select another registered source with sudo clash-source use SOURCE_ID --select-only before recovery.'
            fi
          }
          transaction_failed() {
            trap "" ERR INT TERM HUP
            flock -x -w 30 8 || return 1
            local outcome="''${1:-}" diagnostic=""
            if [ -n "$transaction_epoch" ]; then
              diagnostic=$(jq -r --argjson epoch "$transaction_epoch" 'select(.routing_epoch == $epoch) | .code // empty' /run/proxy-mode/routing-failure.json 2>/dev/null || true)
            fi
            if [ "$transaction_step" = health ] && [ -z "$diagnostic" ]; then diagnostic=$(health_diagnostic); fi
            if [ "$transaction_step" = recovery-cached ]; then
              diagnostic=$(jq -r '.result // empty' /run/proxy-mode/source-recovery-cache.json 2>/dev/null || true)
            fi
            if [ "$(cat /run/proxy-mode/intent)" = proxy ] \
              && [ "$transaction_step" != recovery-cached ] \
              && { [ -z "$transaction_epoch" ] || [ "$transaction_epoch" = "$(${config.my.proxy.routingCommand} view | jq -r .routing_epoch)" ]; }; then
              ${modeScript} --request-proxy || echo 'Check proxy-status for policy enforcement failure' >&2
              ${config.my.proxy.routingCommand} recover || true
              systemctl start --no-block proxy-net-wake.service || true
            fi
            echo "Routing switch failed: $transaction_target ($transaction_step''${outcome:+, $outcome}''${diagnostic:+: $diagnostic})"
            ${lib.optionalString config.my.proxy.controlledSources ''
            if [ "$(cat /run/proxy-mode/status)" = blocked ]; then
              if [ "$transaction_step" = health ] && [ "$outcome" != interrupted ]; then
                timeout --kill-after=2s 5s ${config.my.proxy.sourceCommand} check-recovery-cache >/dev/null 2>&1 || true
              fi
              recovery_hint
            fi
            ''}
          }
          trap 'transaction_failed; exit 1' ERR
          trap 'transaction_failed interrupted; exit 130' INT
          trap 'transaction_failed terminated; exit 143' TERM
          trap 'transaction_failed disconnected; exit 129' HUP
          if [ "$recovery" = 0 ] && ${config.my.proxy.routingCommand} same-ready "$2" "''${egress_args[@]}"; then
            trap - ERR INT TERM HUP
            echo "Routing already active: $2"
            exit 0
          fi
          while true; do
            if [ "$recovery" = 1 ]; then
              export LF_SOURCE_GENERATION
              if [ "$recovery_source" = recover-cached ]; then
                transaction_step='recovery-cached'
                LF_SOURCE_GENERATION=$(timeout --kill-after=2s 10s ${config.my.proxy.sourceCommand} recover-cached "''${recovery_args[@]}")
              else
                transaction_step='recovery-protection'
                ${modeScript} --request-proxy
                systemctl stop clash-verge.service
                ${config.my.proxy.routingCommand} stop-ip-tunnel
                ${config.my.proxy.coreNetworkCommand} routes-off
                transaction_step='recovery-download'
                LF_SOURCE_GENERATION=$(timeout --kill-after=2s 60s ${config.my.proxy.sourceCommand} recover "''${recovery_args[@]}")
              fi
              [[ "$LF_SOURCE_GENERATION" =~ ^[a-f0-9]{64}$ ]]
              if [ "$LF_SOURCE_GENERATION" = "''${failed_startup_digest:-}" ]; then
                transaction_failed 'download matches the failed snapshot'
                exit 1
              fi
            fi
            transaction_step=protection
            ${modeScript} --request-proxy
            if [ "$recovery" = 1 ]; then flock -u 7; fi
            transaction_step=stop
            systemctl stop clash-verge.service
            systemctl restart gost-relay.service
            transaction_step=prepare
            ${config.my.proxy.routingCommand} begin "$2" "''${egress_args[@]}"
            transaction_epoch=$(${config.my.proxy.routingCommand} view | jq -r .routing_epoch)
            transaction_step=dns
            systemctl restart dnsmasq.service socket-guest-dns.service
            ${pkgs.systemd}/bin/resolvectl flush-caches
            transaction_step=namespace
            systemctl restart clash-core-network.service
            transaction_step=helper
            systemctl start clash-verge.service
            transaction_step=core
            ${pkgs.python3}/bin/python3 ${../../../programs/clash/service/clash-verge-stage.py} start-source \
              ${config.programs.clash-verge.package.stageSettings} ${config.programs.clash-verge.package}/bin/verge-mihomo
            if [ "$recovery" = 1 ] && [ "$(cat /run/proxy-mode/config-digest)" != "$LF_SOURCE_GENERATION" ]; then
              transaction_failed 'candidate rejected, see proxy-status'
              exit 1
            fi
            flock -u 8
            systemctl start --no-block proxy-net-wake.service
            if [ "$recovery" = 0 ] && [ -n "''${LF_SOURCE_GENERATION:-}" ]; then
              trap - ERR INT TERM HUP
              exit 0
            fi
            transaction_step=health
            deadline=$((SECONDS + 90))
            if [ "$startup_deadline" = 0 ]; then startup_deadline=$((SECONDS + 210)); fi
            if (( deadline > startup_deadline )); then deadline=$startup_deadline; fi
            ${lib.optionalString config.my.proxy.controlledSources ''
            if [ "$explicit_start" = 1 ] && [ "$recovery" = 0 ]; then watch_startup_recovery=1; fi
            ''}
            if wait_policy proxy "$deadline" "$2" "$transaction_epoch" 2>/dev/null; then break; else wait_result=$?; fi
            if [ "$wait_result" != 75 ]; then
              if [ "$wait_result" = 124 ]; then transaction_failed timeout; else transaction_failed superseded; fi
              exit 1
            fi
            watch_startup_recovery=0
            ${lib.optionalString config.my.proxy.controlledSources ''
            flock -x -w 30 8
            observation=$(${pkgs.python3}/bin/python3 ${./state.py} proxy --routing "$2" --routing-epoch "$transaction_epoch" || true)
            if jq -e --argjson epoch "$transaction_epoch" '.routing_epoch == $epoch and .startup_recovery_needed' <<< "$observation" >/dev/null \
                && [ "$(cat /run/proxy-mode/intent)" = proxy ]; then
              failed_startup_digest=$(cat /run/proxy-mode/config-digest)
              revision=$(jq -er --arg digest "$failed_startup_digest" 'select(.snapshot == $digest) | .revision | select(test("^[0-9a-f]{64}$"))' /run/proxy-mode/source.json)
              recovery_args=(--revision "$revision")
              (umask 077; : >> /run/proxy-mode/source-refresh-job.lock)
              exec 7>>/run/proxy-mode/source-refresh-job.lock
              if flock -x -w 5 7; then
                cache_check=0
                timeout --kill-after=2s 10s ${config.my.proxy.sourceCommand} check-recovery-cache "''${recovery_args[@]}" >/dev/null || cache_check=$?
                if [ "$cache_check" = 0 ]; then
                  recovery_source=recover-cached
                  recovery=1
                ${lib.optionalString config.my.proxy.startupRecoveryFetch ''
                elif [ "$cache_check" = 4 ] && jq -e '(.result == "no-download" or .result == "already-accepted" or .result == "damaged-cache" or .result == "policy-mismatch")' /run/proxy-mode/source-recovery-cache.json >/dev/null; then
                  recovery_source=recover
                  recovery=1
                ''}
                fi
                if [ "$recovery" = 1 ]; then
                  echo "Restored snapshot failed ($(health_diagnostic)). Trying one same-source recovery ($recovery_source)."
                  continue
                fi
                flock -u 7
              fi
            fi
            flock -u 8
            ''}
            if wait_policy proxy "$deadline" "$2" "$transaction_epoch" 2>/dev/null; then break; else wait_result=$?; fi
            if [ "$wait_result" = 124 ]; then transaction_failed timeout; else transaction_failed superseded; fi
            exit 1
          done
          if [ "$recovery" = 1 ] && [ "$(cat /run/proxy-mode/config-digest)" != "$LF_SOURCE_GENERATION" ]; then
            echo 'The source changed during recovery. Check proxy-status.' >&2
            exit 1
          fi
          trap - ERR INT TERM HUP
          echo "Routing switch succeeded: $2"
          ;;
        ''}
        ${lib.optionalString (!config.my.proxy.runtimeRouting) "on|"}off|migrate-firewall)
          if [ "$EUID" != 0 ]; then echo 'Use sudo proxy-control on|off|migrate-firewall' >&2; exit 1; fi
          # Serialize the whole operation, including service start/stop. This is
          # separate from the short policy commit lock used by the loop.
          mkdir -p /run/proxy-mode
          exec 8>/run/proxy-mode/operator.lock
          flock -x -w 30 8
          if [ "$1" = on ] && [ -n "''${LF_SOURCE_GENERATION:-}" ] && [ "$(cat /run/proxy-mode/status)" != direct ]; then
            echo 'Prepared source startup requires Mode A' >&2
            exit 1
          fi
          if [ "$1" = migrate-firewall ]; then
            exec ${modeScript} --migrate-legacy
          fi
          if [ "$1" = on ]; then
            ${lib.optionalString config.programs.clash-verge.enable ''
            ${pkgs.coreutils}/bin/env LF_MANAGED_PROXY_POLICY=${lib.escapeShellArg config.systemd.services.clash-verge.environment.LF_MANAGED_PROXY_POLICY} ${pkgs.python3}/bin/python3 ${../../../programs/clash/service/preflight.py} ${config.programs.clash-verge.package.stageSettings} --startup
            ''}
            # An explicit operator action authorizes the configuration the GUI
            # currently holds. The window is single-use: the first core start
            # consumes it, so a later profile edit needs another clash-on.
            ${modeScript} --request-proxy
            systemctl stop clash-verge.service
            ${lib.optionalString config.programs.clash-verge.enable ''
            ${pkgs.python3}/bin/python3 ${../../../programs/clash/service/clash-verge-stage.py} clear-refresh \
              ${config.programs.clash-verge.package.stageSettings} ${lib.optionalString config.my.proxy.controlledSources "--keep-health-pin"}
            ''}
            exec 9>/run/proxy-mode/authorization.lock
            flock -x -w 30 9
            # The helper runs with ProtectProc=invisible and cannot read
            # /proc/sys/kernel/random/boot_id. Publish the identity from this
            # unrestricted context before any launcher can stage a profile.
            ${pkgs.python3}/bin/python3 ${../../../programs/clash/service/clash-verge-stage.py} initialize-boot /run/proxy-mode
            ${lib.optionalString config.my.proxy.controlledSources ''
            # Only an explicit start requests a fetch. The source activation
            # guard also requests proxy intent and must not enqueue an update.
            if [ -z "''${LF_SOURCE_GENERATION:-}" ]; then
              (umask 077; : > /run/proxy-mode/source-refresh-pending)
            fi
            rm -f /run/proxy-mode/source-refresh-stable.json
            ''}
            (umask 077; printf '%s' "''${LF_SOURCE_GENERATION:-}" > /run/proxy-mode/authorize-window)
            flock -u 9
            ${lib.optionalString config.my.proxy.coreNetns ''
            # Rebuild the private topology after the old core has stopped. A
            # compromised namespace may damage itself, never host policy.
            systemctl restart clash-core-network.service
            ''}
            systemctl start clash-verge.service
            ${lib.optionalString config.my.proxy.controlledSources ''
            ${pkgs.python3}/bin/python3 ${../../../programs/clash/service/clash-verge-stage.py} start-source \
              ${config.programs.clash-verge.package.stageSettings} ${config.programs.clash-verge.package}/bin/verge-mihomo
            ''}
          else
            ${modeScript} --request-direct
            if ! systemctl stop clash-verge.service; then
              ${modeScript} --request-proxy
              exit 1
            fi
            ${lib.optionalString config.my.proxy.runtimeRouting ''
            ${config.my.proxy.routingCommand} stop-ip-tunnel
            ''}
            exec 9>/run/proxy-mode/authorization.lock
            flock -x -w 30 9
            rm -f /run/proxy-mode/authorized-config /run/proxy-mode/config-digest \
              /run/proxy-mode/authorize-window /run/proxy-mode/effective-config-sha256 /run/proxy-mode/effective-config.json ${frontSock}
            ${lib.optionalString config.my.proxy.controlledSources ''
            ${pkgs.python3}/bin/python3 ${../../../programs/clash/service/source_refresh.py} cancel \
              ${config.programs.clash-verge.package.stageSettings}
            ''}
            ${pkgs.python3}/bin/python3 -c 'import importlib.util; s=importlib.util.spec_from_file_location("stage", "${../../../programs/clash/service/clash-verge-stage.py}"); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.clear_refresh("/run/proxy-mode", "/run/clash-verge-rev", keep_health_pin=${if config.my.proxy.controlledSources then "True" else "False"})'
            flock -u 9
            if [ -f /run/clash-verge-rev/forwarder.pid ]; then
              forwarder_pid=$(cat /run/clash-verge-rev/forwarder.pid 2>/dev/null || true)
              if [ -n "$forwarder_pid" ]; then
                case "$forwarder_pid" in
                  *[!0-9]*) ;;
                  *)
                    if [ -r "/proc/$forwarder_pid/cmdline" ] \
                      && tr '\0' ' ' <"/proc/$forwarder_pid/cmdline" \
                        | grep -q "UNIX-LISTEN:.*verge-mihomo.sock"; then
                      kill "$forwarder_pid" 2>/dev/null || true
                    fi
                    ;;
                esac
              fi
              rm -f /run/clash-verge-rev/forwarder.pid
            fi
            ${modeScript} --once
          fi
          flock -u 8
          systemctl start --no-block proxy-net-wake.service
          ;;
        preflight)
          ${if config.programs.clash-verge.enable then ''
          exec ${pkgs.coreutils}/bin/env LF_MANAGED_PROXY_POLICY=${lib.escapeShellArg config.systemd.services.clash-verge.environment.LF_MANAGED_PROXY_POLICY} ${pkgs.python3}/bin/python3 ${../../../programs/clash/service/preflight.py} ${config.programs.clash-verge.package.stageSettings}
          '' else ''echo '{"ready":false,"issues":["managed GUI package is disabled"]}'; exit 1''}
          ;;
        status)
          target="''${2:-auto}"
          [ "$target" != --check ] || target=auto
          verdict=0
          ${pkgs.python3}/bin/python3 ${./state.py} "$target" --max-age ${toString config.my.proxy.stateMaxAgeSeconds} --verification-max-age ${toString config.my.proxy.verificationMaxAgeSeconds} || verdict=$?
          if [ "''${3:-}" = --check ] || [ "''${2:-}" = --check ]; then exit "$verdict"; fi
          ;;
        wait)
          target="''${2:-proxy}"
          case "$target" in proxy|direct) ;; *) exit 2 ;; esac
          duration=90
          if [ "$target" = proxy ]; then duration=30; fi
          if wait_policy "$target" "$((SECONDS + duration))" "''${@:3}"; then
            echo "Proxy ready: $target"
            exit 0
          fi
          echo "Still waiting for current, verified $target policy and consumers" >&2
          ${pkgs.python3}/bin/python3 ${./state.py} "$target" --max-age ${toString config.my.proxy.stateMaxAgeSeconds} --verification-max-age ${toString config.my.proxy.verificationMaxAgeSeconds} || true
          cat /run/proxy-mode/reason /run/dns-upstream/reason 2>/dev/null || true
          exit 1
          ;;
        *) echo 'Usage: proxy-control on|off${lib.optionalString config.my.proxy.runtimeRouting "|recover [--cached]|routing rules|proxy-only"}|migrate-firewall|status [proxy|direct]|wait [proxy|direct]' >&2; exit 2 ;;
      esac
    '';
  };

in
{
  imports = [ ./authorization.nix ./routing.nix ./ip-tunnel.nix ];

  options.my.proxy.proxyModeRules = lib.mkOption {
    type = lib.types.str;
    default = "";
    internal = true;
    description = "nftables fragment applied while Clash runs and is healthy; filled in by network.nix.";
  };

  options.my.proxy.blockedModeRules = lib.mkOption {
    type = lib.types.str;
    default = "";
    internal = true;
    description = "nftables fragment applied while Clash runs but is not the required TUN path or its node is down; filled in by network.nix.";
  };

  options.my.proxy.expectedRules = lib.mkOption { type = lib.types.path; internal = true; };
  options.my.proxy.rulesCheck = lib.mkOption { type = lib.types.path; internal = true; };
  options.my.proxy.modeScript = lib.mkOption { type = lib.types.path; internal = true; };
  options.my.proxy.requestVerify = lib.mkOption { type = lib.types.path; internal = true; };
  options.my.proxy.healthSettings = lib.mkOption { type = lib.types.path; internal = true; };
  options.my.proxy.activeHealthSettings = lib.mkOption { type = lib.types.str; internal = true; };
  options.my.proxy.desktopHealthSettings = lib.mkOption { type = lib.types.attrsOf lib.types.path; internal = true; };
  options.my.proxy.listenerCheck = lib.mkOption { type = lib.types.path; internal = true; };
  options.my.proxy.listenerSettings = lib.mkOption { type = lib.types.path; internal = true; };
  options.my.proxy.tunCheck = lib.mkOption { type = lib.types.path; internal = true; };

  options.my.proxy.isClashOn = lib.mkOption {
    type = lib.types.path;
    internal = true;
    description = "Script that exits 0 when Clash is running; shared decision, do not duplicate.";
  };

  config = {
    my.proxy.isClashOn = isClashOn;
    my.proxy.expectedRules = expectedRules;
    my.proxy.rulesCheck = rulesCheck;
    my.proxy.modeScript = modeScript;
    my.proxy.requestVerify = verifyRequest;
    my.proxy.healthSettings = healthSettings;
    my.proxy.activeHealthSettings = activeHealthSettings;
    my.proxy.routingHealthSettings = routingHealthSettings;
    my.proxy.desktopHealthSettings = desktopHealthSettings;
    my.proxy.listenerCheck = listenerCheck;
    my.proxy.listenerSettings = listenerSettings;
    my.proxy.tunCheck = tunCheck;
    systemd.tmpfiles.rules = [
      "d /run/netsec 0755 root root -"
      "d /run/netsec/requests 0755 root root -"
    ];
    environment.systemPackages = [ proxyControl ];

    systemd.services.proxy-application-dns-probe = lib.mkIf config.services.resolved.enable (applicationDnsProbe tunProbeName);
    systemd.services.proxy-domestic-dns-probe = lib.mkIf (config.services.resolved.enable && domesticProbe != null)
      (applicationDnsProbe (builtins.head (builtins.match "https?://([A-Za-z0-9.-]+)(:[0-9]+)?(/[^[:space:]]*)?" domesticProbe)));

    # Enforcement follows boot-scoped operator intent and observed core state.
    # Only a deliberate off request clears a previously observed proxy session.
    systemd.services.proxy-mode = {
      description = "Apply the proxy killswitch / redirect while Clash is on, blackholing a TUN-less or node-dead core";
      after = [ "nftables.service" ];

      # Never claim `proxy` behind a firewall that is not there: a failed
      # nftables.service propagates here.
      requires = [ "nftables.service" ];
      unitConfig.OnFailure = [ "netsec-alert@%n.service" ];
      wantedBy = [ "multi-user.target" ];
      unitConfig.StartLimitIntervalSec = 0;
      serviceConfig = {
        Type = "notify";
        NotifyAccess = "all";
        WatchdogSec = "90s";
        TimeoutStartSec = "90s";
        MemoryAccounting = true;
        RuntimeDirectory = "proxy-mode";
        RuntimeDirectoryMode = "0755";

        # Keep /run/proxy-mode across a restart: the `core-seen` marker must
        # survive so a core that crashes after a proxy-mode restart is still read
        # as a crash (block), not as a fresh boot (direct).
        RuntimeDirectoryPreserve = true;

        # /run is writable under ProtectSystem=strict. Do not list
        # /run/user/<uid>: that path does not exist at boot, and a
        # ReadWritePaths entry on a missing path fails the unit.
        ReadWritePaths = [ "/run/netsec" ];

        # The loop synchronizes before its first sleep; nftables has already
        # atomically installed the initial safe mode.
        ExecStart = modeScript;
        ExecStartPost = "${pkgs.python3}/bin/python3 ${../firewall/alerts.py} clear proxy-mode.service";
        Restart = "always";
        RestartSec = "5";

        # Only nft needs NET_ADMIN; DAC_READ_SEARCH traverses the core's 0750
        # socket directory. Read-only cgroup/sysctl mounts permit all live
        # checks without granting write access. ProtectHome stays off so
        # the operator CLI can remove the legacy GUI forward socket under
        # /run/user/<uid>/ outside this unit's sandbox.
        NoNewPrivileges = true;
        CapabilityBoundingSet = [ "CAP_NET_ADMIN" "CAP_DAC_READ_SEARCH" ];
        ProtectSystem = "strict";
        ProtectKernelTunables = true;
        ProtectControlGroups = true;
        PrivateTmp = true;
        ProtectKernelModules = true;
        ProtectKernelLogs = true;
        ProtectClock = true;
        ProtectHostname = true;
        RestrictNamespaces = true;
        RestrictSUIDSGID = true;
        RestrictRealtime = true;
        RemoveIPC = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" "AF_NETLINK" ];
      };
    };

    # Every start/reload loads static rules plus Mode C (or cold-boot A) in
    # ONE transaction. ExecStartPost cannot close a reload or inter-transaction gap.
    systemd.services.nftables.serviceConfig = {
      ExecStart = lib.mkForce [ "${modeScript} --reload" ];
      ExecReload = lib.mkForce [ "${modeScript} --reload" ];
      ExecStartPost = lib.mkForce [ ];
      ExecStop = lib.mkForce [ ];  # stopping the unit never removes protection
      NoNewPrivileges = true;
      CapabilityBoundingSet = [ "CAP_NET_ADMIN" "CAP_DAC_READ_SEARCH" ];
    };

    systemd.services.proxy-rules-watch = {
      description = "Wake policy supervision after owned nft rules change";
      after = [ "nftables.service" ];
      wants = [ "nftables.service" ];
      wantedBy = [ "multi-user.target" ];
      unitConfig.StartLimitIntervalSec = 0;
      serviceConfig = {
        ExecStart = pkgs.writeShellScript "proxy-rules-watch" ''
          set -o pipefail
          ${pkgs.nftables}/bin/nft monitor ruleset \
            | ${pkgs.python3}/bin/python3 ${./events.py} ${pkgs.systemd}/bin/systemctl
        '';
        Restart = "always";
        RestartSec = "5s";
        NoNewPrivileges = true;
        CapabilityBoundingSet = [ "CAP_NET_ADMIN" ];
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictAddressFamilies = [ "AF_NETLINK" "AF_UNIX" ];
      };
    };

    # Event trigger: the mihomo control socket is created when the core starts and
    # removed when it stops (or restarts), so watching it turns the supervisors
    # from 2 s pollers into event-driven loops. A backstop interval inside each
    # loop still heals anything the event misses.
    systemd.paths.proxy-net-watch = {
      wantedBy = [ "multi-user.target" ];
      pathConfig = {
        # The socket and its directory: either one changing (core start/stop,
        # socket recreate) fires the coordinator.
        PathChanged = [ mihomoSock mihomoDir ];
        Unit = "proxy-net-wake.service";
      };
    };

    # Re-arm the event watcher after a stop or start limit. Backstop polling
    # maintains enforcement while the watcher is inactive.
    systemd.services.proxy-net-watch-recover = {
      description = "Re-arm the proxy event .path if it is not active";
      serviceConfig = { Type = "oneshot"; };
      script = ''
        if ! ${pkgs.systemd}/bin/systemctl is-active --quiet proxy-net-watch.path; then
          ${pkgs.systemd}/bin/systemctl reset-failed proxy-net-watch.path 2>/dev/null || true
          ${pkgs.systemd}/bin/systemctl start proxy-net-watch.path 2>/dev/null || true
        fi
      '';
    };

    systemd.timers.proxy-net-watch-recover = {
      description = "Check the proxy event .path every minute";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "1min";
        OnUnitActiveSec = "1min";
        AccuracySec = "10s";
      };
    };

    # A TUN link change (the GUI toggling TUN off/on, or the core recreating it)
    # is a netlink event, not a filesystem one -- sysfs does not emit inotify, so
    # a systemd.path cannot see it. Watch rtnetlink and wake the same coordinator;
    # the 5 s backstop still covers anything missed.
    systemd.services.proxy-tun-watch = {
      description = "Wake the proxy supervisor on a TUN link change";
      after = [ "network.target" ];
      wantedBy = [ "multi-user.target" ];
      unitConfig.StartLimitIntervalSec = 0;
      serviceConfig = {
        Type = "simple";
        ExecStart = pkgs.writeShellScript "proxy-tun-watch" ''
          ${pkgs.coreutils}/bin/stdbuf -oL ${pkgs.iproute2}/bin/ip -o monitor link \
            | while IFS= read -r line; do
                case "$line" in
                  *"${config.my.proxy.tunDev}"*)
                    ${pkgs.systemd}/bin/systemctl start --no-block proxy-net-wake.service 2>/dev/null || true
                    ;;
                esac
              done
        '';
        Restart = "always";
        RestartSec = "5";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        ProtectKernelModules = true;
        ProtectKernelLogs = true;
        ProtectClock = true;
        ProtectHostname = true;
        RestrictNamespaces = true;
        RestrictSUIDSGID = true;
        RestrictRealtime = true;
        RemoveIPC = true;
        RestrictAddressFamilies = [ "AF_NETLINK" "AF_UNIX" ];
      };
    };

    systemd.services.proxy-core-tun-watch = lib.mkIf config.my.proxy.coreNetns {
      description = "Wake supervision on core namespace TUN changes";
      wantedBy = [ "multi-user.target" ];
      requires = [ "clash-core-network.service" ];
      after = [ "clash-core-network.service" ];
      bindsTo = [ "clash-core-network.service" ];
      serviceConfig = config.systemd.services.proxy-tun-watch.serviceConfig // {
        NetworkNamespacePath = config.my.proxy.coreTopology.namespacePath;
        CapabilityBoundingSet = "";
      };
    };

    # The coordinator: one event wakes nft (proxy-mode), DNS (dns-upstream) and gost in
    # the same instant, so they switch together instead of three loops noticing at
    # different times. SIGWINCH only interrupts each loop's sleep; every loop keeps
    # its own idempotent decision logic and stays the source of truth.
    systemd.services.proxy-net-wake = {
      description = "Wake the proxy supervisors on a Clash core state change";

      # A single Clash start emits several TUN/socket events, so this oneshot is
      # restarted 3-5x in ~1s; without this it hits the default StartLimit
      # (5/10s), goes failed, and the .path that feeds it goes
      # `unit-start-limit-hit` -- a permanently dead event layer. Match the other
      # supervisors by never rate-limiting it.
      unitConfig.StartLimitIntervalSec = 0;
      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeShellScript "proxy-net-wake" ''
          for u in proxy-mode.service dns-upstream.service gost-relay.service; do
            ${pkgs.systemd}/bin/systemctl kill -s WINCH --kill-whom=main "$u" 2>/dev/null || true
          done
        '';
      };
    };
  };
}

