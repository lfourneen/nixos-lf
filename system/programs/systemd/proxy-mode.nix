{ pkgs, config, lib, ... }:

let
  # The two mode-dependent fragments. `rules` is the normal Mode-B refusal (the
  # core is exempt by its mark and may reach its nodes); `blockedRules` is the
  # strict blackhole used when Clash runs but is not the TUN path we require, and
  # does NOT exempt the core, so a system-proxy/direct core or a dead node cannot
  # fall back to a direct connection.
  rules = pkgs.writeText "proxymode-rules.nft" config.my.proxy.proxyModeRules;
  blockedRules = pkgs.writeText "blockedmode-rules.nft" config.my.proxy.blockedModeRules;

  m = config.my.machine;
  port = m.ports;

  # Same canonical form nft prints (and nftables-verify greps for), so the
  # "core exemption present?" test reads the live rules consistently.
  coreMark = "0x" + lib.toLower (lib.fixedWidthString 8 "0" (lib.toHexString m.mihomoMark));

  # The TUN path is only required with tunMode; the plain HTTP-proxy model
  # (tunMode=false) deliberately runs Clash without a TUN, so it must not be
  # blackholed for lacking one. With tunMode the TUN is mandatory.
  requireTun = config.my.proxy.tunMode;

  # A proxied health probe: routed by the profile (gstatic is geolocation-!cn),
  # so it exercises the node, not just the core. It uses plain HTTP on purpose --
  # `generate_204` returns 0 bytes, so a frequent probe costs almost no traffic,
  # where a TLS probe would pay a handshake every round. Health means: the core
  # answers through its mixed port, Clash's log shows the route is a proxy (not
  # `using DIRECT`), and Clash can delay-test the node that route selected.
  probeUrl = "http://www.gstatic.com/generate_204";
  probeHost = "www.gstatic.com";

  # A DIRECT-routed name used for the TUN self-test: its fake-ip answer is only
  # reachable through the TUN, so a successful connect proves the TUN carries
  # packets. It is a domestic name so the test does not depend on the node.
  tunProbeName = "www.baidu.com";

  # First two octets of the fake-ip pool, to recognise a fake answer.
  fakeIpPrefix = lib.concatStringsSep "." (
    lib.take 2 (lib.splitString "." (lib.head (lib.splitString "/" config.my.proxy.fakeIpRange)))
  );

  # The desktop user's Clash Verge runtime owns the mihomo control socket; the core
  # creates it on start and removes it on stop, so watching it (and its directory,
  # which survives the socket) is the event that says "the Clash core changed
  # state".
  mihomoDir = "/run/user/${
    toString config.users.users.${config.my.machine.desktopUser}.uid
  }/clash-verge-rev";
  mihomoSock = "${mihomoDir}/verge-mihomo.sock";

  # Single source of truth for "Clash is on": dns-upstream.nix calls the same script, so
  # the criterion cannot drift between the two supervisors.
  isClashOn = pkgs.writeShellScript "is-clash-on" ''
    # clash-verge.service also runs an always-on root helper, so its state alone is
    # not "Clash is on": require the core inside the unit's cgroup, which a local
    # process cannot forge. Matching a process name would let any user process pin
    # the host in proxy mode with a dead core. The cgroup is read directly -- an
    # `is-active` would only add a D-Bus round-trip the cgroup already answers.
    for p in $(${pkgs.coreutils}/bin/cat /sys/fs/cgroup/system.slice/clash-verge.service/cgroup.procs 2>/dev/null); do
      ${pkgs.gnugrep}/bin/grep -qa 'bin/verge-mihomo' "/proc/$p/cmdline" 2>/dev/null && exit 0
    done
    exit 1
  '';

  # "The Clash *service* (helper) is up", independent of whether the core runs.
  # The unit is enabled/wanted at boot, so its state alone cannot mean "Clash is
  # on"; but combined with a marker that the core was seen this boot, it is what
  # distinguishes a crashed core (service up, core gone -> block) from a deliberate
  # Mode A (service stopped by clash-off -> direct) and from a fresh boot (service
  # up, core never seen -> direct).
  isClashServiceOn = pkgs.writeShellScript "is-clash-service-on" ''
    for p in $(${pkgs.coreutils}/bin/cat /sys/fs/cgroup/system.slice/clash-verge.service/cgroup.procs 2>/dev/null); do
      ${pkgs.gnugrep}/bin/grep -qa 'bin/clash-verge-service' "/proc/$p/cmdline" 2>/dev/null && exit 0
    done
    exit 1
  '';

  # One pass: decide the mode from the Clash/TUN/node state, then make nft match it.
  modeScript = pkgs.writeShellScript "proxy-mode-sync" ''
    RULES=${rules}
    B_RULES=${blockedRules}
    IS_CLASH_ON=${isClashOn}
    IS_SERVICE_ON=${isClashServiceOn}
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
    DATE=${pkgs.coreutils}/bin/date
    TIMEOUT=${pkgs.coreutils}/bin/timeout
    SLEEP=${pkgs.coreutils}/bin/sleep
    CURL=${pkgs.curl}/bin/curl
    DIG=${pkgs.dnsutils}/bin/dig

    TUNDEV=${config.my.proxy.tunDev}
    REQUIRE_TUN=${if requireTun then "1" else "0"}
    MIHOMO_MIXED=${toString port.mihomoMixed}
    MIHOMO_DNS=${toString port.mihomoDns}
    MIHOMO_SOCK=${mihomoSock}
    CORE_MARK=${coreMark}
    PROBE_URL=${probeUrl}
    PROBE_HOST=${probeHost}
    TUN_PROBE_NAME=${tunProbeName}
    FAKE_IP_PREFIX=${fakeIpPrefix}

    mode=""
    reason=""           # "no-tun" | "direct-mode" | "node" | "core-gone" | ""
    core_gone_fails=0   # consecutive passes with the core gone but the service up
    fails=0
    first=1
    once=0
    changed=1
    nap_pid=""
    interval=2          # start fast; the loop backs off once the state is stable
    MIN=2
    # Backstop cap: short while enforcement is (or should be) loaded -- a missed
    # event (or a base-ruleset reload, which recreates the fragment chains empty)
    # must not leave the guard off for long -- and long when settled in direct,
    # where nothing is loaded and events still drive transitions.
    cap_for() { case "$1" in proxy|blocked|unenforced) echo 5 ;; *) echo 60 ;; esac; }
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
    # (2 s while transitioning/degraded, up to 30 s when stable), so the steady
    # state is almost free while staying responsive.
    trap ':' WINCH
    nap() {
      ${pkgs.coreutils}/bin/sleep "$1" &
      nap_pid=$!
      wait "$nap_pid" 2>/dev/null || true
      kill "$nap_pid" 2>/dev/null || true
      nap_pid=""
    }

    # Ask for an immediate verification. A start issued here rides the activation
    # transaction when a rebuild restarts proxy-mode, but that is harmless now:
    # nftables-verify reports through the journal + /run/netsec/failed and always
    # exits 0 (see nftables-verify.nix), so it can never fail the rebuild.
    request_verify() { $SYSTEMCTL start --no-block nftables-verify.service 2>/dev/null || true; }

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

    now() { $DATE +%s; }

    # --- live state readers -------------------------------------------------
    rules_loaded() { $NFT list table ip lf_proxymode_nat >/dev/null 2>&1; }
    rules_loaded6() { $NFT list table ip6 lf_proxymode_nat >/dev/null 2>&1; }
    chains_loaded() { $NFT list chain inet lf_filter proxymode_drops 2>/dev/null | $GREP -q 'counter'; }
    # The proxy fragment exempts the core by its mark; the blocked one must not
    # in proxymode_drops (its only exemption lives in the guard chain).
    drops_has_core_mark() { $NFT list chain inet lf_filter proxymode_drops 2>/dev/null | $GREP -q "meta mark $CORE_MARK accept"; }
    # The Mode-C guard is filled iff its terminal drop is present. nft prints it
    # as "counter packets N bytes N drop", so match the verdict, not the counter.
    guard_active() { $NFT list chain inet lf_filter lf_blocked_guard 2>/dev/null | $GREP -q 'drop'; }

    # Anything that would still redirect or drop after a tear-down.
    rules_present() {
      guard_active && return 0
      rules_loaded && return 0
      rules_loaded6 && return 0
      chains_loaded && return 0
      return 1
    }
    proxy_loaded() { rules_loaded && rules_loaded6 && chains_loaded && drops_has_core_mark && ! guard_active; }
    blocked_loaded() {
      guard_active || return 1
      drops_has_core_mark && return 1
      rules_loaded && return 1
      rules_loaded6 && return 1
      chains_loaded && return 1
      return 0
    }
    # Does the live state already match the wanted mode?
    mode_loaded() {
      case "$1" in
        proxy) proxy_loaded ;;
        blocked) blocked_loaded ;;
        direct) ! rules_present ;;
        *) return 1 ;;
      esac
    }

    # A silent teardown failure leaves the user offline with Clash closed, so every
    # step is checked: absent tables are fine, a failed delete or flush is not.
    rules_off() {
      rc=0
      if rules_loaded; then
        $NFT delete table ip lf_proxymode_nat || rc=1
      fi
      if rules_loaded6; then
        $NFT delete table ip6 lf_proxymode_nat || rc=1
      fi
      $NFT flush chain inet lf_filter proxymode_drops || rc=1
      $NFT flush chain inet lf_filter proxymode_tail || rc=1
      $NFT flush chain inet lf_filter proxymode_forward || rc=1
      $NFT flush chain inet lf_filter lf_blocked_guard || rc=1
      return $rc
    }
    # The fragments tear down and re-create their own NAT tables, so each is a
    # single atomic transaction.
    rules_on_proxy() { $NFT -f "$RULES"; }
    rules_on_blocked() { $NFT -f "$B_RULES"; }

    # Is the strict TUN path actually carrying traffic? Device up + sing-tun's
    # FIB rule + a route via the device: the same kernel facts nftables-verify
    # reads, so the two cannot disagree about "TUN mode".
    tun_ready() {
      $IP link show dev "$TUNDEV" >/dev/null 2>&1 || return 1
      $IP -o link show dev "$TUNDEV" 2>/dev/null | $GREP -q ',UP' || return 1
      $IP rule show | $GREP -q 'lookup 2022' || return 1
      $IP route show table 2022 | $GREP -q "dev $TUNDEV" || return 1
      return 0
    }
    # Full health gate. All three must hold:
    #  (1) the core answers a proxied HTTP request through its mixed port, and
    #      Clash's own log for that connection does not say `using DIRECT` --
    #      this catches direct mode, a rule profile that is effectively
    #      all-DIRECT, and a group/slot selected to DIRECT;
    #  (2) Clash delay-tests the node the route actually selected (the group
    #      named in the log), so a fallback/other subscription node cannot mask a
    #      dead selection -- this is Clash-centric, not "any node is up";
    #  (3) a fake-ip answer is reachable through the TUN, proving the TUN really
    #      carries packets (shape-only checks cannot see a stuck sing-tun).
    # health_gate names the failing layer for the reason file:
    #   obs   = the route could not be read (probe code / log line / group list)
    #   route = the probe was routed DIRECT or REJECT (no real node)
    #   node  = the effective group's selected node failed the delay test
    #   tun   = the TUN does not carry packets
    health_ok() {
      health_gate=""
      : > "$CAPTURE" 2>/dev/null || true
      ${pkgs.coreutils}/bin/chmod 600 "$CAPTURE" 2>/dev/null || true
      # -N flushes each line as it arrives; --max-time caps the reader.
      $CURL -N -s --max-time 3 --unix-socket "$MIHOMO_SOCK" "http://localhost/logs?level=info" -o "$CAPTURE" 2>/dev/null &
      cap=$!
      $SLEEP 0.2
      code=$($TIMEOUT 3 $CURL -s -o /dev/null -w '%{http_code}' --noproxy "" -x "http://127.0.0.1:$MIHOMO_MIXED" "$PROBE_URL" 2>/dev/null || true)
      # Cheap verdict first, so a dead core is judged before the poll.
      if [ "$code" != "200" ] && [ "$code" != "204" ]; then
        kill "$cap" 2>/dev/null || true
        wait "$cap" 2>/dev/null || true
        health_gate=obs; log "health: (a) probe http code=[$code]"; return 1
      fi
      line=""
      for _ in 1 2 3 4 5 6 7 8 9 10; do
        line=$($GREP -F "$PROBE_HOST" "$CAPTURE" 2>/dev/null | tail -1)
        [ -n "$line" ] && break
        $SLEEP 0.3
      done
      kill "$cap" 2>/dev/null || true
      wait "$cap" 2>/dev/null || true
      rest="''${line#*using }"
      [ "$rest" != "$line" ] || { health_gate=obs; log "health: (a) no route line for $PROBE_HOST"; return 1; }
      # `using DIRECT`/`using REJECT` = the RULE itself; and the MEMBER (the first
      # bracket content) DIRECT/REJECT = the group selected a non-node. Judge the
      # member exactly, so a node NAME containing "[DIRECT]" is not a false hit.
      member=$(printf '%s' "$rest" | $SED -n 's/^[^[]*\[\([^]]*\)\].*/\1/p')
      case "$line" in
        *"using DIRECT"*|*"using REJECT"*) health_gate=route; log "health: (a) rule routed DIRECT/REJECT"; return 1 ;;
      esac
      case "$member" in
        DIRECT|REJECT) health_gate=route; log "health: (a) member $member"; return 1 ;;
      esac
      # (b) Resolve the effective group against the live group list (a group name
      # containing '[' would truncate a plain split), then Clash delay-tests the
      # node that group has selected.
      group=$(printf '%s' "$rest" | $SED -n 's/^\([^[]*\)\[.*/\1/p')
      if [ -z "$group" ] || ! $CURL -s --max-time 2 --unix-socket "$MIHOMO_SOCK" "http://localhost/proxies/$group" 2>/dev/null | $GREP -q '"all"'; then
        group=""
        glist=$($TIMEOUT 3 $CURL -s --unix-socket "$MIHOMO_SOCK" http://localhost/proxies 2>/dev/null | $JQ -r '.proxies | to_entries[] | select(.value.all) | .key' 2>/dev/null)
        while IFS= read -r g; do
          [ -n "$g" ] || continue
          case "$rest" in
            "$g"[*) group="$g"; break ;;
          esac
        done < <(printf '%s\n' "$glist")
      fi
      [ -n "$group" ] || { health_gate=obs; log "health: (a) group not resolved: $rest"; return 1; }
      delay=$($TIMEOUT 3 $CURL -s --unix-socket "$MIHOMO_SOCK" \
        "http://localhost/proxies/$group/delay?url=$PROBE_URL&timeout=2000" 2>/dev/null \
        | $SED -n 's/.*"delay":\([0-9][0-9]*\).*/\1/p')
      [ -n "$delay" ] || { health_gate=node; log "health: (b) delay empty for group [$group]"; return 1; }
      # (c) the TUN carries packets: a DNS query to the fake-ip is routed into the
      # TUN and answered by the core's dns-hijack -- no third-party site whose port
      # policy could fail a healthy host (an HTTP dial has that dependency).
      if [ "$REQUIRE_TUN" = "1" ]; then
        fip=$($TIMEOUT 2 $DIG +short @127.0.0.1 -p "$MIHOMO_DNS" "$TUN_PROBE_NAME" A 2>/dev/null \
          | $GREP -E "^$FAKE_IP_PREFIX\." | head -1)
        [ -n "$fip" ] || { health_gate=tun; log "health: (c) no fake-ip for $TUN_PROBE_NAME"; return 1; }
        $TIMEOUT 3 $DIG +time=2 +tries=1 +short @"$fip" -p 53 "$TUN_PROBE_NAME" A 2>/dev/null \
          | $GREP -q . || { health_gate=tun; log "health: (c) TUN self-test failed via $fip"; return 1; }
      fi
      return 0
    }
    # mihomo "direct" mode routes everything DIRECT even with the TUN up, so the
    # core-mark exemption would let every flow leave from the real address. Read
    # the live mode from the control socket (loopback, so it works while blocked)
    # and refuse to trust it; "rule"/"global" are fine ("global" still uses the
    # group). A read failure is not judged here, so a starting core is not cut.
    mode_direct() {
      cfg=$($TIMEOUT 3 $CURL -s --unix-socket "$MIHOMO_SOCK" http://localhost/configs 2>/dev/null || true)
      case "$cfg" in
        *'"mode":"direct"'*) return 0 ;;
        *) return 1 ;;
      esac
    }

    sync_pass() {
      prev="$mode"
      prearmed=0

      # No fragment at all (proxyKillSwitch off): the host is unenforced direct.
      if [ ! -s "$RULES" ]; then
        rules_off
        mode=direct; reason=""
        printf 'direct\n' > "$STATUS"
        printf '\n' > "$REASON" 2>/dev/null || true
        first=0
        changed=1
        return 0
      fi

      if $IS_CLASH_ON; then
        # Remember that the core ran this boot; the marker is what turns "core
        # vanished" into a block instead of a silent drop to Mode A.
        : > "$MARKER" 2>/dev/null || true
        core_gone_fails=0
        # Enforcement vanished (a base-ruleset reload destroys lf_filter): fail
        # closed at once, before the health gate, so the host is never left with
        # no guard, no killswitch and no :33333 redirect while the gate decides.
        # The gate below then upgrades to proxy if the path is healthy.
        if ! rules_present; then
          if rules_on_blocked; then mode=blocked; reason="reload"; prearmed=1; fi
        fi
        # tun_ok is 1 unless we require a TUN and it is not ready. In the plain
        # HTTP-proxy model (REQUIRE_TUN=0) a missing TUN is not a failure.
        tun_ok=1
        if [ "$REQUIRE_TUN" = "1" ] && ! tun_ready; then tun_ok=0; fi

        if [ "$tun_ok" = "1" ]; then
          # A TUN-up core in "direct" mode leaks exactly like a node-dead one:
          # everything leaves DIRECT under the exempt mark. The mode is read over
          # loopback, so it can be checked whether or not the guard is up.
          if mode_direct; then
            want=blocked; want_reason="direct-mode"
          else
            # The core keeps its egress in blocked, so this runs every pass and
            # the node is picked up the moment it recovers. One failure cuts
            # immediately (the probe is http/0-byte, so it is cheap to run often
            # and a slow start only costs a short blocked window). The reason
            # names the failing layer (a/b/c) for `proxy-status`.
            if health_ok; then
              want=proxy; want_reason=""
            else
              want=blocked; want_reason="$health_gate"
            fi
          fi
        else
          want=blocked; want_reason="no-tun"
        fi
      else
        # Core is gone. Distinguish a deliberate Mode A (clash-off stops the
        # service) and a fresh boot (the service is up but the core was never
        # seen this boot) from a core that was running and vanished while the
        # service stayed up (a crash, or the GUI toggled the core off): only the
        # last one is a fail-closed block.
        if [ -f "$MARKER" ] && $IS_SERVICE_ON; then
          core_gone_fails=$((core_gone_fails + 1))
          if [ "$core_gone_fails" -ge 2 ]; then
            want=blocked; want_reason="core-gone"
          else
            # One pass of grace: clash-off stops the service too, and the helper
            # can still be in the cgroup for a moment after the core is gone.
            want="''${mode:-direct}"; want_reason="$reason"
          fi
        else
          core_gone_fails=0
          rm -f "$MARKER" 2>/dev/null || true
          want=direct; want_reason=""
        fi
      fi

      # Apply the fragment the want requires whenever it differs or its
      # post-condition does not hold (e.g. a manual nft flush emptied it).
      # The pre-arm already changed `mode`; force one apply so STATUS/REASON are
      # written and verify/wake fire even when the gate reaches the same verdict.
      if [ "$prearmed" = "1" ] || [ "$want" != "$mode" ] || ! mode_loaded "$want"; then
        applied=0
        case "$want" in
          proxy)
            if rules_on_proxy; then applied=1; fi
            ;;
          blocked)
            if rules_on_blocked; then applied=1; fi
            ;;
          direct)
            if rules_off; then applied=1; fi
            ;;
        esac
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
        printf '%s\n' "$mode" > "$STATUS"
        printf '%s\n' "$reason" > "$REASON" 2>/dev/null || true
        if [ -n "$prev" ] && [ "$mode" != "$prev" ]; then
          request_verify
          request_wake
        fi
      elif [ "$reason" != "$want_reason" ]; then
        reason="$want_reason"
        printf '%s\n' "$reason" > "$REASON" 2>/dev/null || true
      fi

      # Post-condition of the recorded mode; a mismatch keeps the user offline
      # after closing Clash, so fail visibly through the verifier unit.
      ok=1
      if [ "$mode" = "proxy" ] || [ "$mode" = "blocked" ]; then
        mode_loaded "$mode" || ok=0
      elif [ "$mode" = "unenforced" ]; then
        # Always loud: Clash is on and our enforcement is not loaded.
        ok=0
      else
        rules_present && ok=0
      fi
      if [ "$ok" = "1" ]; then
        fails=0
      else
        fails=$((fails + 1))
        if [ "$fails" = "1" ] || [ $((fails % 6)) -eq 0 ]; then
          loud "nft state does not match mode $mode (attempt $fails)"
          request_verify
        fi
      fi

      # Back off only when the mode is stable and the post-condition holds; any
      # change or mismatch keeps the loop at the fast interval.
      if [ "$mode" != "$prev" ] || [ "$ok" != "1" ]; then changed=1; else changed=0; fi
      return 0
    }

    while true; do
      sync_pass
      [ "$once" = "1" ] && exit 0
      if [ "$changed" = 1 ]; then
        interval=$MIN
      else
        cap=$(cap_for "$mode")
        [ "$interval" -lt "$cap" ] && interval=$(( interval * 2 > cap ? cap : interval * 2 ))
      fi
      nap "$interval"
    done
  '';

