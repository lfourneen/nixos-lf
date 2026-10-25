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
  policySettings = pkgs.writeText "dns-policy.json" (builtins.toJSON {
    nft = "${pkgs.nftables}/bin/nft";
    nmcli = "${pkgs.networkmanager}/bin/nmcli";
    wireless = m.wireless.name;
    split_profiles = config.my.proxy.splitDnsProfileUuids;
    runtime = "/run/dns-upstream";
  });
  resolvedSettings = pkgs.writeText "resolved-policy.json" (builtins.toJSON {
    ip = "${pkgs.iproute2}/bin/ip";
    resolvectl = "${pkgs.systemd}/bin/resolvectl";
    tun = config.my.proxy.tunDev;
  });
  revokeDns = ''
    exec 9>/run/proxy-mode/lock
    ${pkgs.util-linux}/bin/flock -x -w 5 9
    ${pkgs.nftables}/bin/nft -f - <<'NFT_DNS_REVOKE'
    flush set inet lf_filter dns_plaintext4
    flush set inet lf_filter dns_split4
    flush set inet lf_filter dns_split6
    NFT_DNS_REVOKE
  '';

in
{
  my.proxy.resolvedPolicySettings = resolvedSettings;
  # Kernel timeout accounting can pause across suspend. Revoke before sleep,
  # prevent the broker regranting, then reconcile using CLOCK_BOOTTIME on wake.
  systemd.services.dns-policy-sleep = {
    description = "Revoke temporary DNS permissions across suspend";
    wantedBy = [ "sleep.target" ];
    before = [ "sleep.target" ];
    unitConfig.StopWhenUnneeded = true;
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      TimeoutStartSec = 10;
      TimeoutStopSec = 10;
      RuntimeDirectory = "dns-policy";
      RuntimeDirectoryPreserve = true;
      ExecStart = pkgs.writeShellScript "dns-policy-before-sleep" ''
        set -eu
        touch /run/dns-policy/sleeping
        ${revokeDns}
      '';
      ExecStop = pkgs.writeShellScript "dns-policy-after-sleep" ''
        set -eu
        ${revokeDns}
        rm -f /run/dns-policy/sleeping
        ${pkgs.systemd}/bin/systemctl start --no-block proxy-net-wake.service
      '';
      NoNewPrivileges = true;
      CapabilityBoundingSet = [ "CAP_NET_ADMIN" ];
    };
  };
  systemd.services.dns-policy = {
    description = "Apply bounded DNS address leases under the policy lock";
    after = [ "nftables.service" ];
    unitConfig.OnFailure = [ "netsec-alert@%n.service" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.python3}/bin/python3 ${./netsec/dns_policy.py} ${policySettings}";
      ExecStartPost = "${pkgs.python3}/bin/python3 ${./netsec/alerts.py} clear dns-policy.service";
      TimeoutStartSec = 10;
      RuntimeDirectory = "dns-policy";
      RuntimeDirectoryPreserve = true;
      NoNewPrivileges = true;
      CapabilityBoundingSet = [ "CAP_NET_ADMIN" ];
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      RestrictAddressFamilies = [ "AF_UNIX" "AF_NETLINK" ];
      ReadWritePaths = [ "/run/proxy-mode" "/run/netsec" ];
    };
  };
  systemd.services.dns-resolved-policy = {
    description = "Keep resolved on the managed DNS gateway";
    after = [ "systemd-resolved.service" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.python3}/bin/python3 ${./netsec/resolved_policy.py} ${resolvedSettings}";
      TimeoutStartSec = 15;
      NoNewPrivileges = true;
      CapabilityBoundingSet = "";
      ProtectSystem = "strict";
      ProtectHome = true;
      RestrictAddressFamilies = [ "AF_UNIX" "AF_NETLINK" ];
    };
  };
  # DNS upstream: mihomo DNS while clash runs, unbound DoT otherwise, with the DHCP
  # resolvers only inside the bounded windows below.
  systemd.services.dns-upstream = {
    description = "DNS upstream: mihomo DNS when clash is up, DoT otherwise";
    after = [ "network.target" "nftables.service" ];
    wantedBy = [ "multi-user.target" ];
    unitConfig.StartLimitIntervalSec = 0;
    unitConfig.OnFailure = [ "netsec-alert@%n.service" ];
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
      ReadWritePaths = [ "/run/dns-upstream" "/run/netsec" ];
      ExecStartPost = "${pkgs.python3}/bin/python3 ${./netsec/alerts.py} clear dns-upstream.service";
      Type = "notify";
      NotifyAccess = "all";
      WatchdogSec = "90s";
      TimeoutStartSec = "90s";

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
        ready_sent=0
        policy_version=-1
        split='[]'
        last_success=0
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
          ${lib.optionalString (config.my.proxy.plaintextBootBudgetSeconds != null) ''
          if [ -r "$runtime/boot-budget" ]; then
            read -r boot_until < "$runtime/boot-budget"
          else
            boot_until=$((now + ${toString config.my.proxy.plaintextBootBudgetSeconds}))
            atomic_write "$runtime/boot-budget" "$boot_until"
          fi
          [[ "$boot_until" =~ ^[0-9]+$ ]] || return 1
          [ "$budget_until" -le "$boot_until" ] || budget_until=$boot_until
          ''}
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
        publish_dns() {
          now=$(now_mono)
          override=false
          [ ! -e "$runtime/force-plaintext" ] || override=true
          jq -n --arg mode "$current" --arg reason "$state_reason" --arg session "$last_ident" \
            --argjson budget "$budget_until" --argjson window "$window_until" \
            --argjson now "$now" --argjson override "$override" --argjson version "$policy_version" \
            --argjson success "$last_success" \
            '{mode:$mode,reason:$reason,session:$session,budget_until:$budget,window_until:$window,
              remaining_seconds:([$budget-$now,0]|max),plaintext_override:$override,
              monotonic_seconds:$now,policy_version:$version,last_success_monotonic:$success}' \
              > "$runtime/state.json.$$" && mv -f "$runtime/state.json.$$" "$runtime/state.json"
          atomic_write "$runtime/reason" "$state_reason"
          atomic_write "$runtime/status" "$current"
        }
        apply_state() {
          local target="$1" new_conf pid notify=0
          new_conf='server=127.0.0.1#${toString port.unbound}'
          if [ "$target" = proxy ]; then
            new_conf='server=127.0.0.1#${toString port.mihomoDns}'
          elif [[ "$target" == *plaintext ]]; then
            new_conf=""
            for server in $servers; do new_conf="$new_conf"$'\n'"server=$server"; done
          fi
          split_conf=$(printf '%s' "$split" | jq -r '.[] | "server=/" + .domain + "/" + .server')
          [ -z "$split_conf" ] || new_conf="$new_conf"$'\n'"$split_conf"
          # Re-check after slow probes: a concurrent on request must never
          # publish a plaintext resolver list after A has ended.
          if [[ "$target" == *plaintext ]] \
             && { [ "$(jq -r '.mode // "unknown"' /run/proxy-mode/state.json 2>/dev/null)" != direct ] || $CLASH_ON; }; then
            target=blocked
            state_reason='proxy transition: encrypted resolver only'
            new_conf='server=127.0.0.1#${toString port.unbound}'
          fi
          pid=$(systemctl show -p MainPID --value dnsmasq.service 2>/dev/null || true)
          if [ "$new_conf" != "$last_conf" ] || [ "$pid" != "$last_pid" ] || [ -z "$pid" ] || [ "$pid" = 0 ]; then
            atomic_write "$runtime/servers.conf" "$new_conf" || return 1
            if systemctl is-active --quiet dnsmasq.service; then
              timeout 8 systemctl reload dnsmasq.service || return 1
            else
              timeout 8 systemctl start dnsmasq.service || return 1
            fi
            systemctl is-active --quiet dnsmasq.service || return 1
            last_pid=$(systemctl show -p MainPID --value dnsmasq.service) || return 1
            [ -n "$last_pid" ] && [ "$last_pid" != 0 ] || return 1
            last_conf="$new_conf"
            resolvectl flush-caches || true
          fi
          now=$(now_mono)
          until=0
          [[ "$target" != *plaintext ]] || until=$window_until
          if [[ "$target" == *plaintext ]] && [ -e "$runtime/force-plaintext" ]; then until=$((now + 120)); fi
          jq -n --argjson version "$policy_version" --argjson now "$now" --argjson until "$until" \
            --arg servers "$servers" --argjson split "$split" \
            '{policy_version:$version,monotonic_seconds:$now,plaintext_until:$until,
              plaintext:[$servers|splits("[[:space:]]+")|select(length>0)],split:$split}' > "$runtime/policy.json.$$"
          mv -f "$runtime/policy.json.$$" "$runtime/policy.json"
          timeout 10 systemctl start dns-policy.service || return 1
          [ "$(jq -r '.policy_version // .version' /run/proxy-mode/state.json)" = "$policy_version" ] || return 1
          if [ "$current" != "$target" ]; then
            echo "dns-upstream: $current -> $target ($state_reason)"
            [ "$target" != proxy ] || notify=1
          fi
          current="$target"
          last_success=$(now_mono)
          publish_dns
          if [ "$notify" = 1 ]; then ${config.my.proxy.requestVerify}; fi
        }

        # A safe seed exists from tmpfiles. Reconcile every pass, including a
        # failed reload and same-status DHCP DNS changes; no state-only shortcut.
        # Release dnsmasq's After= dependency before asking systemd to start it.
        # This is process readiness; state.json remains pending until apply.
        systemd-notify --ready
        ready_sent=1
        while true; do
          woken=0
          timeout 15 systemctl start dns-resolved-policy.service || true
          split=$(${pkgs.python3}/bin/python3 ${./netsec/dns_policy.py} ${policySettings} --split 2>/dev/null || echo '[]')
          policy_version=$(jq -r '.policy_version // .version // -1' /run/proxy-mode/state.json 2>/dev/null || echo -1)
          mode=$(jq -r '.mode // "unknown"' /run/proxy-mode/state.json 2>/dev/null || echo unknown)
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
            current=pending
            state_reason='DNS apply or policy generation changed; retrying'
            publish_dns
            echo 'dns-upstream: dnsmasq apply failed; retrying' >&2
            interval=2
          elif [ "$current" != "$previous" ] || { [ "$current" != dot ] && [ "$current" != proxy ]; }; then
            interval=2
          elif [ "$interval" -lt 60 ]; then
            interval=$((interval * 2 > 60 ? 60 : interval * 2))
          fi
          if [ "$ready_sent" = 0 ]; then systemd-notify --ready; ready_sent=1; fi
          systemd-notify WATCHDOG=1
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

