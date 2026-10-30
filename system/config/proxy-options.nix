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
  } {
    assertion = config.my.proxy.routeTable <= 4294967295
      && !(builtins.elem config.my.proxy.routeTable [ 52 100 253 254 255 ]);
    message = "Mihomo route table must not collide with Tailscale, TPROXY or built-in routing tables.";
  } {
    assertion = lib.all (url: builtins.match "https?://[^[:space:]]+" url != null) config.my.proxy.nodeProbeUrls;
    message = "Proxy health targets must be non-empty HTTP(S) URLs without whitespace.";
  } {
    # dnsmasq turns each entry into `address=/domain/`; a scheme, path or
    # wildcard would only produce a rule that silently matches nothing.
    assertion = lib.all (d: builtins.match "([A-Za-z0-9_-]+)(\\.[A-Za-z0-9_-]+)*" d != null) config.my.proxy.blockedDomains;
    message = "my.proxy.blockedDomains entries must be bare domain names (no scheme, path or wildcard).";
  } {
    # Same shape as blockedDomains: these become `server=/domain/#` exceptions,
    # so a scheme, path or wildcard would produce a rule that matches nothing.
    assertion = lib.all (d: builtins.match "([A-Za-z0-9_-]+)(\\.[A-Za-z0-9_-]+)*" d != null) config.my.proxy.blockedDomainExceptions;
    message = "my.proxy.blockedDomainExceptions entries must be bare domain names (no scheme, path or wildcard).";
  }];

  options.my.proxy.blockedDomains = lib.mkOption {
    type = lib.types.listOf lib.types.str;
    default = [ ];
    example = [ "ads.example.com" "tracker.example.net" ];
    description = ''
      Domains answered NXDOMAIN by dnsmasq before either mode's upstream is
      asked. Blocking at the shared front door covers Mode A, Mode B and guest
      lookups alike, and dnsmasq matches suffixes, so subdomains are included.
      Default: empty, i.e. no filtering; the host list is in
      system/config/dns-blocklist.nix.
    '';
  };

  options.my.proxy.blockedDomainExceptions = lib.mkOption {
    type = lib.types.listOf lib.types.str;
    default = [ ];
    example = [ "not-ads.example.com" ];
    description = ''
      Names inside a blocked domain that must keep resolving. Each entry is a
      dnsmasq `server=/domain/#` exception: the more specific domain wins over
      the `address=` blocklist, and `#` means the standard servers, so an
      excepted name goes to the same per-mode upstream as any other name.
      Default: empty, i.e. nothing is excepted from the blocklist.
    '';
  };

  options.my.proxy.routeTable = lib.mkOption {
    type = lib.types.ints.positive;
    default = 2022;
    description = "Mihomo TUN iproute2-table-index, shared by the template and checks.";
  };

  options.my.proxy.privateTargetDomains = lib.mkOption {
    type = lib.types.listOf lib.types.str;
    default = [
      "localhost" "lan" "local" "home.arpa" "internal"
      "wlan" "intranet" "private" "domain" "home" "host" "corp"
    ];
    description = "Explicit internal domain/suffix exceptions to the managed core's domain-to-private target guard.";
  };

  options.my.proxy.gostPackage = lib.mkOption {
    type = lib.types.package;
    internal = true;
    readOnly = true;
  };

  options.my.proxy.tunRouting = lib.mkOption {
    type = lib.types.attrsOf lib.types.ints.positive;
    internal = true;
    readOnly = true;
    default = {
      iproute2-rule-index = 9000;
      auto-redirect-input-mark = 8227;
      auto-redirect-output-mark = 8228;
      auto-redirect-iproute2-fallback-rule-index = 32768;
    };
    description = "Managed sing-tun routing priorities and marks, shared by every configuration gate.";
  };

  options.my.proxy.tunStack = lib.mkOption {
    type = lib.types.enum [ "system" "gvisor" "mixed" "mips" ];
    default = "mixed";
    description = "Explicit TUN stack; mixed remains the measured baseline, mips is opt-in.";
  };
  options.my.proxy.tunMtu = lib.mkOption {
    type = lib.types.ints.between 1280 9000;
    default = 1500;
    description = "TUN MTU. Change only after PMTU measurements.";
  };
  options.my.proxy.tunCongestionControl = lib.mkOption {
    type = lib.types.enum [ "cubic" "reno" "bbr" "bbr3" ];
    default = "cubic";
    description = "Mihomo mips-stack TCP congestion controller; separate from host TCP.";
  };
  options.my.proxy.tunIpv6Address = lib.mkOption {
    type = lib.types.str;
    default = "fdfe:dcba:9876::1/126";
    description = "Stable IPv6 TUN address, including on IPv4-only uplinks.";
  };
  options.my.proxy.healthBudgetSeconds = lib.mkOption {
    type = lib.types.ints.between 8 30;
    default = 12;
    description = "Total deadline for a selected-node health probe, across all targets.";
  };
  options.my.proxy.healthRequestTimeoutSeconds = lib.mkOption {
    type = lib.types.ints.between 1 10;
    default = 5;
    description = "Maximum seconds for each real HTTP health request, capped by the shared probe deadline.";
  };
  options.my.proxy.guestInterfaces = lib.mkOption {
    type = lib.types.nonEmptyListOf lib.types.str;
    default = [ config.my.machine.vmBridge config.my.machine.waydroidBridge ]
      ++ lib.optionals config.my.hardening.hotspot.enable [ config.my.machine.hotspot.interface ];
    description = "All routed guest ingress interfaces; policy applies before NAT and conntrack accepts.";
  };
  options.my.proxy.transparentGuestInterfaces = lib.mkOption {
    type = lib.types.listOf lib.types.str;
    default = [ ];
    description = "Opt-in IPv4 guest TPROXY interfaces, permitted only in healthy proxy mode.";
  };
  options.my.proxy.splitDnsProfileUuids = lib.mkOption {
    type = lib.types.listOf lib.types.str;
    default = [ ];
    description = "NetworkManager Wi-Fi profile UUIDs allowed explicit split DNS to private/ULA upstreams excluded by allowPrivateEgress. Leases bind the active connection and interface; public DNS upstreams are unsupported.";
  };
  options.my.proxy.plaintextBootBudgetSeconds = lib.mkOption {
    type = lib.types.nullOr lib.types.ints.positive;
    default = null;
    description = "Optional boot-wide plaintext deadline, independent of DHCP and reconnects; null retains per-session budgets.";
  };

  options.my.proxy.bootMode = lib.mkOption {
    type = lib.types.enum [ "direct" "proxy" ];
    default = "direct";
    description = "Optional fail-closed boot intent, installed before physical networking.";
  };
  options.my.proxy.stateMaxAgeSeconds = lib.mkOption {
    type = lib.types.ints.between 90 300;
    default = 120;
    description = "Maximum age of a successful supervisor/consumer observation.";
  };
  options.my.proxy.verificationMaxAgeSeconds = lib.mkOption {
    type = lib.types.ints.between 390 1800;
    default = 420;
    description = "Verifier freshness: allows the 300s timer, 30s accuracy and bounded 60s execution.";
  };
  options.my.proxy.resolvedPolicySettings = lib.mkOption {
    type = lib.types.path;
    internal = true;
  };

  options.my.proxy.nodeProbeUrls = lib.mkOption {
    type = lib.types.nonEmptyListOf lib.types.str;
    default = [ "https://www.gstatic.com/generate_204" "https://cp.cloudflare.com/generate_204" ];
    description = "HTTP targets for the effective selected route; a second target distinguishes target outages from route failure.";
  };

  options.my.proxy.delayTestUrl = lib.mkOption {
    type = lib.types.strMatching "https://[^[:space:]]+";
    default = "https://www.gstatic.com/generate_204";
    description = "HTTPS default for unset GUI delay tests and missing or HTTP node/provider health-check URLs.";
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

