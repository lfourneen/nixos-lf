{ pkgs, ... }:

{
  # DNS PAC: clash up -> mihomo DNS (1053); down -> unbound DoT (1055).
  # Probe needs the mihomo core port (7897) + a real answer; a dead core falls
  # back immediately, other failures need 2 consecutive probes (anti ghost listener).
  systemd.services.dns-pac = {
    description = "DNS PAC: mihomo DNS when clash is up, DoT otherwise";
    after = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    unitConfig.StartLimitIntervalSec = 0;
    serviceConfig = {
      ExecStart = "${pkgs.writeShellScript "dns-pac-loop" ''

        write_state() {
          case "$1" in
            proxy)
              printf 'server=127.0.0.1#1053\n' > /run/dns-pac/servers.conf
              ;;
            direct)
              # Encrypted DoT (unbound) first, plaintext only as last resort
              printf 'server=127.0.0.1#1055\nserver=223.5.5.5\n' > /run/dns-pac/servers.conf
              ;;
          esac

          # Flush dnsmasq and resolved caches on upstream change.
          ${pkgs.systemd}/bin/systemctl restart dnsmasq.service
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

          sleep 5
        done
      ''
      }";

      Restart = "always";
      RestartSec = "5";
    };
  };

  # dnsmasq must not start before dns-pac's initial write (conf-file exists at
  # its first exec); the restart inside write_state starts it if inactive.
  systemd.services.dnsmasq.requires = [ "dns-pac.service" ];
  systemd.services.dnsmasq.after = [ "dns-pac.service" "unbound.service" ];
}

