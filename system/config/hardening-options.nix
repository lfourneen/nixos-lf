{ config, lib, ... }:

{
  imports = [
    (lib.mkRemovedOptionModule [ "my" "hardening" "externalDns" ] ''
      The wildcard DNS stub did not provide a complete external-client path.
      Remove this setting. VM/Waydroid/hotspot gateway DNS remains separate;
      LAN/tailnet DNS requires an explicit listener, client ACL and answer route.
    '')
  ];
  # Input trust and direct-egress policy are separate. Restricted host egress
  # applies in B; C never inherits its exceptions, including tailnet replies.
  options.my.hardening = {
    allowPrivateEgress = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Compatibility mode: keep direct LAN/CGNAT/ULA access in B; false subjects it to proxy routing. Use directEgress.mode=restricted for explicit host direct-access authorization.";
    };
    directEgress = {
      mode = lib.mkOption {
        type = lib.types.enum [ "compatibility" "restricted" ];
        default = "compatibility";
        description = "B host private/multicast policy. Restricted drops unapproved native socket traffic before TUN/NAT; A and C keep their own policies.";
      };
      privateRoutes = lib.mkOption {
        type = lib.types.bool;
        internal = true;
        readOnly = true;
        default = config.my.hardening.allowPrivateEgress
          || config.my.hardening.directEgress.mode == "restricted";
        description = "Keep private destinations outside TUN/NAT when compatibility or the early restricted guard owns their authorization.";
      };
      configCheck = lib.mkOption {
        type = lib.types.package;
        internal = true;
        readOnly = true;
        description = "Canonical CIDR/scope validation required by normal system builds.";
      };
      exceptions = lib.mkOption {
        default = [ ];
        type = lib.types.listOf (lib.types.submodule {
          options = {
            interface = lib.mkOption {
              type = lib.types.strMatching "[a-zA-Z0-9_.:-]{1,15}";
              description = "Exact output interface; no wildcard.";
            };
            cidr = lib.mkOption {
              type = lib.types.strMatching "[a-fA-F0-9.:]+/[0-9]+";
              description = "Destination CIDR; grants apply only to private, link-local, multicast and limited-broadcast traffic.";
            };
            uids = lib.mkOption {
              type = lib.types.listOf (lib.types.ints.between 0 4294967294);
              description = "Explicit socket owner UIDs; an explicitly empty list permits every UID for this entry.";
            };
            tcpPorts = lib.mkOption { type = lib.types.listOf lib.types.port; default = [ ]; };
            udpPorts = lib.mkOption { type = lib.types.listOf lib.types.port; default = [ ]; };
          };
        });
        description = "Restricted B host direct grants by interface, socket UID, CIDR and destination TCP/UDP ports; revocation also cuts established host-initiated flows.";
      };
      unboundDotInProxy = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Permit Unbound's UID/uplink/fixed-IP TCP DoT exception in B. False drops its DoT before redirection; A retains recovery DoT and C blocks it.";
      };
      timeServerCidrs = lib.mkOption {
        type = lib.types.listOf (lib.types.strMatching "[a-fA-F0-9.:]+/[0-9]+");
        default = [ ];
        description = "Optional strict Chrony NTP/NTS-KE destination CIDRs in A/B. Empty keeps UID/uplink/port compatibility. Include both KE and negotiated NTP servers; address changes fail closed. C remains blocked.";
      };
      timeServerNames = lib.mkOption {
        type = lib.types.listOf (lib.types.strMatching "[a-zA-Z0-9.-]+");
        default = [ ];
        description = "Additional negotiated NTP hostnames to exclude from fake-IP when timeServerCidrs is selected. Configured NTS-KE names are included automatically; this does not grant additional CIDRs.";
      };
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
      default = [ "47984" "47989" "48010" ];
      description = ''
        TCP ports the tailnet may reach on this host. A blanket
        `iifname "tailscale0" accept` exposes every 0.0.0.0-bound listener (the :53
        resolver, :5353 avahi, :27036 steam, the dev servers) to every device in
        the tailnet, and makes narrower rules unreachable.

        Defaults expose Sunshine streaming only. Its management UI stays local;
        SSH is disabled. Add management or other service ports deliberately.
      '';
    };

    tailnetUdpPorts = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "47998" "47999" "48000" "48002" "48010" ];
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

