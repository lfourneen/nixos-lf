{ pkgs, config, lib, ... }:

let
  rules = pkgs.writeText "proxymode-rules.nft" config.my.proxy.proxyModeRules;

  # Single source of truth for "Clash is on": dns-pac.nix calls the same script, so
  # the criterion cannot drift between the two supervisors.
  isClashOn = pkgs.writeShellScript "is-clash-on" ''
    # clash-verge.service also runs an always-on root helper, so its state alone is
    # not "Clash is on": require the core in the unit's cgroup (unspoofable) or the GUI.
    ${pkgs.systemd}/bin/systemctl is-active --quiet clash-verge.service || exit 1
    for p in $(${pkgs.coreutils}/bin/cat /sys/fs/cgroup/system.slice/clash-verge.service/cgroup.procs 2>/dev/null); do
      ${pkgs.gnugrep}/bin/grep -qa 'bin/verge-mihomo' "/proc/$p/cmdline" 2>/dev/null && exit 0
    done
    ${pkgs.gnugrep}/bin/grep -qa '^clash-verge' /proc/[0-9]*/cmdline 2>/dev/null && exit 0
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
    [ "''${1:-}" = "--once" ] && once=1

    log() { echo "proxy-mode: $*" >&2; }
    loud() {
      echo "proxy-mode: $*" >&2
      ${pkgs.systemd}/bin/systemd-cat -t proxy-mode -p err ${pkgs.coreutils}/bin/echo "proxy-mode: $*" || true
    }

    # The fragment loads in one transaction, so one probe covers all of it.
    rules_loaded() { $NFT list table ip proxymode_nat >/dev/null 2>&1; }

    # Anything that would still redirect or drop after a tear-down.
    rules_present() {
      rules_loaded && return 0
      $NFT list chain inet filter proxymode_drops 2>/dev/null | $GREP -q 'counter' && return 0
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
      return $rc
    }

    rules_on() {
      # The fragment flushes the chains itself; the deletes only clear stale tables.
      $NFT delete table ip proxymode_nat 2>/dev/null
      $NFT delete table ip6 proxymode_nat 2>/dev/null
      $NFT -f "$RULES"
    }

    sync_pass() {
      if [ ! -s "$RULES" ]; then
        rules_off
        printf 'direct\n' > "$STATUS"
        first=0
        return 0
      fi

      if $IS_CLASH_ON; then
        want=proxy
        if [ "$first" = "1" ] || ! rules_loaded; then
          if rules_on; then
            log "Clash is on -> redirect + killswitch loaded"
          else
            echo "proxy-mode: could not load the rules" >&2
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

      # Post-condition of the recorded mode; a mismatch is what keeps the user
      # offline after closing Clash, so fail visibly through the verifier unit.
      ok=1
      if [ "$mode" = "proxy" ]; then
        rules_loaded || ok=0
      else
        rules_present && ok=0
      fi
      if [ "$ok" = "1" ]; then
        fails=0
      else
        fails=$((fails + 1))
        if [ "$fails" = "1" ] || [ $((fails % 6)) -eq 0 ]; then
          loud "nft state does not match mode $mode (attempt $fails)"
          $SYSTEMCTL restart --no-block nftables-verify.service 2>/dev/null
        fi
      fi
      return 0
    }

    while true; do
      sync_pass
      [ "$once" = "1" ] && exit 0
      $SLEEP 2
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
      wants = [ "nftables.service" ];
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
  };
}

