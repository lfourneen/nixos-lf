{ pkgs, ... }:

{
  # DNS PAC: clash up -> mihomo DNS (1053); down -> unbound DoT (1055).
  # Probe needs the mihomo core port (7897) + a real answer; a dead core falls
  # back immediately, other failures need 2 consecutive probes (anti ghost listener).
  # 2s polling bounds the worst-case ghost-core window to ~4s (2 misses).
  systemd.services.dns-pac = {
    description = "DNS PAC: mihomo DNS when clash is up, DoT otherwise";
    after = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    unitConfig.StartLimitIntervalSec = 0;
    serviceConfig = {
      RuntimeDirectory = "dns-pac";
      ExecStart = "${pkgs.writeShellScript "dns-pac-loop" ''

        write_state() {
          case "$1" in
            proxy)
              upstream='server=127.0.0.1#1053'
              ;;
            direct)
              # Encrypted DoT only; no plaintext fallback.
              upstream='server=127.0.0.1#1055'
              ;;
          esac
          printf '%s\n' "$upstream" > /run/dns-pac/servers.conf

          # State for proxy-status.
          printf '%s\n' "$1" > /run/dns-pac/status

          # Only restart when the upstream really changed: dnsmasq's StartLimit
          # (5 per 10s) must not be hit by a flapping probe, and a redundant
          # write needs no restart. The cache flush is cheap, so keep it.
          if [ "$upstream" != "''${last_upstream:-}" ]; then
            last_upstream="$upstream"
            ${pkgs.systemd}/bin/systemctl restart --no-block dnsmasq.service || true
          fi
          ${pkgs.systemd}/bin/resolvectl flush-caches
        }

        core_up() {
          ${pkgs.coreutils}/bin/timeout 2 ${pkgs.bash}/bin/bash -c \
            'echo > /dev/tcp/127.0.0.1/7897' 2>/dev/null
        }

        dns_ok() {
          # CN names through mihomo's policy DoH: answers prove the chain works.
          for name in www.baidu.com www.qq.com; do
            ${pkgs.dnsutils}/bin/dig +time=1 +tries=1 +short @127.0.0.1 -p 1053 \
              "$name" 2>/dev/null | ${pkgs.gnugrep}/bin/grep -q . && return 0
          done
          return 1
        }

        mkdir -p /run/dns-pac

        # Start direct: the file must point at a clash-independent upstream
        # before dnsmasq starts. Upgrade to mihomo only after 2 consecutive wins.
        write_state direct
        current="direct"
        hits=0
        misses=0

        while true; do
          if ! core_up; then
            # Core socket gone -> 1053 cannot answer: fall back immediately
            # (no hysteresis so DIRECT never waits on a dead resolver).
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

