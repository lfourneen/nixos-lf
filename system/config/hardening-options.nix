{ lib, ... }:

{
  # Mode-A network posture (Clash closed). These options decide how much of the
  # machine is reachable, not how private it is: a direct connection exposes the
  # real address either way, and nothing here needs the proxy to be running.
  options.my.hardening = {
    hotspot.enable = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Share the wired uplink over the wireless interface: the AP DHCP/DNS/
        host-service accepts and the guest forward + masquerade path.

        Off by default because those accepts match on the source subnet, which a
        peer on the same L2 can spoof while the interface is a client of a foreign
        network; the host-service accepts additionally require the AP address as
        destination. No hotspot configuration exists in this repo, so the default
        removes no working feature.
      '';
    };

    tailnetTcpPorts = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "22" "47984" "47989" "47990" "48010" ];
      description = ''
        TCP ports the tailnet may reach on this host. A blanket
        `iifname "tailscale0" accept` exposes every 0.0.0.0-bound listener (the :53
        resolver, :5353 avahi, :27036 steam, the dev servers) to every device in
        the tailnet, and makes narrower rules unreachable.

        22 covers sshd and Tailscale-SSH; add your own ports deliberately.
      '';
    };

    tailnetUdpPorts = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "47998-48010" ];
      description = "UDP ports the tailnet may reach on this host.";
    };

    wifi.clonedMacAddress = lib.mkOption {
      type = lib.types.str;
      default = "preserve";
      example = "stable";
      description = ''
        MAC the wireless interface presents. `preserve` (the default) leaves
        NetworkManager's own behaviour alone, and the key is only emitted when this
        differs from the default. `stable` is deterministic per network, so a
        portal or a DHCP reservation keeps seeing the same address -- closer to
        friendly than `random`, but it is still a change of identity.
      '';
    };
  };
}

