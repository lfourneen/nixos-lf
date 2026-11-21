{ lib, leaseEnabled }:

let
  # Temporary packet priority bits, separate from the socket, routing and
  # conntrack marks that the primary policy inspects. Clear the bits at both
  # ends of each hook so a prior trip through a hook cannot authorize a packet.
  bits = {
    primary = 2147483648;
    mode = 1073741824;
    lease = 536870912;
    proxyDrops = 268435456;
    proxyTail = 134217728;
    service = 67108864;
  };
  mask = names: "0x" + lib.toHexString (lib.foldl' (sum: name: builtins.bitOr sum bits.${name}) 0 names);
  clear = "meta priority set meta priority & 0x03ffffff";
  stamp = names: "meta priority set meta priority | ${mask names}";
  acceptBeforeProxy = "${stamp [ "proxyDrops" "proxyTail" ]} accept";

  hooks = {
    output = { early = "-270"; late = "10"; };
    input = { early = "-20"; late = "10"; };
    forward = { early = "-20"; late = "10"; };
    prerouting = { early = "-270"; late = "-197"; };
  };

  proof = mode: hook:
    let proxy = mode == "proxy";
    in lib.optionals (hook != "prerouting") [ "primary" ]
      ++ lib.optionals (proxy && leaseEnabled) [ "lease" ]
      ++ lib.optionals (hook != "output" || !proxy) [ "mode" ]
      ++ lib.optionals (hook == "input") [ "service" ]
      ++ lib.optionals (hook == "output" && proxy) [ "service" "proxyDrops" "proxyTail" ];

  requireChain = hook: "lf_guard_require_${hook}";
  reset = lib.concatMapStrings (hook: "flush chain inet lf_policy_guard ${requireChain hook}\n")
    (lib.attrNames hooks);
in
{
  inherit stamp acceptBeforeProxy reset;

  # This table continues enforcing the current mode if lf_filter disappears.
  # A packet surviving an earlier drop rule still needs evidence that it ran
  # through the relevant policy chains for its hook.
  table = ''
    table inet lf_policy_guard {
    ${lib.concatStrings (lib.mapAttrsToList (hook: priority: ''
      chain lf_guard_clear_${hook} {
        type filter hook ${hook} priority ${priority.early}; policy accept;
        ${clear}
      }
      chain lf_guard_check_${hook} {
        type filter hook ${hook} priority ${priority.late}; policy accept;
        jump ${requireChain hook}
        ${clear}
      }
      chain ${requireChain hook} { }
    '') hooks)}
    }
  '';

  enforce = mode: reset + lib.concatMapStrings (hook:
    let expected = mask (proof mode hook);
    in "add rule inet lf_policy_guard ${requireChain hook} meta priority & ${expected} != ${expected} counter drop\n")
    (lib.attrNames hooks);
}
