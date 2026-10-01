{ pkgs, config, ... }:

let
  # The fragment this generation applies in proxy mode: the live chains are
  # compared against this exact text, so a gutted or stale ruleset shows up.
  fragment = pkgs.writeText "proxymode-rules.nft" config.my.proxy.proxyModeRules;

in
{
  # Compares the live nftables state with what this generation writes and with the
  # mode proxy-mode recorded. It reads kernel state, never the status file alone,
  # and never repairs anything -- repair is the opt-in unit below.
  systemd.services.nftables-verify = {
    description = "Verify that the live nftables state matches this generation and the mode";
    after = [ "nftables.service" "proxy-mode.service" "clash-verge.service" ];
    wants = [ "nftables.service" "proxy-mode.service" ];
    # A health check, not a unit whose failure may fail activation. It runs at boot
    # (after the Clash service), on proxy-mode's request and every 5 min from the
    # timer below, but it must never enter the failed state: `nh`/`nixos-rebuild`
    # reports any failed unit during activation, so a runtime Mode-B problem (e.g.
    # the TUN is down while the core runs) would block the very rebuild meant to fix
    # it. The script reports through the journal + /run/netsec/failed and always
    # exits 0, so the default `restartIfChanged` is kept -- a changed definition
    # takes effect on the next run, not deferred behind a "don't restart" flag.
    wantedBy = [ "multi-user.target" ];

    serviceConfig = {
      Type = "oneshot";
      # No RemainAfterExit: the timer below has to be able to start this unit again.
      NoNewPrivileges = true;
    };

    script = ''
      set -euo pipefail

      # Belt and suspenders: this unit must never enter the failed state, or a
      # rebuild would be blocked by the very problem the probe found. fail() already
      # exits 0; this absorbs an unexpected shell error too.
      trap 'exit 0' EXIT

      NFT=${pkgs.nftables}/bin/nft
      IP=${pkgs.iproute2}/bin/ip
      SS=${pkgs.iproute2}/bin/ss
      SYSTEMCTL=${pkgs.systemd}/bin/systemctl
      DIG=${pkgs.dnsutils}/bin/dig
      GREP=${pkgs.gnugrep}/bin/grep
      SED=${pkgs.gnused}/bin/sed
      CAT=${pkgs.coreutils}/bin/cat
      RM=${pkgs.coreutils}/bin/rm
      SORT=${pkgs.coreutils}/bin/sort
      DIFF=${pkgs.diffutils}/bin/diff
      DATE=${pkgs.coreutils}/bin/date
      SLEEP=${pkgs.coreutils}/bin/sleep

      FRAGMENT=${fragment}
      STATUS=/run/proxy-mode/status
      TUNDEV=${config.my.proxy.tunDev}
      GOST_REDIRECT=${toString config.my.machine.ports.gostRedirect}
      MIHOMO_MIXED=${toString config.my.machine.ports.mihomoMixed}
      MIHOMO_DNS=${toString config.my.machine.ports.mihomoDns}
      CLIENT_DNS=${toString config.my.machine.ports.dnsmasq}
      DOT_PORT=${toString config.my.machine.ports.dot}
      EXPECT_UNBOUND_UID=${toString config.users.users.unbound.uid}
      IS_CLASH_ON=${config.my.proxy.isClashOn}
      FLAG=/run/netsec/failed

      # A failing check alerts loudly, then exits 0 on purpose: this unit must never
      # enter the failed state, or `nixos-rebuild` (which reports any failed unit
      # during activation) would refuse to apply. The alert is the journal line,
      # /run/netsec/failed and the wall broadcast.
      fail() {
        msg="$1"
        echo "nftables-verify: FAIL: $msg" >&2
        ${pkgs.coreutils}/bin/mkdir -p /run/netsec
        printf '%s\n' "nftables-verify: $msg" > "$FLAG"
        ${pkgs.systemd}/bin/systemd-cat -t netsec-alert -p err ${pkgs.coreutils}/bin/echo \
          "netsec-alert: $msg" || true
        ${pkgs.util-linux}/bin/wall "NETSEC ALERT: $msg" || true
        exit 0
      }
      warn() { echo "nftables-verify: WARN: $1" >&2; }

      # --- live state readers -------------------------------------------------
      # nft prints chains with a type line and counters expanded, so normalise
      # both sides before comparing.
      live_chain() {
        $NFT list chain inet filter "$1" 2>/dev/null \
          | $GREP -vE '^[[:space:]]*(table|chain|\}|type )' \
          | $SED -E 's/counter packets [0-9]+ bytes [0-9]+/counter/' \
          | $SED -E 's/^[[:space:]]+//; s/[[:space:]]+$//' \
          | $GREP -vE '^[[:space:]]*$'
      }
      live_nat() {
        $NFT list table "$1" "$2" 2>/dev/null \
          | $GREP -vE '^[[:space:]]*(table|chain|\}|type )' \
          | $SED -E 's/counter packets [0-9]+ bytes [0-9]+/counter/' \
          | $SED -E 's/^[[:space:]]+//; s/[[:space:]]+$//' \
          | $GREP -vE '^[[:space:]]*$'
      }
      expected_chain() {
        $GREP -E "^[[:space:]]*add rule inet filter $1 " "$FRAGMENT" \
          | $SED -E "s/^[[:space:]]*add rule inet filter $1 //"
      }
      # The nat tables are inline in the fragment, not `add rule` lines, so their
      # expected rules are read out of the block. Only `chain output` exists there.
      expected_table() {
        ${pkgs.gawk}/bin/awk -v hdr="table $1 $2 {" '
          index($0, hdr) == 1 { inside = 1; next }
          inside {
            if ($0 ~ /^[[:space:]]*chain /) next
            if ($0 ~ /^[[:space:]]*type /) next
            if ($0 ~ /^[[:space:]]*}[[:space:]]*$/) {
              if ($0 ~ /^}/) { inside = 0 }
              next
            }
            if ($0 ~ /^[[:space:]]*$/) next
            gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print
          }' "$FRAGMENT"
      }
      # nft canonicalises sets: a single-element set loses its braces, and the
      # element order is nft's own (numeric for CIDR sets, its own order for string
      # sets). Normalise both sides identically -- drop single-element braces and
      # sort every set's elements -- so the comparison tests the rules, not nft's
      # formatting. Without this, an exactly-correct ruleset compares unequal.
      norm_sets() {
        ${pkgs.gawk}/bin/awk '
          {
            line = $0; out = ""
            while (match(line, /\{[^{}]*\}/)) {
              out = out substr(line, 1, RSTART - 1)
              blk = substr(line, RSTART + 1, RLENGTH - 2)
              line = substr(line, RSTART + RLENGTH)
              n = split(blk, a, ",")
              for (i = 1; i <= n; i++) gsub(/^[ \t]+|[ \t]+$/, "", a[i])
              for (i = 1; i <= n; i++) for (j = i + 1; j <= n; j++) if (a[j] < a[i]) { t = a[i]; a[i] = a[j]; a[j] = t }
              if (n <= 1) out = out a[1]
              else { out = out "{ " a[1]; for (i = 2; i <= n; i++) out = out ", " a[i]; out = out " }" }
            }
            print out line
          }'
      }
      # Set equality: a count cannot tell a gutted ruleset from the intended one.
      cmp_rules() {
        local exp liv
        exp=$(printf '%s\n' "$2" | norm_sets | $SORT)
        liv=$(printf '%s\n' "$3" | norm_sets | $SORT)
        if [ "$exp" != "$liv" ]; then
          fail "$1: the live rules are not the ones this generation loads
$($DIFF <(printf '%s\n' "$exp") <(printf '%s\n' "$liv") || true)"
        fi
      }

      # The mode and the TUN legitimately disagree for a moment while Clash starts
      # or stops; wait up to 8 s for them to agree, then judge.
      mode_now=""
      for _ in 1 2 3 4 5 6 7 8; do
        mode_now=$($CAT "$STATUS" 2>/dev/null | $SED -E 's/[[:space:]]+//g' || echo unknown)
        if [ "$mode_now" = proxy ] && ! $IP link show dev "$TUNDEV" >/dev/null 2>&1; then
          sleep 1
          continue
        fi
        if [ "$mode_now" = direct ] && $IP link show dev "$TUNDEV" >/dev/null 2>&1; then
          sleep 1
          continue
        fi
        break
      done

      # --- static skeleton ---
      for c in input output forward proxymode_drops proxymode_tail proxymode_forward; do
        $NFT list chain inet filter "$c" >/dev/null 2>&1 \
          || fail "inet filter $c is missing: the static ruleset did not load completely"
      done
      $NFT list chain inet filter input | $GREP -q 'policy drop' \
        || fail "inet filter input has no 'policy drop' policy: inbound is not default-denied"
      out=$($NFT list chain inet filter output)
      $GREP -q 'jump proxymode_drops' <<<"$out" || fail "chain output no longer jumps to proxymode_drops"
      $GREP -q 'jump proxymode_tail' <<<"$out" || fail "chain output no longer jumps to proxymode_tail"
      $NFT list chain inet filter forward | $GREP -q 'jump proxymode_forward' \
        || fail "chain forward no longer jumps to proxymode_forward"

      # --- uid operands match the daemons ---
      # `[not set]`/empty means the unit has not started yet (common at boot): its
      # uid is pinned in the config, so there is nothing to compare until it runs.
      live_uid=$($SYSTEMCTL show -p UID --value unbound.service 2>/dev/null || true)
      if [ -n "$live_uid" ] && [ "$live_uid" != "[not set]" ] && [ "$live_uid" != "$EXPECT_UNBOUND_UID" ]; then
        fail "unbound runs as uid $live_uid but the rules were built for $EXPECT_UNBOUND_UID: its DoT exemption matches no process"
      fi
      $NFT list chain inet filter output | $GREP -q "skuid $EXPECT_UNBOUND_UID .*tcp dport $DOT_PORT accept" \
        || fail "the live rules carry no 'skuid $EXPECT_UNBOUND_UID ... tcp dport $DOT_PORT accept': unbound's DoT exemption is missing"

      # --- the recorded mode, and whether the core really is running ----------
      mode=$($CAT "$STATUS" 2>/dev/null || echo unknown)
      mode=$(printf '%s' "$mode" | $SED -E 's/[[:space:]]+//g')
      core=off
      $IS_CLASH_ON 2>/dev/null && core=on

      case "$mode" in
        proxy)
          [ "$core" = on ] \
            || fail "/run/proxy-mode/status says proxy but no Clash core is in clash-verge.service's cgroup"
          $IP link show dev "$TUNDEV" >/dev/null 2>&1 \
            || fail "proxy mode is recorded but the $TUNDEV device does not exist: nothing is captured"
          $IP -o link show dev "$TUNDEV" | $GREP -q ',UP' \
            || fail "the $TUNDEV device exists but is not up"
          $NFT list tables | $GREP -qx 'table inet mihomo' \
            || fail "mihomo's own inet table is missing: its auto-redirect and dns-hijack rules are gone until the TUN restarts"
          $IP rule show | $GREP -q 'lookup 2022' \
            || fail "no FIB rule selects sing-tun's table 2022: the routing half of the capture is gone"
          $IP route show table 2022 | $GREP -q "dev $TUNDEV" \
            || fail "table 2022 carries no route via $TUNDEV"
          $SS -lntH | $GREP -q "127\.0\.0\.1:$GOST_REDIRECT" \
            || fail "nothing listens on gost's :$GOST_REDIRECT: flows the TUN does not carry have no path"
          $SS -lntH | $GREP -q "127\.0\.0\.1:$MIHOMO_MIXED" \
            || fail "nothing listens on mihomo's :$MIHOMO_MIXED"
          $SS -lnteH "sport = :$MIHOMO_DNS" | $GREP -q 'cgroup:/system.slice/clash-verge.service' \
            || fail "the DNS listener on :$MIHOMO_DNS does not belong to clash-verge.service: something else answers DNS"
          for t in "ip proxymode_nat" "ip6 proxymode_nat"; do
            set -- $t
            $NFT list table "$1" "$2" >/dev/null 2>&1 \
              || fail "$1 $2 is missing although proxy mode is recorded: the redirect is incomplete"
          done

          cmp_rules "chain proxymode_drops" "$(expected_chain proxymode_drops)" "$(live_chain proxymode_drops)"
          cmp_rules "chain proxymode_tail" "$(expected_chain proxymode_tail)" "$(live_chain proxymode_tail)"
          cmp_rules "chain proxymode_forward" "$(expected_chain proxymode_forward)" "$(live_chain proxymode_forward)"
          cmp_rules "table ip proxymode_nat" "$(expected_table ip proxymode_nat)" "$(live_nat ip proxymode_nat)"
          cmp_rules "table ip6 proxymode_nat" "$(expected_table ip6 proxymode_nat)" "$(live_nat ip6 proxymode_nat)"

          # A name the validating upstream refuses must not come back as an answer
          # here. Judge only once dns-pac has actually put the client resolver on
          # mihomo -- proxy-mode's status leads dns-pac's, and while they disagree
          # the resolver is still on the non-validating DoT, which is Mode A by
          # design, not a downgrade.
          dns_state=$($CAT /run/dns-pac/status 2>/dev/null | $SED -E 's/[[:space:]]+//g')
          if [ "$dns_state" != "proxy" ]; then
            warn "dns-pac is [$dns_state], not proxy yet: the client resolver is not on the validating upstream, so DNSSEC is not judged"
          else
            ctl=$($DIG +time=3 +tries=1 +short @127.0.0.1 -p "$CLIENT_DNS" example.com 2>/dev/null | $GREP -cE '^[0-9a-fA-F:]' || true)
            if [ "$ctl" = 0 ]; then
              warn "the client resolver (:$CLIENT_DNS) did not answer a control query; DNS in proxy mode is broken, so validation is not judged"
            else
              # dns-pac restarts dnsmasq asynchronously, so the first probe can still
              # hit the previous upstream: retry and pass as soon as the name is
              # refused (SERVFAIL) or swallowed (no status). Only a resolver that
              # keeps answering for every attempt counts as a downgrade. dig writes
              # its diagnostics to stdout, so parse the status line, not a line count.
              refused=0
              st=""
              for _ in 1 2 3 4 5; do
                probe="$($DATE +%s%N).dnssec-failed.org"
                st=$($DIG +time=3 +tries=1 @127.0.0.1 -p "$CLIENT_DNS" "$probe" 2>/dev/null | $GREP -oE 'status: [A-Z]+' | head -1 || true)
                case "$st" in
                  "status: SERVFAIL"|"") refused=1; break ;;
                esac
                $SLEEP 1
              done
              [ "$refused" = 1 ] \
                || fail "the client resolver kept answering '$probe' with [$st] although the validating upstream refuses it: a non-validating resolver is in the path"
            fi
          fi
          ;;
        unenforced)
          fail "proxy-mode recorded 'unenforced': Clash is running but the enforcement fragment is not loaded"
          ;;
        direct)
          for c in proxymode_drops proxymode_tail proxymode_forward; do
            n=$(live_chain "$c" | $GREP -c . || true)
            [ "$n" = 0 ] || fail "direct mode is recorded, but inet filter $c still holds $n rule(s)"
          done
          if $NFT list table ip proxymode_nat >/dev/null 2>&1; then
            fail "direct mode is recorded, but the IPv4 redirect table is still loaded"
          fi
          if $NFT list table ip6 proxymode_nat >/dev/null 2>&1; then
            fail "direct mode is recorded, but the IPv6 redirect table is still loaded"
          fi
          if $IP link show dev "$TUNDEV" >/dev/null 2>&1; then
            fail "direct mode is recorded but the $TUNDEV device exists: the GUI's tun.enable is overriding my.proxy.tunMode"
          fi
          ;;
        unknown|"")
          if [ "$core" = on ]; then
            fail "/run/proxy-mode/status is unreadable while a Clash core is running: the mode cannot be verified"
          fi
          warn "proxy-mode state unreadable and no Clash core running; checked the static skeleton and the uid operands only"
          ;;
        *)
          fail "unrecognised mode '$mode' in $STATUS"
          ;;
      esac

      $RM -f "$FLAG" 2>/dev/null || true
      echo "nftables-verify: OK (mode: $mode)"
    '';
  };

  # Backstop for the state the boot run cannot know about yet: every 5 min, about
  # three journal lines on a healthy machine.
  systemd.timers.nftables-verify = {
    description = "Re-check the proxy mode and the firewall state";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "90s";
      OnUnitActiveSec = "5min";
      AccuracySec = "30s";
    };
  };

  # Opt-in repair, never automatic: a check that repairs its own failure can hide
  # it. Start it by hand when the alert fires, e.g.
  #   systemctl start nftables-verify-repair.service
  systemd.services.nftables-verify-repair = {
    description = "Repair the Mode-B enforcement after a failed verification";
    serviceConfig = {
      Type = "oneshot";
    };
    script = ''
      ${pkgs.systemd}/bin/systemctl try-restart proxy-mode.service || true
      ${pkgs.nftables}/bin/nft list tables >/dev/null 2>&1 \
        || ${pkgs.systemd}/bin/systemctl restart nftables.service || true
      ${pkgs.systemd}/bin/systemctl restart nftables-verify.service || true
    '';
  };
}

