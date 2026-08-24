{ pkgs, ... }:

{
  # Moonlight
  services.sunshine = {
    enable = true;
    autoStart = true;
    # iptables-based option, useless here (networking.firewall is off) — nftables rules handle ports
    openFirewall = false;
    # Required for DRM/KMS screen capture on AMD (Wayland)
    capSysAdmin = true;
  };

  # Moonlight client for the desktop session
  environment.systemPackages = with pkgs; [
    moonlight-qt
  ];
}

