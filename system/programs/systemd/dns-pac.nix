{ pkgs, ... }:

{
  # DNS PAC (fail-open, mirrors gost-pac): clash up -> dnsmasq forwards to mihomo
  # (1053, hidden DoH DNS); clash down -> public DNS. resolved sees only the
  # always-up dnsmasq gateway, so no dead-1053 hang; switch <= 6s, caches flushed.
  # Probe = real CN-domain resolution through mihomo, so "up but DNS chain dead"
  # also fails over to public DNS.
  systemd.services.dns-pac = {
    description = "DNS PAC: mihomo-only DNS when clash is up, public DNS otherwise";
    after = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      ExecStart = "${pkgs.writeShellScript "dns-pac-loop" ''

        write_state() {
          case "$1" in
            proxy)
              printf 'server=127.0.0.1#1053\n' > /run/dns-pac/servers.conf
              ;;
            direct)
              printf 'server=223.5.5.5\nserver=119.29.29.29\n' > /run/dns-pac/servers.conf
              ;;
          esac

          # Flush dnsmasq and resolved caches on upstream change.
          ${pkgs.systemd}/bin/systemctl restart dnsmasq.service
          ${pkgs.systemd}/bin/resolvectl flush-caches
        }

        probe() {
          # geosite:cn names: mihomo must drain them through its policy DoH
          # (doh.pub/alidns) to answer; timeout/SERVFAIL/refused -> dead.
          # www.baidu.com = de-facto CN connectivity probe + www.qq.com 
          # as second stable anchor.
          for name in www.baidu.com www.qq.com; do
            ${pkgs.dnsutils}/bin/dig +time=1 +tries=1 +short @127.0.0.1 -p 1053 \
              "$name" 2>/dev/null | ${pkgs.gnugrep}/bin/grep -q . && return 0
          done
          return 1
        }

        mkdir -p /run/dns-pac

        # SAFE DEFAULT: start in direct (public DNS) unconditionally — the file
        # must exist and point at a live, clash-independent upstream before
        # dnsmasq starts. The loop upgrades to mihomo within <=5s if the probe
        # succeeds; a false-positive "proxy" state at boot is now impossible.
        write_state direct
        current="direct"

        while true; do
          if probe; then
            new_status="proxy"
          else
            new_status="direct"
          fi

          # Switch only on backend state change
          if [ "$new_status" != "$current" ]; then
            echo "DNS upstream changed from [$current] to [$new_status]. Switching..."
            write_state "$new_status"
            current="$new_status"
          fi

          sleep 5
        done
      ''
      }";

      Restart = "always";
      RestartSec = "5";
    };
  };

  # dnsmasq must never start before dns-pac: order (after) plus hard ordering
  # (requires). dns-pac's initial write_state also runs `systemctl restart
  # dnsmasq`, which on an inactive unit simply STARTS it — so the conf-file is
  # guaranteed to exist at dnsmasq's first exec regardless of boot order.
  systemd.services.dnsmasq.requires = [ "dns-pac.service" ];
  systemd.services.dnsmasq.after = [ "dns-pac.service" ];
}

