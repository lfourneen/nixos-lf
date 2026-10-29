{ lib, ... }:

{
  imports = [
    (lib.mkRemovedOptionModule [ "my" "hardening" "externalDns" ] ''
      The wildcard DNS stub did not provide a complete external-client path.
      Remove this setting. VM/Waydroid/hotspot gateway DNS remains separate;
      LAN/tailnet DNS requires an explicit listener, client ACL and answer route.
    '')
  ];
  # Mode-A network posture (Clash closed). These options decide how much of the
  # machine is reachable, not how private it is: a direct connection exposes the
  # real address either way, and nothing here needs the proxy to be running.
  options.my.hardening = {
    allowPrivateEgress = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Keep direct LAN/CGNAT/ULA access in B. Disable to subject host private-address flows to the proxy policy too (including tailnet).";
    };
    guestHostServices = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Explicitly enable the selected host-service ports for libvirt guests; DHCP/DNS remain separate.";
    };
    guestTcpPorts = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Explicit desktop TCP services available to libvirt guests.";
    };
    guestUdpPorts = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Explicit desktop UDP services available to libvirt guests.";
    };

    tailscale.magicDns = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Route only the authenticated tailnet's MagicDNS suffix through its resolver; public DNS stays managed.";
    };
    guestPrivateEgress = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Keep guest LAN/CGNAT/ULA forwarding. Disable for untrusted guests and allow only guestPrivateExceptions.";
    };
    guestPrivateExceptions = lib.mkOption {
      default = [ ];
      type = lib.types.listOf (lib.types.submodule {
        options = {
          interface = lib.mkOption { type = lib.types.str; };
          cidr = lib.mkOption { type = lib.types.str; };
          tcpPorts = lib.mkOption { type = lib.types.listOf lib.types.port; default = [ ]; };
          udpPorts = lib.mkOption { type = lib.types.listOf lib.types.port; default = [ ]; };
        };
      });
      description = "Interface/CIDR/transport/port exceptions for restricted guest private forwarding; apply to established traffic too.";
    };

    hotspot.enable = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Share the wired uplink over my.machine.hotspot.interface (ap0): AP DHCP/DNS/
        host-service accepts and the guest forward + masquerade path.
        A shares the uplink directly; B refuses guest public egress unless the
        AP is explicitly in my.proxy.transparentGuestInterfaces (IPv4 TPROXY).
        C refuses public egress. This switch does not enable hostapd or DHCP.

        The AP interface must be distinct from both physical uplinks, so a Wi-Fi
        client is never treated as guest ingress. Create/configure that AP
        interface explicitly; this switch installs its firewall role only.
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
      type = lib.types.either
        (lib.types.enum [ "preserve" "permanent" "random" "stable" "stable-ssid" ])
        (lib.types.strMatching "([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}");
      default = "stable-ssid";
      example = "permanent";
      description = ''
        Default MAC policy for NetworkManager Wi-Fi profiles without an explicit
        cloned address. `stable-ssid` derives an address from the SSID and the
        machine's persistent identity; reconnecting to the same SSID keeps it.
        `stable` uses connection.stable-id instead. `random` changes on each
        connection, `preserve` leaves the current address untouched, and
        `permanent` restores the hardware address. A literal MAC is also allowed.
        Individual profiles can override this default for MAC-based access rules.
      '';
    };
  };
}

