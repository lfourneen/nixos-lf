{ pkgs, ... }:

{
  # RustDesk self-hosted server
  services.rustdesk-server = {
    enable = true;
    openFirewall = false;
    signal.enable = true;
    relay.enable = true;
  };
  
  environment.systemPackages = with pkgs; [
    rustdesk
  ];
}

