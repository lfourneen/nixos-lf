{
  # Private veth endpoints, never physical NICs or learned uplink addresses.
  # All consumers use my.proxy.coreTopology, with configurable coreLink values.
  namespace = "lf-clash-core";
  namespacePath = "/run/netns/lf-clash-core";
  peer = "lf-host";
  hostMac = "02:4c:46:00:00:01";
  coreMac = "02:4c:46:00:00:02";
  host4 = "10.255.253.1";
  core4 = "10.255.253.2";
  subnet4 = "10.255.253.0/30";
  host6 = "fd46:6c66:636f::1";
  core6 = "fd46:6c66:636f::2";
  subnet6 = "fd46:6c66:636f::/64";
  observer = "/run/clash-network/observer.sock";
}
