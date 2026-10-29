{ config, lib, ... }:

let
  m = config.my.machine;
  ipv4 = lib.unique (m.privateV4 ++ [ "0.0.0.0/8" "127.0.0.0/8" "169.254.0.0/16"
    m.vmSubnet m.waydroidSubnet ] ++ lib.optional config.my.hardening.hotspot.enable m.hotspot.subnet);
  ipv6 = m.ulaV6 ++ m.linkLocalV6 ++ [ "::/128" "::1/128" ];
  mapped = map (cidr: let parts = lib.splitString "/" cidr;
    in "::ffff:${builtins.head parts}/${toString (96 + lib.toInt (builtins.elemAt parts 1))}") ipv4;

in
{
  # 100.64.0.0/10 stays in the list even though the uplink itself is CGNAT:
  # Tailscale peers are reached by literal addresses or the authenticated
  # `*.ts.net` suffix (both exempt), so dropping it would only re-open the
  # domain-to-CGNAT path the integration test exercises with 100.67.0.1.
  inherit ipv4 ipv6; all = ipv4 ++ ipv6 ++ mapped;
}
