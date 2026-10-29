{ pkgs, config }:

let
  inherit (pkgs) lib;
  render = { name, overrides ? { }, ipv6 ? config.networking.enableIPv6 }:
    let
      proxy = config.my.proxy // overrides;
      osConfig = config // {
        my = config.my // { inherit proxy; };
        networking = config.networking // { enableIPv6 = ipv6; };
      };
      profile = import ../../home/config/cvr-merge.nix { inherit lib osConfig; };
      directory = ".local/share/io.github.clash-verge-rev.clash-verge-rev/profiles";
    in assert lib.all (a: a.assertion) profile.assertions; {
      inherit name;
      merge = pkgs.writeText "${name}-Merge.yaml" profile.home.file."${directory}/Merge.yaml".text;
      script = pkgs.writeText "${name}-Script.js" profile.home.file."${directory}/Script.js".text;
      expected = {
        inherit ipv6;
        tun = proxy.tunMode;
        stack = proxy.tunStack;
        mtu = proxy.tunMtu;
        address = proxy.tunIpv6Address;
        congestion = proxy.tunCongestionControl;
        mixed = config.my.machine.ports.mihomoMixed;
        tproxy = if proxy.transparentGuestInterfaces == [ ] then 0 else config.my.machine.ports.mihomoTproxy;
        urls = proxy.nodeProbeUrls;
        routing = proxy.tunRouting;
        delayUrl = proxy.delayTestUrl;
        targets = osConfig.my.proxy.managedProfile.lf-private-targets;
      };
    };
  manifest = pkgs.writeText "proxy-profile-tests.json" (builtins.toJSON (map render [
    { name = "default"; }
    { name = "mips"; overrides = { tunStack = "mips"; tunCongestionControl = "bbr"; }; }
    { name = "ipv4"; ipv6 = false; }
    { name = "http-only"; overrides.tunMode = false; }
    { name = "guest-tproxy"; overrides.transparentGuestInterfaces = [ config.my.machine.vmBridge ]; }
  ]));

in
pkgs.runCommand "proxy-profile-tests" {
  nativeBuildInputs = [ (pkgs.python3.withPackages (p: [ p.pyyaml ])) pkgs.nodejs ];
} ''
  python3 ${./test_profile.py} ${manifest}
  node ${./test_profile.js} ${manifest}
  touch "$out"
''

