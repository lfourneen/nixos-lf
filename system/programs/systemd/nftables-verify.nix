{ pkgs, config, lib, ... }:

let
  m = config.my.machine;
  port = m.ports;

in
{
  # A verifier reports a machine-readable result even when a tool fails. Its
  # exit status deliberately does not prevent a corrective nixos-rebuild.
  systemd.services.nftables-verify = {
    description = "Verify ordered nftables policy and proxy runtime";
    after = [ "nftables.service" "proxy-mode.service" "clash-verge.service" "dnsmasq.service" "unbound.service" ];
    wants = [ "nftables.service" "proxy-mode.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      NoNewPrivileges = true;
      RuntimeDirectory = "netsec";
      RuntimeDirectoryMode = "0755";
      RuntimeDirectoryPreserve = true;
    };
    script = ''
      set -Eeuo pipefail
      PATH=${lib.makeBinPath [ pkgs.coreutils pkgs.gnugrep pkgs.systemd pkgs.iproute2 pkgs.dnsutils pkgs.util-linux pkgs.jq ]}
      NFT=${pkgs.nftables}/bin/nft
      RULE_CHECK=${config.my.proxy.rulesCheck}
      EXPECTED=${config.my.proxy.expectedRules}
      IS_CLASH_ON=${config.my.proxy.isClashOn}
      FLAG=/run/netsec/failed
      RESULT=/run/netsec/result
      snapshot=/run/netsec/rules.$$.json
      completed=0
      degraded=0
      atomic_write() { printf '%s\n' "$2" > "$1.$$" && mv -f "$1.$$" "$1"; }
      fail() {
        completed=1
        echo "nftables-verify: FAIL: $1" >&2
        atomic_write "$FLAG" "nftables-verify: $1" || true
        atomic_write "$RESULT" failed || true
        ${pkgs.systemd}/bin/systemd-cat -t netsec-alert -p err ${pkgs.coreutils}/bin/echo "netsec-alert: $1" || true
        wall "NETSEC ALERT: $1" || true
        exit 0
      }
      on_exit() {
        rc=$?
        trap - EXIT
        rm -f "$snapshot"
        if [ "$completed" != 1 ]; then fail "verifier terminated before completion (exit $rc)"; fi
        exit 0
      }
      trap on_exit EXIT
      warn() { echo "nftables-verify: WARN: $1" >&2; degraded=1; }
      # Lock only the policy snapshot, not slow liveness probes. All writers use
      # this lock, so the status and JSON snapshot describe the same commit.
      mkdir -p /run/proxy-mode
      exec 9>/run/proxy-mode/lock
      flock -s -w 10 9 || fail 'cannot acquire policy snapshot lock'
      version=$(cat /run/proxy-mode/version)
      mode=$(cat /run/proxy-mode/status)
      case "$mode" in proxy|blocked|direct) ;; *) fail "invalid enforcement mode: $mode" ;; esac
      $NFT -j list ruleset > "$snapshot" || fail 'cannot read kernel nftables state'
      chmod 600 "$snapshot"
      "$RULE_CHECK" compare "$EXPECTED/$mode.json" "$snapshot" \
        || fail "ordered rules/hook/policy differ from this generation ($mode)"
      flock -u 9

      # Ignore a liveness result collected across a newer commit; the mode-change
      # notification will schedule another check, and the timer remains a backstop.
      still_current() { [ "$(cat /run/proxy-mode/version 2>/dev/null)" = "$version" ]; }
      live_fail() { if still_current; then fail "$1"; else completed=1; exit 0; fi; }
      listening() {
        for _ in 1 2 3 4 5; do
          if ss -lntH "sport = :$1" | grep -Fq "127.0.0.1:$1"; then return 0; fi
          sleep 1
        done
        return 1
      }
      listening ${toString port.dnsmasq} || live_fail 'dnsmasq listener missing'
      listening ${toString port.unbound} || live_fail 'unbound listener missing'
      live_uid=$(systemctl show -p UID --value unbound.service)
      case "$live_uid" in
        ""|"[not set]"|${toString config.users.users.unbound.uid}) ;;
        *) live_fail "unbound UID differs from firewall: $live_uid" ;;
      esac
      [ "$(cat /proc/sys/net/ipv4/ip_forward)" = 1 ] || live_fail 'IPv4 forwarding disabled'

      case "$mode" in
        proxy)
          $IS_CLASH_ON || live_fail 'proxy mode without a core in the service cgroup'
          ${lib.optionalString config.my.proxy.tunMode ''
          ip -o link show dev ${lib.escapeShellArg config.my.proxy.tunDev} | grep -q ',UP' || live_fail 'TUN down'
          $NFT list table inet mihomo >/dev/null || live_fail 'mihomo auto-redirect table missing'
          for family in 4 ${lib.optionalString config.networking.enableIPv6 "6"}; do
            ip -"$family" rule show | grep -q 'lookup ${toString config.my.proxy.routeTable}' || live_fail "IPv$family TUN rule missing"
            ip -"$family" route show table ${toString config.my.proxy.routeTable} | grep -q 'dev ${config.my.proxy.tunDev}' || live_fail "IPv$family TUN route missing"
          done
          ''}
          listening ${toString port.gostHttp} || live_fail 'gost HTTP listener missing'
          listening ${toString port.gostRedirect} || live_fail 'gost redirect listener missing'
          ss -lnteH 'sport = :${toString port.mihomoMixed}' | grep -q 'cgroup:/system.slice/clash-verge.service' || live_fail 'mixed listener identity mismatch'
          ss -lnteH 'sport = :${toString port.mihomoDns}' | grep -q 'cgroup:/system.slice/clash-verge.service' || live_fail 'DNS listener identity mismatch'
          if [ "$(cat /run/dns-upstream/status)" != proxy ]; then
            warn 'DNS has not converged to mihomo'
          else
            answer=$(dig +time=2 +tries=1 +short @127.0.0.1 -p ${toString port.dnsmasq} "$(date +%s%N).probe.example.com" A)
            ${if config.my.proxy.tunMode then ''
            printf '%s\n' "$answer" | "$RULE_CHECK" address ${lib.escapeShellArg config.my.proxy.fakeIpNet} >/dev/null || live_fail 'client DNS did not return a fake-IP inside the configured CIDR'
            '' else ''
            [ -n "$answer" ] || live_fail 'client DNS did not answer'
            ''}
          fi
          ;;
        blocked)
          # core-gone and service-gone are valid protected states. A missing
          # process must not make a correctly installed Mode C fail verification.
          ;;
        direct)
          if $IS_CLASH_ON; then live_fail 'direct mode while a core is running'; fi
          ${lib.optionalString config.my.proxy.tunMode ''
          # With a required TUN, direct mode must have no TUN device: its
          # presence means the GUI's tun.enable is overriding my.proxy.tunMode.
          # Retry briefly so a core's own teardown is not judged mid-flight.
          tun_gone=1
          for _ in 1 2 3; do
            if ! ip link show dev ${lib.escapeShellArg config.my.proxy.tunDev} >/dev/null 2>&1; then
              tun_gone=1; break
            fi
            tun_gone=0; sleep 1
          done
          [ "$tun_gone" = 1 ] || live_fail "direct mode is recorded but the ${config.my.proxy.tunDev} device exists (the GUI tun.enable is overriding my.proxy.tunMode)"
          ''}
          ;;
      esac
      if ! still_current; then completed=1; exit 0; fi
      # Do not erase an alert owned by a different failing unit.
      if [ -e "$FLAG" ] && grep -q '^nftables-verify:' "$FLAG"; then rm -f "$FLAG"; fi
      if [ "$degraded" = 1 ]; then result=degraded; else result=ok; fi
      atomic_write "$RESULT" "$result"
      atomic_write /run/netsec/verified-version "$version"
      completed=1
      echo "nftables-verify: $result (mode: $mode, version: $version)"
    '';
  };

  systemd.timers.nftables-verify = {
    wantedBy = [ "timers.target" ];
    timerConfig = { OnBootSec = "90s"; OnUnitActiveSec = "5min"; AccuracySec = "30s"; };
  };

  # Repair the owned static policy, not merely netlink access. This takes the
  # same atomic loader/lock path as activation and never flushes foreign tables.
  systemd.services.nftables-verify-repair = {
    description = "Rebuild owned firewall policy and recheck";
    serviceConfig.Type = "oneshot";
    script = ''
      set -e
      ${pkgs.systemd}/bin/systemctl restart nftables.service
      ${pkgs.systemd}/bin/systemctl restart proxy-mode.service
      ${pkgs.systemd}/bin/systemctl start proxy-net-wake.service
      ${pkgs.systemd}/bin/systemctl restart nftables-verify.service
    '';
  };
}

