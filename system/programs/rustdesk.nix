{ pkgs, ... }:

{
  # RustDesk self-hosted server
  services.rustdesk-server = {
    enable = true;
    openFirewall = false;
    signal = {
      enable = true;
      # hbbs needs a non-empty --relay-servers arg; `''` collapses to an empty
      # value so each client relays via the host it reached (LAN or tailnet).
      relayHosts = [ "''" ];
    };
    relay.enable = true;
  };
  
  environment.systemPackages = with pkgs; [
    rustdesk
  ];
}

