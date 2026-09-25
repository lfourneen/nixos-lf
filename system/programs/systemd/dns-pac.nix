{ pkgs, config, ... }:

{
  # DNS PAC: clash up -> mihomo DNS (1053); down -> unbound DoT (1055). The mode
  # decision is the shared is-clash-on script, and the listener must be clash's own.
  systemd.services.dns-pac = {
    description = "DNS PAC: mihomo DNS when clash is up, DoT otherwise";
    after = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    unitConfig.StartLimitIntervalSec = 0;
    serviceConfig = {
      RuntimeDirectory = "dns-pac";

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

        isp_servers() {
          $CLASH_ON && return 0

          lease=/var/lib/dhcpcd/ens1.lease
          if [ ! -r "$lease" ]; then
            echo "dns-pac: $lease is not readable; no ISP DNS fallback" >&2
            return 0
          fi

          found="$(${pkgs.gnused}/bin/sed -n 's/^domain_name_servers=//p; s/^new_domain_name_servers=//p' "$lease" \
            | ${pkgs.coreutils}/bin/tr -s ' \t' '\n' \
            | ${pkgs.gnugrep}/bin/grep -vE '^(0\.0\.0\.0|127\.|169\.254\.|255\.255\.255\.255)' \
            | ${pkgs.gnugrep}/bin/grep -E '^((25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])$')"
          if [ -z "$found" ]; then
            echo "dns-pac: no ISP resolver in $lease; no DNS fallback" >&2
            return 0
          fi

          printf 'server=%s\n' $found
        }

        write_state() {
          case "$1" in
            proxy)
              new_conf='server=127.0.0.1#1053'
              ;;
            direct)
              new_conf='server=127.0.0.1#1055'
              isp="$(isp_servers)"
              if [ -n "$isp" ]; then
                new_conf="$(printf '%s\n%s' "$new_conf" "$isp")"
              fi
              ;;
          esac
          printf '%s\n' "$new_conf" > /run/dns-pac/servers.conf

          # State for proxy-status.
          printf '%s\n' "$1" > /run/dns-pac/status

          # Only restart when the upstream really changed: dnsmasq's StartLimit
          # (5 per 10s) must not be hit by a flapping probe, and a redundant
          # write needs no restart. The cache flush is cheap, so keep it.
          if [ "$new_conf" != "''${last_conf:-}" ]; then
            last_conf="$new_conf"
            ${pkgs.systemd}/bin/systemctl restart --no-block dnsmasq.service || true
          fi
          ${pkgs.systemd}/bin/resolvectl flush-caches
        }

        # Identity, not reachability: a local impostor can bind 1053 but cannot land
        # in clash-verge's cgroup, which the kernel reports.
        dns_listener_up() {
          ${pkgs.iproute2}/bin/ss -lnteH 'sport = :1053' 2>/dev/null \
            | ${pkgs.gnugrep}/bin/grep -q 'cgroup:/system.slice/clash-verge.service'
        }

        dns_ok() {
          # baidu is DIRECT-policy, gstatic goes through the node (respect-rules):
          # requiring both proves the core and the node, not just a live core.
          for name in www.baidu.com www.gstatic.com; do
            ${pkgs.dnsutils}/bin/dig +time=2 +tries=1 +short @127.0.0.1 -p 1053 \
              "$name" 2>/dev/null | ${pkgs.gnugrep}/bin/grep -q . || return 1
          done
          return 0
        }

        mkdir -p /run/dns-pac

        # Start direct: the file must point at a clash-independent upstream
        # before dnsmasq starts. Upgrade to mihomo only after 2 consecutive wins.
        write_state direct
        current="direct"
        hits=0
        misses=0

        while true; do
          if ! $CLASH_ON || ! dns_listener_up; then
            # Clash is off, or its DNS listener is gone/not ours -> 1053 cannot
            # answer: fall back immediately (no hysteresis so DIRECT never waits).
            if [ "$current" != "direct" ]; then
              echo "clash core gone; DNS -> direct"
              write_state direct
              current="direct"
            fi
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
            elif [ "$misses" -ge 2 ]; then
              new_status="direct"
            fi

            if [ "$new_status" != "$current" ]; then
              echo "DNS upstream changed from [$current] to [$new_status]. Switching..."
              write_state "$new_status"
              current="$new_status"
              hits=0
              misses=0
            fi
          fi

          sleep 2
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
    "f /run/dns-pac/servers.conf 0644 root root - server=127.0.0.1#1055"
  ];
}

