{ pkgs, ... }:

{
  # Service Mode: core runs as root, which `proxyKillSwitch` in network.nix
  # relies on. After rebuild, turn on "Service Mode" in the Clash Verge GUI.
  programs.clash-verge = {
    enable = true;
    package = pkgs.clash-verge-rev;
    serviceMode = true;
    group = "users";
  };
}

