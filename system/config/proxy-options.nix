{ lib, ... }:

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
    default = "Mihomo";
    description = "Clash TUN interface name; firewall rules and the home-side clash Merge template read this via osConfig.";
  };
}

