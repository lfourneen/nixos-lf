{ config, lib, ... }:

let
  m = config.my.machine;
  p = config.my.proxy;
  routeExclude = lib.optionals config.my.hardening.allowPrivateEgress
    (m.privateV4 ++ m.ulaV6 ++ m.linkLocalV6)
    ++ m.multicastV4 ++ [ "${m.limitedBroadcastV4}/32" ] ++ m.multicastV6;
  emptyTunFields = [ "route-address" "route-address-set" "route-exclude-address-set"
    "include-interface" "exclude-interface" "include-uid" "include-uid-range"
    "exclude-uid" "exclude-uid-range" "include-mac-address" "exclude-mac-address"
    "exclude-src-port" "exclude-src-port-range" "exclude-dst-port" "exclude-dst-port-range"
    "inet4-route-address" "inet6-route-address" "inet4-route-exclude-address"
    "inet6-route-exclude-address" "loopback-address" ];
  pow2 = n: lib.foldl' (v: _: v * 2) 1 (lib.range 1 n);
  cidr4 = value: let
    pair = lib.splitString "/" value;
    bits = lib.toInt (builtins.elemAt pair 1);
    ip = lib.foldl' (v: n: v * 256 + lib.toInt n) 0 (lib.splitString "." (builtins.head pair));
    size = pow2 (32 - bits);
    first = builtins.bitAnd ip (4294967296 - size);
  in { inherit first; last = first + size - 1; };
  fake = if p.fakeIpNet == "invalid" then { first = 0; last = -1; } else cidr4 p.fakeIpRange;
  reserved4 = [ "0.0.0.0/8" "127.0.0.0/8" "169.254.0.0/16" "224.0.0.0/4" "240.0.0.0/4" ]
    ++ m.privateV4 ++ [ m.vmSubnet m.waydroidSubnet ];
in
{
  options.my.proxy.managedDns = lib.mkOption {
    type = lib.types.attrs;
    internal = true;
    readOnly = true;
    default = {
      enable = true;
      listen = "127.0.0.1:${toString m.ports.mihomoDns}";
      ipv6 = false;
      enhanced-mode = if p.tunMode then "fake-ip" else "redir-host";
      fake-ip-range = p.fakeIpRange;
      fake-ip-filter = [
        "*.lan" "*.local" "*.localhost" "*.test" "*.home.arpa" "*.internal"
        "localhost.ptlogin2.qq.com" "+.stun.*.*" "+.stun.*.*.*" "+.stun.*.*.*.*"
        "lens.l.google.com" "+.srv.nintendo.net" "+.stun.playstation.net" "+.xboxlive.com"
        "+.msftncsi.com" "+.msftconnecttest.com" "capture.apple.com"
        "connectivitycheck.gstatic.com" "connectivitycheck.android.com"
      ];
      use-hosts = true;
      respect-rules = true;
      default-nameserver = [ "tls://${lib.head m.dotUpstreams}" "tls://119.29.29.29" ];
      proxy-server-nameserver = [ "https://${lib.head m.dotUpstreams}/dns-query" ];
      nameserver = [ "https://1.1.1.1/dns-query" "https://8.8.8.8/dns-query" ];
      nameserver-policy = {
        "geosite:cn" = map (ip: "https://${ip}/dns-query") m.dotUpstreams;
        "geosite:geolocation-!cn" = [ "https://1.1.1.1/dns-query" "https://8.8.8.8/dns-query" ];
      } // lib.genAttrs [ "+.google.com" "+.googleapis.com" "+.googleapis.cn" "+.googlevideo.com"
        "+.gstatic.com" "+.youtube.com" "+.youtu.be" "+.facebook.com" "+.twitter.com"
        "+.x.com" "+.github.com" "+.githubusercontent.com" "+.openai.com" "+.chatgpt.com"
        "+.anthropic.com" ] (_: [ "https://1.1.1.1/dns-query" ]);
    };
  };
  options.my.proxy.managedProfile = lib.mkOption {
    type = lib.types.attrs;
    internal = true;
    readOnly = true;
    default = {
      listeners = [ ];
      tunnels = [ ];
      external-controller = "";
      external-controller-tls = "";
      external-controller-pipe = "";
      external-doh-server = "";
      ss-config = "";
      vmess-config = "";
      "tuic-server.enable" = false;
      "iptables.enable" = false;
      mixed-port = m.ports.mihomoMixed;
      port = 0;
      socks-port = 0;
      redir-port = 0;
      tproxy-port = if p.transparentGuestInterfaces == [ ] then 0 else m.ports.mihomoTproxy;
      allow-lan = false;
      ipv6 = config.networking.enableIPv6;
      routing-mark = m.mihomoMark;
      "tun.enable" = p.tunMode;
      "dns.fallback" = [ ];
    } // lib.mapAttrs' (key: value: lib.nameValuePair "dns.${key}" value) p.managedDns
      // lib.genAttrs (map (key: "tun.${key}") emptyTunFields) (_: [ ])
      // lib.optionalAttrs p.tunMode {
        "tun.device" = p.tunDev;
        "tun.stack" = p.tunStack;
        "tun.mtu" = p.tunMtu;
        "tun.auto-route" = true;
        "tun.auto-redirect" = true;
        "tun.strict-route" = true;
        "tun.iproute2-table-index" = p.routeTable;
        "tun.route-exclude-address" = routeExclude;
        "tun.inet6-address" = lib.optionals config.networking.enableIPv6 [ p.tunIpv6Address ];
        "tun.dns-hijack" = [ "any:53" "tcp://any:53" ];
      };
  };
  config.assertions = [
    {
      assertion = lib.all (value: let other = cidr4 value;
        in fake.last < other.first || other.last < fake.first) reserved4;
      message = "Fake-IP must not overlap real LAN, guest, tailnet, loopback or reserved IPv4 ranges.";
    }
    {
      assertion = builtins.bitAnd m.mihomoMark 16711680 == 0
        && !(builtins.elem m.mihomoMark [ 563 8227 8228 ]);
      message = "The core mark must not collide with TPROXY, sing-tun or Tailscale reserved marks.";
    }
    {
      assertion = builtins.match "[a-zA-Z0-9][a-zA-Z0-9.-]*[a-zA-Z0-9]" p.tunProbeName != null;
      message = "The TUN probe must be a valid hostname.";
    }
    {
      assertion = m.vmBridge != m.waydroidBridge && m.wired.name != m.wireless.name;
      message = "Guest bridges and physical uplinks must have distinct roles.";
    }
  ];
}
