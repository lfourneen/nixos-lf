{ lib, config, ... }:

let
  parts = builtins.match "([0-9]+)\\.([0-9]+)\\.([0-9]+)\\.([0-9]+)/([0-9]+)" config.my.proxy.fakeIpRange;
  nums = if parts == null then [ ] else map lib.toInt parts;
  valid = parts != null && lib.length nums == 5
    && lib.all (n: n >= 0 && n <= 255) (lib.take 4 nums)
    && builtins.elemAt nums 4 >= 8 && builtins.elemAt nums 4 <= 30;
  prefix = builtins.elemAt nums 4;
  pow2 = n: lib.foldl' (v: _: v * 2) 1 (lib.range 1 n);
  address = lib.foldl' (v: n: v * 256 + n) 0 (lib.take 4 nums);
  network = builtins.bitAnd address (4294967296 - pow2 (32 - prefix));
  octet = n: let x = builtins.div network (pow2 n); in toString (x - builtins.div x 256 * 256);

in
{
  config.assertions = [{
    assertion = valid;
    message = "my.proxy.fakeIpRange must be an IPv4 CIDR with prefix length 8..30.";
  }];

  options.my.proxy.routeTable = lib.mkOption {
    type = lib.types.ints.positive;
    default = 2022;
    description = "Mihomo TUN iproute2-table-index, shared by the template and checks.";
  };

  options.my.proxy.nodeProbeUrls = lib.mkOption {
    type = lib.types.nonEmptyListOf lib.types.str;
    default = [ "http://www.gstatic.com/generate_204" "http://www.msftconnecttest.com/connecttest.txt" ];
    description = "Delay-test targets for the effective selected node; a second target distinguishes target outages from node failure.";
  };
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

  # Network form of the same pool. The Mode-C guard lets root reach it (the TUN
  # is the only route there) so proxy-mode's own TUN self-test can run while
  # blocked; root can already use the mixed port, so this adds no capability.
  options.my.proxy.fakeIpNet = lib.mkOption {
    type = lib.types.str;
    readOnly = true;
    default = if valid then "${octet 24}.${octet 16}.${octet 8}.${octet 0}/${toString prefix}" else "invalid";
    description = "Network form of the fake-ip pool; the Mode-C guard exempts root to it for the TUN self-test.";
  };

  # The name proxy-mode's TUN self-test resolves. It must receive a fake-ip
  # answer, so it must not be caught by the Merge template's fake-ip-filter; the
  # home side asserts that at build time (a filtered name would break the TUN
  # layer of the health gate and lock the host in Mode C).
  options.my.proxy.tunProbeName = lib.mkOption {
    type = lib.types.str;
    default = "www.baidu.com";
    description = "Name used for proxy-mode's TUN self-test; must not match a fake-ip-filter entry.";
  };
}

