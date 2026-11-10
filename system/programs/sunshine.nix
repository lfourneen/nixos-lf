{ pkgs, ... }:

{
  # Stream the current graphical desktop to a paired Moonlight client.
  services.sunshine = {
    enable = true;
    autoStart = true;

    # Retain global CUDA support. The locked variant is available from the
    # configured cache.nixos-cuda.org; verify substitutes with --max-jobs 0
    # after upgrades before attempting a resource-intensive local build.
    package = pkgs.sunshine;

    # iptables-based option, useless here (networking.firewall is off) — nftables rules handle ports
    openFirewall = false;

    # Required for DRM/KMS screen capture on Wayland.
    capSysAdmin = true;

    settings = {
      # Pair through the local web UI; only streaming ports reach the tailnet.
      origin_web_ui_allowed = "pc";
      upnp = "disabled";
    };

    applications.apps = [{ name = "Desktop"; image-path = "desktop.png"; }];
  };

  # Moonlight client for the desktop session
  environment.systemPackages = with pkgs; [
    moonlight-qt
  ];
}

