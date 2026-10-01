{ pkgs, config, lib, ... }:

let
  m = config.my.machine;
  port = m.ports;

  # The interface whose DHCP lease carries the fallback resolvers: it must be the
  # one NixOS runs a DHCP client on, or the lease path does not exist.
  dhcpIfaces = lib.attrNames (lib.filterAttrs (_: v: v.useDHCP == true) config.networking.interfaces);
  dhcpIface = if dhcpIfaces == [ ] then m.wired.name else lib.head dhcpIfaces;

in
{
  assertions = [
    {
      assertion = lib.length dhcpIfaces == 1;
      message = ''
        dns-pac.nix reads the fallback resolvers from the DHCP lease of the one
        interface that runs a DHCP client, but this configuration has
        ${toString (lib.length dhcpIfaces)} of them
        (${lib.concatStringsSep ", " dhcpIfaces}). Pin the interface or fix
        networking.interfaces.
      '';
    }
  ];

  # DNS PAC: mihomo DNS while clash runs, unbound DoT otherwise, with the DHCP
  # resolvers only inside the bounded windows below.
  systemd.services.dns-pac = {
    description = "DNS PAC: mihomo DNS when clash is up, DoT otherwise";
    after = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    unitConfig.StartLimitIntervalSec = 0;
    serviceConfig = {
      RuntimeDirectory = "dns-pac";
      # Keep /run/dns-pac/servers.conf across a restart: dnsmasq reads it at start
      # and the tmpfiles seed lives in this directory, so removing it on stop (the
      # default) would leave dnsmasq with no conf-file to read.
      RuntimeDirectoryPreserve = true;

      # Root only for D-Bus (systemctl restart dnsmasq + resolvectl flush-caches):
      # no caps and no writes outside /run/dns-pac. AF_NETLINK is for ss(8).
      NoNewPrivileges = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      ProtectClock = true;
      ProtectHostname = true;
      RestrictNamespaces = true;
      RestrictSUIDSGID = true;
      RestrictRealtime = true;
      RemoveIPC = true;
      CapabilityBoundingSet = "";
      RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" "AF_NETLINK" ];
      ReadWritePaths = [ "/run/dns-pac" ];

      ExecStart = "${pkgs.writeShellScript "dns-pac-loop" ''
        CLASH_ON=${config.my.proxy.isClashOn}

        # Event-driven, adaptive sleep (same mechanism as proxy-mode.nix):
        # proxy-net-wake sends SIGWINCH on a Clash core change, which interrupts
        # `wait`; SIGWINCH is ignore-by-default, so an early wake cannot kill the
        # process before the trap is installed. The interval adapts -- 2 s while
        # transitioning/degraded, up to 15 s once the path is settled and healthy.
        nap_pid=""
        interval=2
        MIN=2
        MAX=15              # cap the backstop: a missed event must not leave DNS
                            # pointing at a core that is gone for long
        trap ':' WINCH
        nap() {
          ${pkgs.coreutils}/bin/sleep "$1" &
          nap_pid=$!
          wait "$nap_pid" 2>/dev/null || true
          kill "$nap_pid" 2>/dev/null || true
          nap_pid=""
        }

        # dhcpcd >= 10 writes an opaque lease blob, so parsing the file never
        # matches: `dhcpcd -U` decodes it over /run/dhcpcd/unpriv.sock.
        lease_resolvers() {
          ${pkgs.dhcpcd}/bin/dhcpcd -U -4 "${dhcpIface}" 2>/dev/null \
            || ${pkgs.networkmanager}/bin/nmcli -t -g IP4.DNS device show "${dhcpIface}" 2>/dev/null
        }

        # The guard is what keeps proxy mode from appending a plaintext resolver.
        isp_servers() {
          $CLASH_ON && return 0

          found="$(lease_resolvers \
            | ${pkgs.gnused}/bin/sed -n 's/^domain_name_servers=//p; s/^new_domain_name_servers=//p' \
            | ${pkgs.coreutils}/bin/tr -s ' \t' '\n' \
            | ${pkgs.gnugrep}/bin/grep -vE '^(0\.0\.0\.0|127\.|169\.254\.|255\.255\.255\.255)' \
            | ${pkgs.gnugrep}/bin/grep -E '^((25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])$')"
          if [ -z "$found" ]; then
            echo "dns-pac: no DHCP resolver available (dhcpcd -U/nmcli); no plaintext fallback" >&2
            return 0
          fi

          printf 'server=%s\n' $found
        }

        # Identity of the link: a change means a new network, which opens the
        # bootstrap window.
        dhcp_identity() {
          lease_resolvers \
            | ${pkgs.gnugrep}/bin/grep -E '^(ip_address|routers|domain_name_servers|new_domain_name_servers)=' \
            | ${pkgs.coreutils}/bin/tr '\n' ' '
        }

        write_state() {
          case "$1" in
            proxy)
              new_conf='server=127.0.0.1#${toString port.mihomoDns}'
              ;;
            dot|degraded-dot-only)
              # Encrypted only; same upstream as `dot`, louder reason.
              new_conf='server=127.0.0.1#${toString port.unbound}'
              ;;
            *)
              # The bounded plaintext states, reachable only from mode_a_state().
              new_conf='server=127.0.0.1#${toString port.unbound}'
              isp="$(isp_servers)"
              if [ -n "$isp" ]; then
                new_conf="$(printf '%s\n%s' "$new_conf" "$isp")"
              fi
              ;;
          esac
          printf '%s\n' "$new_conf" > /run/dns-pac/servers.conf

          # State for proxy-status plus a reason: degradations name themselves here.
          printf '%s\n' "$1" > /run/dns-pac/status
          printf '%s\n' "$state_reason" > /run/dns-pac/reason

          # Only restart when the upstream really changed: dnsmasq's StartLimit
          # (5 per 10s) must not be hit by a flapping probe, and a redundant
          # write needs no restart. The cache flush is cheap, so keep it.
          if [ "$new_conf" != "''${last_conf:-}" ]; then
            last_conf="$new_conf"
            ${pkgs.systemd}/bin/systemctl restart --no-block dnsmasq.service || true
          fi
          ${pkgs.systemd}/bin/resolvectl flush-caches
        }

        # Identity, not reachability: a local impostor can bind ${toString port.mihomoDns} but cannot land
        # in clash-verge's cgroup, which the kernel reports.
        dns_listener_up() {
          ${pkgs.iproute2}/bin/ss -lnteH 'sport = :${toString port.mihomoDns}' 2>/dev/null \
            | ${pkgs.gnugrep}/bin/grep -q 'cgroup:/system.slice/clash-verge.service'
        }

        dns_ok() {
          # baidu is DIRECT-policy, gstatic goes through the node (respect-rules):
          # requiring both proves the core and the node, not just a live core.
          for name in www.baidu.com www.gstatic.com; do
            ${pkgs.dnsutils}/bin/dig +time=2 +tries=1 +short @127.0.0.1 -p ${toString port.mihomoDns} \
              "$name" 2>/dev/null | ${pkgs.gnugrep}/bin/grep -q . || return 1
          done
          return 0
        }

        # Health of the encrypted path itself: dnsmasq's own failover is a hang
        # (one failed upstream means the client waits ~8 s for no SERVFAIL).
        dot_ok() {
          for name in example.com www.baidu.com; do
            ${pkgs.dnsutils}/bin/dig +time=3 +tries=1 +short @127.0.0.1 -p ${toString port.unbound} "$name" 2>/dev/null \
              | ${pkgs.gnugrep}/bin/grep -q . || return 1
          done
          return 0
        }

        nm_connectivity() {
          ${pkgs.networkmanager}/bin/nmcli -t networking connectivity 2>/dev/null || echo unknown
        }

        # ---- Mode A (Clash closed) --------------------------------------------
        # Plaintext resolvers are appended only inside two bounded, logged windows,
        # because a portal's resolver is usually the only one that answers before the
        # portal has been passed; outside them a DoT outage stops DNS loudly.
        #
        # mode_a_state() must run in the current shell: it updates dot_fails, so a
        # subshell would re-open the window on every tick.
        PL_TTL=120
        window_until=0
        dot_fails=0
        MODE_A_STATE=""
        state_reason="mode-a: starting, encrypted upstream first"

        now_epoch() { ${pkgs.coreutils}/bin/date +%s; }
        open_window() { window_until=$(( $(now_epoch) + PL_TTL )); }
        window_open() { [ "$(now_epoch)" -lt "$window_until" ]; }

        # The reason file tracks the current state, so a recovery clears "degraded".
        write_reason() {
          if [ -f /run/dns-pac/reason ] \
             && [ "$(${pkgs.coreutils}/bin/cat /run/dns-pac/reason)" = "$state_reason" ]; then
            return 0
          fi
          printf '%s\n' "$state_reason" > /run/dns-pac/reason
        }

        mode_a_state() {
          if dot_ok; then
            [ "$dot_fails" -ge 3 ] && echo "dns-pac: encrypted upstream recovered" >&2
            dot_fails=0
            state_reason="mode-a: encrypted upstream healthy"
            MODE_A_STATE="dot"
            return 0
          fi

          dot_fails=$((dot_fails + 1))
          [ "$dot_fails" = 1 ] && open_window

          if [ -n "$(isp_servers)" ]; then
            plaintext_note=""
          else
            plaintext_note=" [no DHCP resolver available: the window has no server]"
          fi

          if [ -e /run/dns-pac/force-plaintext ]; then
            state_reason="mode-a: operator override (/run/dns-pac/force-plaintext)$plaintext_note"
            MODE_A_STATE="degraded-plaintext"
            return 0
          fi

          if window_open; then
            state_reason="mode-a: DoT failed $dot_fails time(s); bounded plaintext window open (portal/bootstrap)$plaintext_note"
            MODE_A_STATE="bootstrap-plaintext"
            return 0
          fi

          conn="$(nm_connectivity)"
          if [ "$conn" = "portal" ] || [ "$conn" = "limited" ]; then
            open_window
            state_reason="mode-a: captive portal (NetworkManager=$conn), DoT unreachable -> plaintext DNS$plaintext_note"
            MODE_A_STATE="portal-plaintext"
            return 0
          fi

          state_reason="mode-a: DoT down, fail-closed (DNS stops; nothing degrades to plaintext). Remedies: touch /run/dns-pac/force-plaintext, or fix tcp/853"
          MODE_A_STATE="degraded-dot-only"
          return 0
        }

        mkdir -p /run/dns-pac

        # Start encrypted-only: dnsmasq needs a clash-independent upstream, and the
        # upgrade to mihomo happens only after two consecutive wins.
        state_reason="mode-a: starting, encrypted upstream first"
        write_state dot
        current="dot"
        hits=0
        misses=0
        last_ident=""

        while true; do
          if ! $CLASH_ON || ! dns_listener_up; then
            # Mode A. A change of uplink identity opens the bootstrap window.
            ident="$(dhcp_identity)"
            if [ "$ident" != "$last_ident" ]; then
              last_ident="$ident"
              open_window
              echo "dns-pac: uplink identity changed; plaintext bootstrap window opened" >&2
            fi

            mode_a_state
            new_status="$MODE_A_STATE"
            if [ "$new_status" != "$current" ]; then
              echo "DNS upstream changed from [$current] to [$new_status]: $state_reason"
              write_state "$new_status"
              current="$new_status"
            fi
            [ "$MODE_A_STATE" = "dot" ] && healthy=1 || healthy=0
            hits=0
            misses=0
          else
            if dns_ok; then
              hits=$((hits+1))
              misses=0
            else
              misses=$((misses+1))
              hits=0
            fi

            new_status="$current"
            if [ "$hits" -ge 2 ]; then
              new_status="proxy"
              state_reason="mode-b: mihomo DNS answered both probes"
            elif [ "$misses" -ge 2 ]; then
              # Core up, its DNS not answering: encrypted DoT only, and loud.
              new_status="degraded-dot-only"
              state_reason="mode-b: core up but its DNS probe failed; encrypted DoT only"
            fi

            if [ "$new_status" != "$current" ]; then
              echo "DNS upstream changed from [$current] to [$new_status]. Switching..."
              write_state "$new_status"
              current="$new_status"
              hits=0
              misses=0
            fi
            [ "$current" = "proxy" ] && healthy=1 || healthy=0
          fi

          # Back off only when the path is settled and healthy; every transition,
          # degradation or open window keeps the fast interval.
          if [ "$healthy" = 1 ] && [ "$new_status" = "$current" ]; then changed=0; else changed=1; fi
          write_reason
          if [ "$changed" = 1 ]; then
            interval=$MIN
          elif [ "$interval" -lt "$MAX" ]; then
            interval=$(( interval * 2 > MAX ? MAX : interval * 2 ))
          fi
          nap "$interval"
        done
      ''
      }";

      Restart = "always";
      RestartSec = "5";
    };
  };

  # dnsmasq must not start before dns-pac's initial write, but dns-pac's stop
  # must not take it down: keep the ordering, use wants instead of requires.
  systemd.services.dnsmasq.wants = [ "dns-pac.service" ];
  systemd.services.dnsmasq.after = [ "dns-pac.service" "unbound.service" ];

  # Pre-seed servers.conf with the DoT default so dnsmasq can start even if
  # dns-pac has not run yet; the script rewrites it at startup and on a switch.
  systemd.tmpfiles.rules = [
    "d /run/dns-pac 0755 root root -"
    "f /run/dns-pac/servers.conf 0644 root root - server=127.0.0.1#${toString port.unbound}"
  ];
}

