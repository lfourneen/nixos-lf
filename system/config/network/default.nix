{
  imports = [
    ./host.nix
    ./network.nix
    ./options.nix
    ./proxy/options.nix
    ./proxy/profile.nix
    ./proxy/coordinator.nix
    ./dns/blocklist.nix
    ./dns/upstream.nix
    ./firewall/alert.nix
    ./firewall/bootstrap.nix
    ./firewall/recover.nix
    ./firewall/verify.nix
    ./diagnostics
    ./maintenance
  ];
}
