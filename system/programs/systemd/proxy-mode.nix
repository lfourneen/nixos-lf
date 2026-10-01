{ pkgs, config, lib, ... }:

let
  rules = pkgs.writeText "proxymode-rules.nft" config.my.proxy.proxyModeRules;

  # The desktop user's Clash Verge runtime owns the mihomo control socket; the core
  # creates it on start and removes it on stop, so watching it (and its directory,
  # which survives the socket) is the event that says "the Clash core changed
  # state".
  mihomoDir = "/run/user/${
    toString config.users.users.${config.my.machine.desktopUser}.uid
  }/clash-verge-rev";
  mihomoSock = "${mihomoDir}/verge-mihomo.sock";

  # Single source of truth for "Clash is on": dns-pac.nix calls the same script, so
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

  # One pass: decide the mode from the Clash state, then make nft match it.
  modeScript = pkgs.writeShellScript "proxy-mode-sync" ''
    RULES=${rules}
    IS_CLASH_ON=${isClashOn}
    STATUS=/run/proxy-mode/status
    NFT=${pkgs.nftables}/bin/nft
    SYSTEMCTL=${pkgs.systemd}/bin/systemctl
    GREP=${pkgs.gnugrep}/bin/grep
    SLEEP=${pkgs.coreutils}/bin/sleep

    mode=""
    fails=0
    first=1
    once=0
    changed=1
    nap_pid=""
    interval=2          # start fast; the loop backs off once the state is stable
    MIN=2
    MAX=10              # cap the backstop: if an event is ever missed, the
                        # killswitch cannot lag by more than this
    [ "''${1:-}" = "--once" ] && once=1

    log() { echo "proxy-mode: $*" >&2; }
    loud() {
      echo "proxy-mode: $*" >&2
      ${pkgs.systemd}/bin/systemd-cat -t proxy-mode -p err ${pkgs.coreutils}/bin/echo "proxy-mode: $*" || true
    }

    # Event-driven sleep: `proxy-net-wake.service` sends SIGUSR1 when the Clash
    # core appears or disappears, and SIGUSR1 interrupts `wait`, so the loop reacts
    # at once instead of waiting out the backstop interval. The interval itself
    # adapts (2 s while transitioning/degraded, up to 30 s when stable and clean),
    # which is what makes the steady state almost free while staying responsive.
    trap ':' USR1
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

    # The fragment loads in one transaction, so one probe covers all of it -- but
    # both families and every fragment chain, or a half-loaded state goes unnoticed.
    rules_loaded() { $NFT list table ip proxymode_nat >/dev/null 2>&1; }
    rules_loaded6() { $NFT list table ip6 proxymode_nat >/dev/null 2>&1; }

    # Anything that would still redirect or drop after a tear-down.
    rules_present() {
      rules_loaded && return 0
      rules_loaded6 && return 0
      $NFT list chain inet filter proxymode_drops 2>/dev/null | $GREP -q 'counter' && return 0
      $NFT list chain inet filter proxymode_tail 2>/dev/null | $GREP -q 'counter' && return 0
      $NFT list chain inet filter proxymode_forward 2>/dev/null | $GREP -q 'counter' && return 0
      return 1
    }

    # A silent teardown failure leaves the user offline with Clash closed, so every
    # step is checked: absent tables are fine, a failed delete or flush is not.
    rules_off() {
      rc=0
      if rules_loaded; then
        $NFT delete table ip proxymode_nat || rc=1
      fi
      if $NFT list table ip6 proxymode_nat >/dev/null 2>&1; then
        $NFT delete table ip6 proxymode_nat || rc=1
      fi
      $NFT flush chain inet filter proxymode_drops || rc=1
      $NFT flush chain inet filter proxymode_tail || rc=1
      $NFT flush chain inet filter proxymode_forward || rc=1
      return $rc
    }

    rules_on() {
      # The fragment flushes the chains itself; the deletes only clear stale tables.
      $NFT delete table ip proxymode_nat 2>/dev/null
      $NFT delete table ip6 proxymode_nat 2>/dev/null
      $NFT -f "$RULES"
    }

    sync_pass() {
      prev="$mode"

      if [ ! -s "$RULES" ]; then
        rules_off
        printf 'direct\n' > "$STATUS"
        first=0
        changed=1
        return 0
      fi

      if $IS_CLASH_ON; then
        want=proxy
        if [ "$first" = "1" ] || ! rules_loaded; then
          if rules_on; then
            log "Clash is on -> redirect + killswitch loaded"
            # Verify the live state the fragment depends on (the TUN, mihomo's own
            # table, the FIB rules, the listeners) right at Mode-B start, instead
            # of waiting for the timer.
            request_verify
          else
            # Never record a mode that was not entered: `unenforced` is the state
            # for "Clash is on, enforcement is not loaded".
            loud "could not load the rules; enforcement absent"
            want=unenforced
          fi
        fi
      else
        want=direct
        # Retry while anything is left over, and log the exit from proxy mode once.
        if [ "$first" = "1" ] || [ "$mode" = "proxy" ] || rules_present; then
          if rules_off; then
            [ "$mode" = "proxy" ] && log "Clash is off -> direct mode"
          else
            echo "proxy-mode: could not tear the rules down" >&2
          fi
        fi
      fi

      mode="$want"
      first=0
      printf '%s\n' "$mode" > "$STATUS"

      # A mode change is the moment the claim and the world have to be compared.
      if [ -n "$prev" ] && [ "$mode" != "$prev" ]; then
        request_verify
      fi

      # Post-condition of the recorded mode; a mismatch is what keeps the user
      # offline after closing Clash, so fail visibly through the verifier unit.
      ok=1
      if [ "$mode" = "proxy" ]; then
        # Both families: a missing ip6 nat table means half the redirect is gone.
        { rules_loaded && rules_loaded6; } || ok=0
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
      elif [ "$interval" -lt "$MAX" ]; then
        interval=$(( interval * 2 > MAX ? MAX : interval * 2 ))
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
    description = "nftables fragment applied while Clash runs; filled in by network.nix.";
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
      description = "Load the redirect + killswitch only while Clash is running";
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
        # --once before the loop so the recorded mode is never stale.
        ExecStartPre = "${modeScript} --once";
        ExecStart = modeScript;
        Restart = "always";
        RestartSec = "5";
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

    # The coordinator: one event wakes nft (proxy-mode), DNS (dns-pac) and gost in
    # the same instant, so they switch together instead of three loops noticing at
    # different times. SIGUSR1 only interrupts each loop's sleep; every loop keeps
    # its own idempotent decision logic and stays the source of truth.
    systemd.services.proxy-net-wake = {
      description = "Wake the proxy supervisors on a Clash core state change";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = pkgs.writeShellScript "proxy-net-wake" ''
          for u in proxy-mode.service dns-pac.service gost-pac.service; do
            ${pkgs.systemd}/bin/systemctl kill -s USR1 --kill-whom=main "$u" 2>/dev/null || true
          done
        '';
      };
    };
  };
}

