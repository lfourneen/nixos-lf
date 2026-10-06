{ lib, config, ... }:

{
  # Single source of truth for TUN mode; home/config/cvr-merge.nix reads it
  # via osConfig so the clash Merge template can't drift out of sync.
  options.my.proxy.tunMode = lib.mkOption {
    type = lib.types.bool;
    default = true;
    description = "TUN transparent-proxy mode; the home-side clash Merge template reads this via osConfig.";
  };

  # Single source of truth for the TUN interface name; the firewall rules and
  # the clash Merge template both read it, so a GUI rename can't silently break
  # the input-chain accept or the reverse-default-deny exception.
  options.my.proxy.tunDev = lib.mkOption {
    type = lib.types.str;
    default = config.my.machine.tunDevice;
    description = "Clash TUN interface name; firewall rules and the home-side clash Merge template read this via osConfig.";
  };

  # Single source of truth for the fake-ip pool. The home-side clash Merge
  # template writes it into `dns.fake-ip-range` while TUN mode is on, and
  # nftables-verify checks that the client resolver answers inside it. Only
  # meaningful with tunMode: outside a TUN nothing maps a fake address back to
  # its domain, so the template stays redir-host when tunMode is false.
  options.my.proxy.fakeIpRange = lib.mkOption {
    type = lib.types.str;
    default = "198.18.0.1/16";
    description = "Clash fake-ip pool (RFC 2544 benchmarking range); used with tunMode and asserted by nftables-verify.";
  };
}