in
{
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

  options.my.proxy.isClashOn = lib.mkOption {
    type = lib.types.path;
    internal = true;
    description = "Script that exits 0 when Clash is running; shared decision, do not duplicate.";
  };

  config = {
    my.proxy.isClashOn = isClashOn;

    # The redirect and the killswitch exist only while Clash runs: without Clash
    # the host is plain direct-connected, and a crashed core stays fail-closed.
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
        Type = "simple";
        RuntimeDirectory = "proxy-mode";
        RuntimeDirectoryMode = "0755";
        # Keep /run/proxy-mode across a restart: the `core-seen` marker must
        # survive so a core that crashes after a proxy-mode restart is still read
        # as a crash (block), not as a fresh boot (direct).
        RuntimeDirectoryPreserve = true;
        # --once before the loop so the recorded mode is never stale.
        ExecStartPre = "${modeScript} --once";
        ExecStart = modeScript;
        Restart = "always";
        RestartSec = "5";

        # Sandboxing. It shells out to nft (netlink), systemctl (D-Bus), ip and
        # curl (the node probe), and reads cgroupfs, so ProtectKernelTunables /
        # ProtectControlGroups stay off and AF_NETLINK / AF_INET are allowed;
        # everything else is narrowed. ProtectHome is deliberately OFF: the
        # mihomo control socket lives under /run/user/<uid>/, which ProtectHome
        # would make inaccessible, breaking the log capture, /configs and the
        # node delay call.
        NoNewPrivileges = true;
        ProtectSystem = "strict";
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

    # Self-heal for the event layer: if the .path ever ends up not armed (a
    # future start-limit, or a manual stop), re-arm it, so the event layer can
    # never stay silently dead (audit P1). The loops' backstop polling keeps the
    # host enforcing even while the .path is down.
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

