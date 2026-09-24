{ pkgs, ... }:

{
  # nftables loads once at boot with three retries and no check: on failure the host
  # runs with no killswitch and no :33333 redirect. This unit makes that state loud.
  systemd.services.nftables-verify = {
    description = "Verify the loaded nftables ruleset still carries the egress guards";
    after = [ "nftables.service" ];
    wants = [ "nftables.service" ];
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

      # A failed or partially applied load leaves the chain absent: primary check.
      input=$($nft list chain inet filter input)   || fail "inet filter input is missing"
      output=$($nft list chain inet filter output) || fail "inet filter output is missing"
      nat=$($nft list chain ip nat output)         || fail "ip nat output is missing"
      nat6=$($nft list chain ip6 nat output)       || fail "ip6 nat output is missing"

      # Listeners are only safe while the input chain still default-denies.
      grep -q 'policy drop' <<<"$input" || fail "inet filter input lost 'policy drop'"

      # The killswitch is the last layer standing for source-bound egress: the
      # NIC-scoped drops and the chain-tail reverse deny must all be present.
      drops=$(grep -c 'counter .*drop' <<<"$output" || true)
      [ "$drops" -ge 2 ] || fail "killswitch drops missing from inet filter output (found ''${drops})"

      # Counted separately: the two NIC-scoped drops alone satisfy the check above.
      tail_denies=$(grep -c 'oifname != { "lo", "Mihomo" }' <<<"$output" || true)
      [ "$tail_denies" -ge 2 ] || fail "chain-tail reverse default-deny missing (found ''${tail_denies})"

      # Not a security layer: without it Clash-down TCP is still dropped, but as a
      # silent timeout instead of an immediate ECONNREFUSED.
      grep -q 'redirect to :33333' <<<"$nat"  || fail "ip nat output lost the :33333 redirect"
      grep -q 'redirect to :33333' <<<"$nat6" || fail "ip6 nat output lost the :33333 redirect"

      # unbound's DoT is the only resolver left when Clash is down.
      grep -q 'dport 853 accept' <<<"$output" || fail "unbound DoT accept rule is missing"

      echo "nftables-verify: ruleset OK"
    '';
  };
}

