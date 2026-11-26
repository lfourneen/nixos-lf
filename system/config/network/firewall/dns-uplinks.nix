{ config, lib }:

let
  m = config.my.machine;
  p = config.my.proxy;
  internal = lib.unique ([ "lo" p.tunDev config.services.tailscale.interfaceName
    m.vmBridge m.waydroidBridge m.hotspot.interface ] ++ p.guestInterfaces
    ++ lib.optional (p.ipTunnel != null) p.ipTunnelPolicy.device);
    
in
{
  # These selectors govern host DNS and DHCP clients. Guest forwarding
  # retains its own explicitly declared egress interfaces.
  inherit internal;
  match = direction: ''${direction} != { ${lib.concatMapStringsSep ", " (i: ''"${i}"'') internal} }'';

  dotRules = uid: action: map (endpoint:
    "meta skuid ${toString uid} ${if lib.hasInfix ":" endpoint.address then "ip6" else "ip"} daddr ${endpoint.address} tcp dport ${toString m.ports.dot} ${action}accept"
  ) m.dotEndpoints;
}
