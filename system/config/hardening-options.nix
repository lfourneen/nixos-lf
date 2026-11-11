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
      default = false;
      description = "Legacy compatibility: trust libvirt/Waydroid private forwarding when guestNetworks.<interface>.trust is unset. Hotspots and additional interfaces stay restricted. Prefer explicit per-interface trust.";
    };

    guestNetworks = lib.mkOption {
      default = { };
      type = lib.types.attrsOf (lib.types.submodule {
        options = {
          trust = lib.mkOption {
            type = lib.types.nullOr (lib.types.enum [ "restricted" "trusted" ]);
            default = null;
            description = "Unset means restricted, except the explicit legacy libvirt/Waydroid compatibility switch. Trust permits private routed destinations, never public B/C forwarding or arbitrary host services.";
          };

          sourceCidrs = lib.mkOption {
            type = lib.types.listOf lib.types.str;
            default = [ ];
            description = "Canonical, disjoint guest source subnets. Built-in bridges receive their machine IPv4 subnet; IPv6 requires an explicit ULA subnet. These identify a network, not individual peers.";
          };

          gateways = lib.mkOption {
            type = lib.types.listOf lib.types.str;
            default = [ ];
            description = "Host gateway addresses inside the declared source subnets; DNS and explicit host ports are bound to these destinations. This does not create an interface or server.";
          };

          hostTcpPorts = lib.mkOption { type = lib.types.listOf lib.types.port; default = [ ]; };
          hostUdpPorts = lib.mkOption { type = lib.types.listOf lib.types.port; default = [ ]; };
        };
      });
      description = "Routed guest trust by exact ingress interface, including an enabled hotspot. Host ports are separate from private forwarding. Use a separate bridge for each trust class; this does not isolate peers sharing a layer-2 segment.";
    };

    guestPrivateExceptions = lib.mkOption {
      default = [ ];
      type = lib.types.listOf (lib.types.submodule {
        options = {
          interface = lib.mkOption { type = lib.types.strMatching "[a-zA-Z0-9_.-]{1,15}"; };
          cidr = lib.mkOption { type = lib.types.strMatching "[a-fA-F0-9.:]+/[0-9]+"; };
          tcpPorts = lib.mkOption { type = lib.types.listOf lib.types.port; default = [ ]; };
          udpPorts = lib.mkOption { type = lib.types.listOf lib.types.port; default = [ ]; };
        };
      });
      description = "Canonical private destination CIDR and TCP/UDP port grants for a restricted guest interface. Current grants govern both directions of established routed flows; host services remain separate.";
    };

    socketGuestUsers = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Dedicated accounts with fixed UIDs 1000..65533 for restricted rootless/passt host-socket backends, including all delegated subuids. Desktop, root, infrastructure and shared/overlapping identities are refused. Public traffic follows host A/B/C; internal/loopback, fake-IP and known translation targets are denied before NAT, except explicit private grants and forced real-address DNS. Shared desktop-UID backends remain in the host trust class.";
    };

    socketGuestPrivateExceptions = lib.mkOption {
      default = [ ];
      type = lib.types.listOf (lib.types.submodule {
        options = {
          user = lib.mkOption { type = lib.types.str; };
          interface = lib.mkOption { type = lib.types.strMatching "[a-zA-Z0-9_.:-]{1,15}"; };
          cidr = lib.mkOption { type = lib.types.strMatching "[a-fA-F0-9.:]+/[0-9]+"; };
          tcpPorts = lib.mkOption { type = lib.types.listOf lib.types.port; default = [ ]; };
          udpPorts = lib.mkOption { type = lib.types.listOf lib.types.port; default = [ ]; };
        };
      });
      description = "Private host-socket destination grants for socketGuestUsers, by account, original output interface, canonical CIDR and destination ports. Proxy/resolver listener ports and unsafe targets cannot be granted. Revocation also cuts established sockets; C never inherits these grants.";
    };

    guestConfigCheck = lib.mkOption {
      type = lib.types.package;
      internal = true;
      readOnly = true;
      description = "Guest network/source/CIDR/port/identity validation required by normal system builds.";
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

    wifi.identities = lib.mkOption {
      default = { };
      type = lib.types.attrsOf (lib.types.submodule {
        options = {
          mode = lib.mkOption {
            type = lib.types.enum [ "stable" "random" "permanent" "fixed" ];
            default = "stable";
          };
          address = lib.mkOption {
            type = lib.types.nullOr (lib.types.strMatching "([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}");
            default = null;
          };
          epoch = lib.mkOption {
            type = lib.types.strMatching "[a-zA-Z0-9_.-]{1,64}";
            default = "1";
            description = "Per-profile identity revision. Changing it rotates derived stable MAC/DHCP/IPv6 identities without deleting the host NetworkManager key or machine-id.";
          };
        };
      });
      description = "Wi-Fi identity migration targets keyed by canonical profile UUID. network-identity plan/apply/restore manages only identity properties; apply requires a disconnected profile with autoconnect disabled. No automatic reconnect or profile rewriting on rebuild.";
    };

    wired = {
      clonedMacAddress = lib.mkOption {
        type = lib.types.either (lib.types.enum [ "permanent" "random" ])
          (lib.types.strMatching "([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}");
        default = "permanent";
        description = "Primary dhcpcd uplink MAC policy. Permanent preserves hardware at boot; random uses dhcpcd randomise_hwaddr on link activation/carrier loss; a literal is fixed by the .link file. Non-permanent policies also enable DHCP anonymity and current-MAC SLAAC. Apply wired policy changes with a prepared reboot, not a live MAC edit.";
      };
      persistDhcpIdentity = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Persist /var/lib/dhcpcd across root rollback. Before first enabling, stop dhcpcd and copy its current state to /persist/var/lib/dhcpcd; an empty first bind would otherwise hide the existing DUID/leases. Anonymous mode ignores the retained DUID.";
      };
    };

    linkIdentitySettings = lib.mkOption { type = lib.types.path; internal = true; readOnly = true; };
    linkIdentityCheck = lib.mkOption { type = lib.types.package; internal = true; readOnly = true; };
  };

  config.my.hardening.guestNetworks = {
    ${config.my.machine.vmBridge} = {
      sourceCidrs = lib.mkDefault [ config.my.machine.vmSubnet ];
      gateways = lib.mkDefault [ config.my.machine.vmAddress ];
    };

    ${config.my.machine.waydroidBridge} = {
      sourceCidrs = lib.mkDefault [ config.my.machine.waydroidSubnet ];
      gateways = lib.mkDefault [ config.my.machine.waydroidAddress ];
    };

  } // lib.optionalAttrs config.my.hardening.hotspot.enable {
    ${config.my.machine.hotspot.interface} = {
      sourceCidrs = lib.mkDefault [ config.my.machine.hotspot.subnet ];
      gateways = lib.mkDefault [ config.my.machine.hotspot.address ];
    };
  };
}

