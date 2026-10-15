{ pkgs, config, lib, ... }:

let
  m = config.my.machine;
  port = m.ports;

  dhcpIfaces = lib.attrNames (lib.filterAttrs (_: v: v.useDHCP == true) config.networking.interfaces);
  dhcpcdExec = config.systemd.services.dhcpcd.serviceConfig.ExecStart;
  dhcpcdConfig = builtins.head (builtins.match ".* --config ([^ ]+).*" dhcpcdExec);
  linkSettings = pkgs.writeText "dns-uplink.json" (builtins.toJSON {
    ip = "${pkgs.iproute2}/bin/ip";
    nmcli = "${pkgs.networkmanager}/bin/nmcli";
    dhcpcd = "${pkgs.dhcpcd}/sbin/dhcpcd";
    dhcpcd_config = dhcpcdConfig;
    dhcp_interfaces = dhcpIfaces;
    interfaces = [ m.wired.name m.wireless.name ];
  });

in
{
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
      # and the tmpfiles seed/budgets live here; keep them across service restarts.
      RuntimeDirectoryPreserve = true;

      # Root only for D-Bus (dnsmasq reload/start + resolvectl flush-caches):
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
        PATH=${lib.makeBinPath [ pkgs.coreutils pkgs.systemd pkgs.jq pkgs.dnsutils pkgs.iproute2 pkgs.gnugrep pkgs.networkmanager ]}
        CLASH_ON=${config.my.proxy.isClashOn}
        runtime=/run/dns-upstream
        current=pending
        interval=2
        woken=0
        last_conf=""
        last_pid=""
        last_ident=""
        hits=0; misses=0; dot_fails=0
        budget_until=0; window_until=0
        no_resolver_logged=0
        PL_TTL=120; PL_TOTAL=300
        mkdir -p "$runtime/budgets"
        atomic_write() { printf '%s\n' "$2" > "$1.$$" && mv -f "$1.$$" "$1"; }
        trap 'woken=1' WINCH
        nap() {
          [ "$woken" = 0 ] || return 0
          sleep "$1" &
          nap_pid=$!
          # Close the signal-before-wait race as well as wakes during probes.
          if [ "$woken" = 0 ]; then wait "$nap_pid" 2>/dev/null || true; fi
          kill "$nap_pid" 2>/dev/null || true
          wait "$nap_pid" 2>/dev/null || true
        }
        now_mono() {
          read -r uptime _ < /proc/uptime
          printf '%s\n' "''${uptime%%.*}"
        }
        save_budget() {
          atomic_write "$runtime/budgets/$ident" "$budget_until $window_until"
        }
        load_budget() {
          budget_until=0; window_until=0
          if [ -r "$runtime/budgets/$ident" ]; then
            read -r b w < "$runtime/budgets/$ident" || true
            if [[ "$b $w" =~ ^[0-9]+\ [0-9]+$ ]]; then budget_until=$b; window_until=$w; fi
          fi
        }
        open_window() {
          now=$(now_mono)
          [ "$budget_until" != 0 ] || budget_until=$((now + PL_TOTAL))
          window_until=$((now + PL_TTL))
          [ "$window_until" -le "$budget_until" ] || window_until=$budget_until
          save_budget
        }
        dot_ok() {
          st=$(dig +time=3 +tries=1 @127.0.0.1 -p ${toString port.unbound} \
            "$(date +%s%N).probe.example.com" A 2>/dev/null)
          [[ "$st" == *"status: NOERROR"* || "$st" == *"status: NXDOMAIN"* ]]
        }
        dns_listener_up() {
          ss -lnteH 'sport = :${toString port.mihomoDns}' 2>/dev/null \
            | grep -q 'cgroup:/system.slice/clash-verge.service'
        }
        dns_ok() {
          for name in www.baidu.com www.gstatic.com; do
            answer=$(dig +time=2 +tries=1 +short @127.0.0.1 -p ${toString port.mihomoDns} "$name" A 2>/dev/null)
            [ -n "$answer" ] || return 1
          done
        }
        apply_state() {
          local target="$1" new_conf pid notify=0
          new_conf='server=127.0.0.1#${toString port.unbound}'
          if [ "$target" = proxy ]; then
            new_conf='server=127.0.0.1#${toString port.mihomoDns}'
          elif [[ "$target" == *plaintext ]]; then
            for server in $servers; do new_conf="$new_conf"$'\n'"server=$server"; done
          fi
          # Re-check after slow probes: a concurrent on request must never
          # publish a plaintext resolver list after A has ended.
          if [[ "$target" == *plaintext ]] \
             && { [ "$(cat /run/proxy-mode/status 2>/dev/null)" != direct ] || $CLASH_ON; }; then
            target=blocked
            state_reason='proxy transition: encrypted resolver only'
            new_conf='server=127.0.0.1#${toString port.unbound}'
          fi
          pid=$(systemctl show -p MainPID --value dnsmasq.service 2>/dev/null || true)
          if [ "$new_conf" != "$last_conf" ] || [ "$pid" != "$last_pid" ] || [ -z "$pid" ] || [ "$pid" = 0 ]; then
            atomic_write "$runtime/servers.conf" "$new_conf" || return 1
            if systemctl is-active --quiet dnsmasq.service; then
              systemctl reload dnsmasq.service || return 1
            else
              systemctl start dnsmasq.service || return 1
            fi
            systemctl is-active --quiet dnsmasq.service || return 1
            last_pid=$(systemctl show -p MainPID --value dnsmasq.service) || return 1
            [ -n "$last_pid" ] && [ "$last_pid" != 0 ] || return 1
            last_conf="$new_conf"
            resolvectl flush-caches || true
          fi
          if [ "$current" != "$target" ]; then
            echo "dns-upstream: $current -> $target ($state_reason)"
            [ "$target" != proxy ] || notify=1
          fi
          current="$target"
          atomic_write "$runtime/reason" "$state_reason"
          atomic_write "$runtime/status" "$current"
          if [ "$notify" = 1 ]; then systemctl start --no-block nftables-verify.service 2>/dev/null || true; fi
        }

        # A safe seed exists from tmpfiles. Reconcile every pass, including a
        # failed reload and same-status DHCP DNS changes; no state-only shortcut.
        while true; do
          woken=0
          mode=$(cat /run/proxy-mode/status 2>/dev/null || echo unknown)
          new_status=blocked
          state_reason="mode-$mode: encrypted resolver only"
          servers=""
          if [ "$mode" = proxy ] && $CLASH_ON && dns_listener_up; then
            if dns_ok; then hits=$((hits + 1)); misses=0; else hits=0; misses=$((misses + 1)); fi
            if [ "$hits" -ge 2 ]; then
              new_status=proxy; state_reason='mode-b: mihomo DNS healthy'
            elif [ "$current" = proxy ] && [ "$misses" -lt 2 ]; then
              new_status=proxy; state_reason='mode-b: one DNS miss, awaiting confirmation'
            else
              new_status=degraded-dot-only; state_reason='mode-b: awaiting mihomo DNS confirmation'
            fi
          elif [ "$mode" = direct ] && ! $CLASH_ON; then
            hits=0; misses=0
            link=$(${pkgs.python3}/bin/python3 ${./netsec/link.py} ${linkSettings} 2>/dev/null || echo '{}')
            ident=$(printf '%s' "$link" | jq -r '.identity // "offline"')
            servers=$(printf '%s' "$link" | jq -r '.servers[]?')
            if [ "$ident" != "$last_ident" ]; then
              last_ident="$ident"; dot_fails=0
              load_budget
            fi
            if dot_ok; then
              new_status=dot; dot_fails=0; no_resolver_logged=0
              state_reason='mode-a: encrypted upstream healthy'
            else
              dot_fails=$((dot_fails + 1))
              new_status=degraded-dot-only
              state_reason='mode-a: DoT unavailable; no permitted plaintext resolver'
              if [ -n "$servers" ]; then
                no_resolver_logged=0
                [ "$dot_fails" != 1 ] || open_window
                now=$(now_mono)
                if [ -e "$runtime/force-plaintext" ]; then
                  new_status=degraded-plaintext; state_reason='mode-a: explicit plaintext override'
                elif [ "$now" -lt "$window_until" ]; then
                  new_status=bootstrap-plaintext; state_reason='mode-a: bounded plaintext bootstrap'
                elif [ "$now" -lt "$budget_until" ]; then
                  conn=$(nmcli -t networking connectivity 2>/dev/null || true)
                  if [ "$conn" = portal ] || [ "$conn" = limited ]; then
                    open_window
                    new_status=portal-plaintext; state_reason='mode-a: bounded portal plaintext window'
                  fi
                else
                  state_reason='mode-a: plaintext deadline spent; encrypted resolver only'
                fi
              elif [ "$no_resolver_logged" = 0 ]; then
                no_resolver_logged=1
                echo 'dns-upstream: DoT is down and no DHCP resolver could be read; no plaintext fallback' >&2
              fi
            fi
          else
            hits=0; misses=0
          fi

          previous="$current"
          if ! apply_state "$new_status"; then
            # Keep last_conf unchanged so the next pass must retry. Do not claim
            # that a desired server list is already the active resolver state.
            atomic_write "$runtime/status" pending
            atomic_write "$runtime/reason" 'dnsmasq apply failed; retrying'
            echo 'dns-upstream: dnsmasq apply failed; retrying' >&2
            interval=2
          elif [ "$current" != "$previous" ] || { [ "$current" != dot ] && [ "$current" != proxy ]; }; then
            interval=2
          elif [ "$interval" -lt 60 ]; then
            interval=$((interval * 2 > 60 ? 60 : interval * 2))
          fi
          [ "$woken" = 0 ] || continue
          nap "$interval"
        done
      ''}";

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

