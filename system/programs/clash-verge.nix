{ pkgs, ... }:

{
  # Service Mode: core runs as root, which `proxyKillSwitch` in network.nix
  # relies on. After rebuild, turn on "Service Mode" in the Clash Verge GUI.
  programs.clash-verge = {
    enable = true;
    package = pkgs.unstable.clash-verge-rev.override {
      # Backport SagerNet/sing-tun 7954dd6 "Fix nftables interval end when
      # range hits max address" (missing in the metacubex fork): a route-exclude
      # range ending at the max address (255.255.255.255/32, ff00::/8) added the
      # same key twice, which the kernel rejects with EEXIST ("auto redirect:
      # file exists"), so TUN never comes up.
      mihomo = pkgs.unstable.mihomo.overrideAttrs (old: {
        postConfigure = (old.postConfigure or "") + ''
          chmod -R u+w vendor/github.com/metacubex/sing-tun
          patch -p1 -d vendor/github.com/metacubex/sing-tun \
            < ${pkgs.fetchurl {
              name = "sing-tun-nftables-interval-end.patch";
              url = "https://github.com/SagerNet/sing-tun/commit/7954dd6e20105bd48659dbb8bfc145c0a2e86a6d.patch";
              hash = "sha256-cLXIVVBHXzda1cQ+49/iQeSLfaXt9Ba7t1xhNAYlU/M=";
            }}
        '';
      });
    };
    serviceMode = true;
    group = "users";
  };
}

