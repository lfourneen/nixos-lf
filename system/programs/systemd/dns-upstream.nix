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
        dns-upstream.nix reads the fallback resolvers from the DHCP lease of the one
        interface that runs a DHCP client, but this configuration has
        ${toString (lib.length dhcpIfaces)} of them
        (${lib.concatStringsSep ", " dhcpIfaces}). Pin the interface or fix
        networking.interfaces.
      '';
    }
  ];

  # DNS upstream: mihomo DNS while clash runs, unbound DoT otherwise, with the DHCP
  # resolvers only inside the bounded windows below.
  systemd.services.dns-upstream = {
    description = "DNS upstream: mihomo DNS when clash is up, DoT otherwise";
    after = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    unitConfig.StartLimitIntervalSec = 0;
    serviceConfig = {
      RuntimeDirectory = "dns-upstream";
      # Keep /run/dns-upstream/servers.conf across a restart: dnsmasq reads it at start
      # and the tmpfiles seed lives in this directory, so removing it on stop (the
      # default) would leave dnsmasq with no conf-file to read.
      RuntimeDirectoryPreserve = true;

      # Root only for D-Bus (systemctl restart dnsmasq + resolvectl flush-caches):
      # no caps and no writes outside /run/dns-upstream. AF_NETLINK is for ss(8).
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
      ReadWritePaths = [ "/run/dns-upstream" ];

      ExecStart = "${pkgs.writeShellScript "dns-upstream-loop" ''
        CLASH_ON=${config.my.proxy.isClashOn}

        # Event-driven, adaptive sleep (same mechanism as proxy-mode.nix):
        # proxy-net-wake sends SIGWINCH on a Clash core change, which interrupts
        # `wait`; SIGWINCH is ignore-by-default, so an early wake cannot kill the
        # process before the trap is installed. The interval adapts -- 2 s while
        # transitioning/degraded, up to 15 s once the path is settled and healthy.
        nap_pid=""
        interval=2
        MIN=2
        # Backstop cap: short while a degradation or a plaintext window is in
        # flight, long once the path is settled (dot or proxy). Events still drive
        # the transitions, so the cap only bounds a missed event.
        cap_for() { case "$1" in dot|proxy) echo 60 ;; *) echo 15 ;; esac; }
        trap 'woken=1' WINCH
        # woken=1 when a wake arrived (during a nap OR while a probe was running);
        # the flag is consumed at the next decision round, so an event that lands
        # inside the ~10 s Mode-A probe path is not lost.
        woken=1
        nap() {
          ${pkgs.coreutils}/bin/sleep "$1" &
          nap_pid=$!
          wait "$nap_pid" 2>/dev/null
          rc=$?
          kill "$nap_pid" 2>/dev/null || true
          nap_pid=""
          [ "$rc" -gt 128 ] && woken=1
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
            | ${pkgs.gnused}/bin/sed -n 's/^domain_name_servers=//p; s/^new_domain_name_servers=//p; s/^IP4\.DNS\[[0-9]\+\]://p' \
            | ${pkgs.coreutils}/bin/tr -s ' \t' '\n' \
            | ${pkgs.gnugrep}/bin/grep -vE '^(0\.0\.0\.0|127\.|169\.254\.|255\.255\.255\.255)' \
            | ${pkgs.gnugrep}/bin/grep -E '^((25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])$')"
          if [ -z "$found" ]; then
            echo "dns-upstream: no DHCP resolver available (dhcpcd -U/nmcli); no plaintext fallback" >&2
            return 0
          fi

          printf 'server=%s\n' $found
        }

        # Identity of the link: a change means a new network, which opens the
        # bootstrap window.
        dhcp_identity() {
          lease_resolvers \
            | ${pkgs.gnugrep}/bin/grep -E '^(ip_address|routers|domain_name_servers|new_domain_name_servers)=|^IP4\.(ADDRESS|GATEWAY|DNS)' \
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
          printf '%s\n' "$new_conf" > /run/dns-upstream/servers.conf

          # State for proxy-status plus a reason: degradations name themselves here.
          printf '%s\n' "$1" > /run/dns-upstream/status
          printf '%s\n' "$state_reason" > /run/dns-upstream/reason

          # Only restart when the upstream really changed: dnsmasq's StartLimit
          # (5 per 10s) must not be hit by a flapping probe, and a redundant
          # write needs no restart. Record the new upstream only once the
          # restart has actually converged; otherwise the next round re-issues it
          # (a restart left `last_conf` claiming a state dnsmasq never reached).
          if [ "$new_conf" != "''${last_conf:-}" ]; then
            if ${pkgs.systemd}/bin/systemctl restart dnsmasq.service 2>/dev/null \
               && ${pkgs.systemd}/bin/systemctl is-active --quiet dnsmasq.service; then
              last_conf="$new_conf"
            else
              echo "dns-upstream: dnsmasq restart did not converge; retrying" >&2
            fi
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
          # NOTE: with fake-ip (the TUN template), mihomo synthesizes an answer
          # locally, so an unreachable upstream no longer makes this probe fail --
          # node death is detected by proxy-mode's route-aware probe instead. This
          # still catches a hung/dead mihomo DNS handler (no answer at all), which
          # is what keeps the DoT fallback below meaningful.
          for name in www.baidu.com www.gstatic.com; do
            ${pkgs.dnsutils}/bin/dig +time=2 +tries=1 +short @127.0.0.1 -p ${toString port.mihomoDns} \
              "$name" 2>/dev/null | ${pkgs.gnugrep}/bin/grep -q . || return 1
          done
          return 0
        }

        # Health of the encrypted path itself: dnsmasq's own failover is a hang
        # (one failed upstream means the client waits ~8 s for no SERVFAIL).
        # Probe a per-round random label: it can never be answered from cache, so
        # a stale cached name cannot make a cut DoT path look healthy. A live
        # resolver answers it (NXDOMAIN is fine); a cut one times out.
        dot_ok() {
          st=$(${pkgs.dnsutils}/bin/dig +time=3 +tries=1 @127.0.0.1 -p ${toString port.unbound} \
            "$(${pkgs.coreutils}/bin/date +%s%N).probe.example.com" A 2>/dev/null \
            | ${pkgs.gnugrep}/bin/grep -oE 'status: [A-Z]+' | head -1)
          [ "$st" = "status: NOERROR" ] || [ "$st" = "status: NXDOMAIN" ]
        }

        nm_connectivity() {
          ${pkgs.networkmanager}/bin/nmcli -t networking connectivity 2>/dev/null || echo unknown
        }

        # The client resolver belongs on mihomo only while proxy-mode records the
        # strict `proxy` state. In `blocked` (TUN missing, or node down) and in
        # `direct` it must not: fake-ip would hand out addresses with no route, and
        # mihomo's own upstreams are refused anyway.
        proxy_mode_ok() {
          ${pkgs.gnugrep}/bin/grep -q '^proxy$' /run/proxy-mode/status 2>/dev/null
        }

        # ---- Mode A (Clash closed) --------------------------------------------
        # Plaintext resolvers are appended only inside two bounded, logged windows,
        # because a portal's resolver is usually the only one that answers before the
        # portal has been passed; outside them a DoT outage stops DNS loudly.
        #
        # mode_a_state() must run in the current shell: it updates dot_fails, so a
        # subshell would re-open the window on every tick.
        PL_TTL=120
        PL_TOTAL=300        # total plaintext seconds allowed per uplink identity
        window_until=0
        budget_until=0      # 0 = unspent; else the absolute end of the total budget
        dot_fails=0
        MODE_A_STATE=""
        state_reason="mode-a: starting, encrypted upstream first"

        now_epoch() { ${pkgs.coreutils}/bin/date +%s; }
        # Open a plaintext window, capped by the per-identity total budget: the
        # window never outlives budget_until, and once the budget is spent it does
        # not reopen, so a sustained NetworkManager `limited`/`portal` verdict can
        # no longer keep cleartext DNS open indefinitely.
        open_window() {
          now=$(now_epoch)
          [ "$budget_until" = 0 ] && budget_until=$(( now + PL_TOTAL ))
          w=$(( now + PL_TTL ))
          [ "$w" -gt "$budget_until" ] && w=$budget_until
          [ "$w" -gt "$now" ] && window_until=$w
        }
        window_open() { [ "$(now_epoch)" -lt "$window_until" ]; }
        budget_spent() { [ "$budget_until" != 0 ] && [ "$(now_epoch)" -ge "$budget_until" ]; }

        # The reason file tracks the current state, so a recovery clears "degraded".
        write_reason() {
          if [ -f /run/dns-upstream/reason ] \
             && [ "$(${pkgs.coreutils}/bin/cat /run/dns-upstream/reason)" = "$state_reason" ]; then
            return 0
          fi
          printf '%s\n' "$state_reason" > /run/dns-upstream/reason
        }

        mode_a_state() {
          if dot_ok; then
            [ "$dot_fails" -ge 3 ] && echo "dns-upstream: encrypted upstream recovered" >&2
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

          if [ -e /run/dns-upstream/force-plaintext ]; then
            state_reason="mode-a: operator override (/run/dns-upstream/force-plaintext)$plaintext_note"
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
            if budget_spent; then
              state_reason="mode-a: plaintext budget ($PL_TOTAL s) spent for this uplink; DoT down, fail-closed (DNS stops). Remedies: touch /run/dns-upstream/force-plaintext, or fix tcp/853"
              MODE_A_STATE="degraded-dot-only"
              return 0
            fi
            open_window
            state_reason="mode-a: captive portal (NetworkManager=$conn), DoT unreachable -> plaintext DNS$plaintext_note"
            MODE_A_STATE="portal-plaintext"
            return 0
          fi

          state_reason="mode-a: DoT down, fail-closed (DNS stops; nothing degrades to plaintext). Remedies: touch /run/dns-upstream/force-plaintext, or fix tcp/853"
          MODE_A_STATE="degraded-dot-only"
          return 0
        }

        mkdir -p /run/dns-upstream

        # Start encrypted-only: dnsmasq needs a clash-independent upstream, and the
        # upgrade to mihomo happens only after two consecutive wins.
        state_reason="mode-a: starting, encrypted upstream first"
        write_state dot
        current="dot"
        hits=0
        misses=0
        last_ident=""

        while true; do
          if ! $CLASH_ON || ! dns_listener_up || ! proxy_mode_ok; then
            # Mode A, or Clash is on but proxy-mode is `blocked`/`direct`: use the
            # encrypted resolver, because mihomo is not the path the host trusts.
            # A change of uplink identity opens the bootstrap window.
            ident="$(dhcp_identity)"
            if [ "$ident" != "$last_ident" ]; then
              last_ident="$ident"
              # A new link gets a fresh plaintext budget.
              budget_until=0
              open_window
              echo "dns-upstream: uplink identity changed; plaintext bootstrap window opened" >&2
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
          else
            cap=$(cap_for "$current")
            [ "$interval" -lt "$cap" ] && interval=$(( interval * 2 > cap ? cap : interval * 2 ))
          fi
          # An event wake while a switch is still in flight must not wait a whole
          # interval for the second confirmation: re-evaluate at once (the woken
          # flag is pre-cleared so this repeats at most once).
          if [ "$woken" = 1 ] && [ "$changed" = 1 ]; then
            woken=0
            continue
          fi
          nap "$interval"
        done
      ''
      }";

      Restart = "always";
      RestartSec = "5";
    };
  };

  # dnsmasq must not start before dns-upstream's initial write, but dns-upstream's stop
  # must not take it down: keep the ordering, use wants instead of requires.
  systemd.services.dnsmasq.wants = [ "dns-upstream.service" ];
  systemd.services.dnsmasq.after = [ "dns-upstream.service" "unbound.service" ];

  # Pre-seed servers.conf with the DoT default so dnsmasq can start even if
  # dns-upstream has not run yet; the script rewrites it at startup and on a switch.
  systemd.tmpfiles.rules = [
    "d /run/dns-upstream 0755 root root -"
    "f /run/dns-upstream/servers.conf 0644 root root - server=127.0.0.1#${toString port.unbound}"
  ];
}

