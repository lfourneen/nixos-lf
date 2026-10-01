{ lib, ... }:

# Everything specific to this machine: interfaces and their match keys, the TUN
# device, the uids the nft rules match, the ports the ruleset and its services
# share, and the address blocks treated as local.
#
# Porting: set hostName, wired/wireless (name plus MAC or PCI path), the hotspot
# and VM subnets if they differ, and the DoT upstreams.
{
  options.my.machine = {
    hostName = lib.mkOption { type = lib.types.str; default = "nixos"; };

    desktopUser = lib.mkOption {
      type = lib.types.str;
      default = "lfour";
      description = ''
        Desktop login user. Its Clash Verge runtime owns the mihomo control socket
        under /run/user/<uid>/, which the proxy event trigger watches to turn the
        supervisors from pollers into event-driven loops.
      '';
    };

    wired = {
      name = lib.mkOption {
        type = lib.types.str;
        default = "ens1";
        description = "Wired uplink; pinned by MAC because PCIe bus numbers can shift.";
      };
      mac = lib.mkOption { type = lib.types.str; default = "fc:5c:ee:c5:db:de"; };
      driver = lib.mkOption { type = lib.types.str; default = "r8169"; };
    };

    wireless = {
      name = lib.mkOption {
        type = lib.types.str;
        default = "wlo1";
        description = "Wireless interface; pinned by PCI path so MAC randomisation cannot move it.";
      };
      path = lib.mkOption { type = lib.types.str; default = "pci-0000:03:00.0"; };
      driver = lib.mkOption { type = lib.types.str; default = "mt7921e"; };
    };

    vmBridge = lib.mkOption { type = lib.types.str; default = "virbr0"; };
    tunDevice = lib.mkOption { type = lib.types.str; default = "Mihomo"; };

    uids = {
      unbound = lib.mkOption { type = lib.types.int; default = 983; };
      dnsmasq = lib.mkOption { type = lib.types.int; default = 985; };
      gost = lib.mkOption { type = lib.types.int; default = 987; };
    };

    # Ports the nftables rules and the services behind them must agree on.
    ports = {
      mihomoDns = lib.mkOption { type = lib.types.port; default = 1053; };
      dnsmasq = lib.mkOption { type = lib.types.port; default = 1054; };
      unbound = lib.mkOption { type = lib.types.port; default = 1055; };
      gostHttp = lib.mkOption { type = lib.types.port; default = 33332; };
      gostRedirect = lib.mkOption { type = lib.types.port; default = 33333; };
      mihomoMixed = lib.mkOption { type = lib.types.port; default = 7897; };
      mihomoTproxy = lib.mkOption { type = lib.types.port; default = 7896; };
      dot = lib.mkOption { type = lib.types.port; default = 853; };
      ntp = lib.mkOption { type = lib.types.port; default = 123; };
    };

    # Ranges the TUN deliberately leaves alone and the kill switch treats as
    # reachable. This uplink is CGNAT, so 100.64/10 is one of them.
    privateV4 = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "10.0.0.0/8" "172.16.0.0/12" "192.168.0.0/16" "100.64.0.0/10" ];
    };
    multicastV4 = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "224.0.0.0/4" ];
    };
    # nft takes the bare address, the TUN exclusion list the host form.
    limitedBroadcastV4 = lib.mkOption { type = lib.types.str; default = "255.255.255.255"; };
    # Kept apart because the ruleset lists the link-local block first and the TUN
    # exclusion list the other way round.
    linkLocalV6 = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "fe80::/10" ];
    };
    ulaV6 = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "fc00::/7" ];
    };
    multicastV6 = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "ff00::/8" ];
    };

    hotspot = {
      subnet = lib.mkOption { type = lib.types.str; default = "10.42.0.0/24"; };
      address = lib.mkOption { type = lib.types.str; default = "10.42.0.1"; };
    };
    vmSubnet = lib.mkOption { type = lib.types.str; default = "192.168.122.0/24"; };
    magicDns = lib.mkOption { type = lib.types.str; default = "100.100.100.100"; };

    # Encrypted fallback resolver (DoT) used while Clash is down.
    dotUpstreams = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "223.5.5.5" "223.6.6.6" ];
    };
    dotAuthName = lib.mkOption { type = lib.types.str; default = "dns.alidns.com"; };
  };
}

