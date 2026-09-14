{ lib, ... }:

{
  # Single source of truth for TUN mode; home/config/cvr-merge.nix reads it
  # via osConfig so the clash Merge template can't drift out of sync.
  options.my.proxy.tunMode = lib.mkOption {
    type = lib.types.bool;
    default = true;
    description = "TUN transparent-proxy mode; the home-side clash Merge template reads this via osConfig.";
  };
}

