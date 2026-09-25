{ pkgs, ... }:

{
  # nftables loads once at boot with no post-check. This unit verifies the static
  # skeleton and that the enforcement rules match the mode proxy-mode recorded.
  systemd.services.nftables-verify = {
    description = "Verify the loaded nftables ruleset still carries the egress guards";
    after = [ "nftables.service" "proxy-mode.service" ];
    wants = [ "nftables.service" "proxy-mode.service" ];
    wantedBy = [ "multi-user.target" ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      NoNewPrivileges = true;
    };

    script = ''
      set -euo pipefail
      nft=${pkgs.nftables}/bin/nft

      fail() {
        echo "nftables-verify: FAILED: $1" >&2
        exit 1
      }

      # Static skeleton: a failed or partial load leaves these chains missing.
      input=$($nft list chain inet filter input)           || fail "inet filter input is missing"
      output=$($nft list chain inet filter output)         || fail "inet filter output is missing"
      drops=$($nft list chain inet filter proxymode_drops) || fail "inet filter proxymode_drops is missing"
      tail=$($nft list chain inet filter proxymode_tail)   || fail "inet filter proxymode_tail is missing"

      # Listeners are only safe while the input chain still default-denies.
      grep -q 'policy drop' <<<"$input" || fail "inet filter input lost 'policy drop'"
      grep -q 'jump proxymode_drops' <<<"$output" || fail "inet filter output lost the enforcement jump"
      grep -q 'dport 853 accept' <<<"$output" || fail "unbound DoT accept rule is missing"

      # Proxy mode adds the drops and the redirect; direct mode must have none of
      # them left over. proxy-mode.service owns the transitions and records the mode.
      mode=$(${pkgs.coreutils}/bin/cat /run/proxy-mode/status 2>/dev/null || echo unknown)
      case "$mode" in
        proxy)
          drops_n=$(grep -c 'counter .*drop' <<<"$drops" || true)
          [ "$drops_n" -ge 2 ] || fail "proxy mode without the killswitch drops (found $drops_n)"
          tail_n=$(grep -c 'oifname != { "lo",' <<<"$tail" || true)
          [ "$tail_n" -ge 2 ] || fail "proxy mode without the chain-tail default-deny (found $tail_n)"
          nat=$($nft list table ip proxymode_nat 2>/dev/null) || fail "proxy mode without the IPv4 redirect table"
          grep -q 'redirect to :33333' <<<"$nat" || fail "ip proxymode_nat lost the :33333 redirect"
          nat6=$($nft list table ip6 proxymode_nat 2>/dev/null) || fail "proxy mode without the IPv6 redirect table"
          grep -q 'redirect to :33333' <<<"$nat6" || fail "ip6 proxymode_nat lost the :33333 redirect"
          ;;
        direct)
          drops_n=$(grep -c 'counter .*drop' <<<"$drops" || true)
          tail_n=$(grep -c 'oifname != { "lo",' <<<"$tail" || true)
          [ "$drops_n" -eq 0 ] || fail "direct mode with $drops_n leftover killswitch drop(s)"
          [ "$tail_n" -eq 0 ] || fail "direct mode with $tail_n leftover chain-tail drop(s)"
          if $nft list table ip proxymode_nat >/dev/null 2>&1; then
            fail "direct mode with a leftover IPv4 redirect table"
          fi
          if $nft list table ip6 proxymode_nat >/dev/null 2>&1; then
            fail "direct mode with a leftover IPv6 redirect table"
          fi
          ;;
        *)
          echo "nftables-verify: proxy-mode state unreadable, checked the static skeleton only" >&2
          ;;
      esac

      echo "nftables-verify: ruleset OK (mode: $mode)"
    '';
  };
}

