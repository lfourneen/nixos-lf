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
    systemctl = "${pkgs.systemd}/bin/systemctl";
    nmcli = "${pkgs.networkmanager}/bin/nmcli";
    wireless = m.wireless.name;
    split_profiles = config.my.proxy.splitDnsProfileUuids;
    # Private exclusions are also what keep conditional upstreams outside
    # Mihomo's DNS hijack. Public split upstreams are deliberately unsupported.
    split_cidrs = lib.optionals config.my.hardening.allowPrivateEgress (m.privateV4 ++ m.ulaV6);
    runtime = "/run/dns-upstream";
  });
  # Plaintext resolvers used only inside the bounded Mode-A window, and only
  # when the link supplies none. Never Unbound upstreams, and a different
  # operator from the validated endpoints (asserted in network.nix).
  transportFallback = lib.concatStringsSep " " m.dotTransportFallback;
  # Upstreams the probe contacts directly (TLS) so a validator without a
  # verdict can be attributed to the network or to the validator.
  dotProbeUpstreams = lib.concatMapStringsSep " "
    (ip: "${ip}@${toString port.dot}#${m.dotAuthName}") m.dotUpstreams;
  resolvedSettings = pkgs.writeText "resolved-policy.json" (builtins.toJSON {
    ip = "${pkgs.iproute2}/bin/ip";
    resolvectl = "${pkgs.systemd}/bin/resolvectl";
    tun = config.my.proxy.tunDev;
    tailnet = {
      enabled = config.services.tailscale.enable && config.my.hardening.tailscale.magicDns;
      cli = "${config.services.tailscale.package}/bin/tailscale";
      interface = config.services.tailscale.interfaceName;
      resolver = m.magicDns;
      domain_file = "/run/netsec-tailnet/domain";
    };
  });

in
{
  my.proxy.resolvedPolicySettings = resolvedSettings;
  systemd.services.dns-split-revoke = {
    description = "Revoke split DNS before a managed connection changes";
    unitConfig.StartLimitIntervalSec = 0;
    unitConfig.OnFailure = [ "netsec-alert@%n.service" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.python3}/bin/python3 ${./netsec/dns_policy.py} ${policySettings} --revoke-split";
      ExecStartPost = [
        "${pkgs.python3}/bin/python3 ${./netsec/alerts.py} clear dns-split-revoke.service"
        "${pkgs.systemd}/bin/systemctl start --no-block proxy-net-wake.service"
      ];
      TimeoutStartSec = 10;
      RuntimeDirectory = "dns-policy";
      RuntimeDirectoryPreserve = true;
      NoNewPrivileges = true;
      CapabilityBoundingSet = [ "CAP_NET_ADMIN" ];
      ProtectSystem = "strict";
      ProtectHome = true;
      ReadWritePaths = [ "/run/proxy-mode" "/run/netsec" ];
      RestrictAddressFamilies = [ "AF_UNIX" "AF_NETLINK" ];
    };
  };
  # Kernel timeout accounting can pause across suspend. Revoke before sleep,
  # prevent the broker regranting, then reconcile using CLOCK_BOOTTIME on wake.
  systemd.services.dns-policy-sleep = {
    description = "Revoke temporary DNS permissions across suspend";
    requiredBy = [ "sleep.target" ];
    before = [ "sleep.target" ];
    unitConfig.StopWhenUnneeded = true;
    unitConfig.OnFailure = [ "netsec-alert@%n.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      TimeoutStartSec = 15;
      TimeoutStopSec = 10;
      RuntimeDirectory = "dns-policy";
      RuntimeDirectoryPreserve = true;
      ExecStart = "${pkgs.python3}/bin/python3 ${./netsec/dns_sleep.py} ${policySettings} before";
      # Runs even after a failed start. Do not wait here: resume is ordered after
      # sleep.target and must also handle a canceled sleep transaction.
      ExecStopPost = "${pkgs.systemd}/bin/systemctl start --no-block dns-policy-resume.service";
      NoNewPrivileges = true;
      CapabilityBoundingSet = [ "CAP_NET_ADMIN" ];
    };
  };
  systemd.services.dns-policy-resume = {
    description = "Recover DNS leases after resume or a failed sleep hook";
    after = [ "sleep.target" "dns-policy-sleep.service" "netsec-alert@dns-policy-sleep.service.service" ];
    unitConfig.StartLimitIntervalSec = 0;
    unitConfig.OnFailure = [ "netsec-alert@%n.service" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.python3}/bin/python3 ${./netsec/dns_sleep.py} ${policySettings} resume";
      ExecStartPost = [
        "${pkgs.python3}/bin/python3 ${./netsec/alerts.py} clear dns-policy-sleep.service"
        "${pkgs.python3}/bin/python3 ${./netsec/alerts.py} clear dns-policy-resume.service"
        "${pkgs.systemd}/bin/systemctl start --no-block proxy-net-wake.service"
      ];
      Restart = "on-failure";
      RestartSec = "2s";
      TimeoutStartSec = 20;
      RuntimeDirectory = "dns-policy";
      RuntimeDirectoryPreserve = true;
      NoNewPrivileges = true;
      CapabilityBoundingSet = [ "CAP_NET_ADMIN" ];
      ProtectSystem = "strict";
      ProtectHome = true;
      RestrictAddressFamilies = [ "AF_UNIX" "AF_NETLINK" ];
      ReadWritePaths = [ "/run/proxy-mode" "/run/netsec" ];
    };
  };
  systemd.services.dns-policy = {
    description = "Apply bounded DNS address leases under the policy lock";
    after = [ "nftables.service" ];
    unitConfig.StartLimitIntervalSec = 0;
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
    after = [ "systemd-resolved.service" ] ++ lib.optional config.services.tailscale.enable "tailscaled-set.service";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.python3}/bin/python3 ${./netsec/resolved_policy.py} ${resolvedSettings}";
      TimeoutStartSec = 20;
      RuntimeDirectory = "netsec-tailnet";
      RuntimeDirectoryPreserve = true;
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
      # The verdict history outlives the boot on purpose: a failed generation is
      # rolled back from the boot menu, so the evidence must not be tmpfs-only.
      StateDirectory = "dns-upstream";
      ReadWritePaths = [ "/run/dns-upstream" "/run/netsec" "/var/lib/dns-upstream" ];
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
        hits=0; misses=0; dot_fails=0; dot_grace_rounds=0; ever_secure=0
        prime_rounds=''${NETSEC_DOT_PRIME_ROUNDS:-1}
        history=/var/lib/dns-upstream/history.log
        history_lines=400
        dot_state=not-probed
        dot_result='{"status":"not-probed","rcode":null,"ede":[],"detail":"not-probed"}'
        budget_until=0; window_until=0
        no_resolver_logged=0
        PL_TTL=120; PL_TOTAL=1800
        ready_sent=0
        policy_version=-1
        split='{"entries":[],"context":null}'
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
          local probe_exit=0
          # The probe separates the local validator verdict from the encrypted
          # transport, so a slow or unreachable DoT upstream can never masquerade
          # as a signature error. The direct stage is bounded by two 5s TLS
          # attempts on top of the 8s local query.
          dot_result=$(NETSEC_DOT_PROBE_UPSTREAMS=${lib.escapeShellArg dotProbeUpstreams} \
            ${pkgs.python3}/bin/python3 ${./netsec/dnssec_probe.py} ${toString port.unbound}) || probe_exit=$?
          # A failed helper must not break state publication or look secure.
          if ! printf '%s' "$dot_result" | jq -e 'type == "object" and (.status | type == "string")' >/dev/null 2>&1; then
            dot_result='{"status":"indeterminate","rcode":null,"ede":[],"detail":"probe-failed"}'
            probe_exit=4
          fi
          case "$probe_exit" in
            0) dot_state=secure; return 0 ;;
            2) dot_state=unavailable ;;
            3) dot_state=validation-error ;;
            *) dot_state=indeterminate ;;
          esac
          return 1
        }
        dns_listener_up() {
          ${config.my.proxy.listenerCheck} core-dns >/dev/null 2>&1
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
            --argjson success "$last_success" --arg dnssec "$dot_state" --argjson probe "$dot_result" \
            '{mode:$mode,reason:$reason,session:$session,budget_until:$budget,window_until:$window,
              remaining_seconds:([$budget-$now,0]|max),plaintext_override:$override,
              monotonic_seconds:$now,policy_version:$version,last_success_monotonic:$success,dnssec_status:$dnssec,dnssec_probe:$probe}' \
              > "$runtime/state.json.$$" && mv -f "$runtime/state.json.$$" "$runtime/state.json"
          atomic_write "$runtime/reason" "$state_reason"
          atomic_write "$runtime/status" "$current"
          record_history
        }
        record_history() {
          # Persist the verdict so a rolled-back boot still has the evidence.
          jq -nc --arg t "$(date -u +%FT%TZ)" --arg mode "$current" \
            --arg reason "$state_reason" --arg verdict "$dot_state" \
            --arg ident "$last_ident" --argjson probe "$dot_result" \
            --argjson budget "$budget_until" --argjson window "$window_until" \
            '{time:$t,mode:$mode,reason:$reason,verdict:$verdict,identity:$ident,probe:$probe,budget_until:$budget,window_until:$window}' \
            >> "$history" 2>/dev/null || return 0
          if [ "$(wc -l < "$history" 2>/dev/null || echo 0)" -gt "$history_lines" ]; then
            tail -n "$history_lines" "$history" > "$history.$$" && mv -f "$history.$$" "$history"
          fi
        }
        apply_state() {
          local target="$1" new_conf pid notify=0
          apply_reason=""
          new_conf='server=127.0.0.1#${toString port.unbound}'
          if [ "$target" = proxy ]; then
            new_conf='server=127.0.0.1#${toString port.mihomoDns}'
          elif [ "$target" = split-error ] || [ "$target" = resolver-error ]; then
            # no-resolv + an empty servers-file disables forwarding. Do not
            # send unknown enterprise suffixes to a default public resolver.
            new_conf=""
          elif [[ "$target" == *plaintext ]]; then
            new_conf=""
            for server in $servers; do new_conf="$new_conf"$'\n'"server=$server"; done
          fi
          split_conf=$(printf '%s' "$split" | jq -r '.context.interface as $iface | .entries[] | "server=/" + .domain + "/" + .server + "@" + $iface')
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
            atomic_write "$runtime/servers.conf" "$new_conf" || { apply_reason='cannot write servers.conf'; return 1; }
            if systemctl is-active --quiet dnsmasq.service; then
              timeout 8 systemctl reload dnsmasq.service || { apply_reason='dnsmasq reload failed'; return 1; }
            else
              timeout 8 systemctl start dnsmasq.service || { apply_reason='dnsmasq start failed'; return 1; }
            fi
            systemctl is-active --quiet dnsmasq.service || { apply_reason='dnsmasq is not active'; return 1; }
            last_pid=$(systemctl show -p MainPID --value dnsmasq.service) || last_pid=""
            [ -n "$last_pid" ] && [ "$last_pid" != 0 ] || { apply_reason='dnsmasq has no main pid'; return 1; }
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
          timeout 10 systemctl start dns-policy.service || { apply_reason='DNS lease broker failed'; return 1; }
          [ "$(jq -r '.status' /run/dns-policy/state.json)" = applied ] || { apply_reason='DNS leases not applied'; return 1; }
          # A mode flip changes the version under a running pass; that is not a
          # resolver failure, so say which check gave up.
          [ "$(jq -r '.policy_version // .version' /run/proxy-mode/state.json)" = "$policy_version" ] \
            || { apply_reason="policy generation moved to $(jq -r '.policy_version // .version' /run/proxy-mode/state.json)"; return 1; }
          if [ "$current" != "$target" ]; then
            echo "dns-upstream: $current -> $target ($state_reason)"
            case "$target" in
              *plaintext|dot-priming) echo "dns-upstream: verdict=$dot_state probe=$dot_result" ;;
            esac
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
          split_ok=1
          if split=$(${pkgs.python3}/bin/python3 ${./netsec/dns_policy.py} ${policySettings} --split); then
            if [ -e /run/netsec/alerts/dns-split-policy.json ]; then
              ${pkgs.python3}/bin/python3 ${./netsec/alerts.py} clear dns-split-policy
            fi
          else
            split='{"entries":[],"context":null}'; split_ok=0
            if [ ! -e /run/netsec/alerts/dns-split-policy.json ]; then
              ${pkgs.python3}/bin/python3 ${./netsec/alerts.py} set dns-split-policy 'cannot read approved split DNS; forwarding disabled'
            fi
          fi
          policy_version=$(jq -r '.policy_version // .version // -1' /run/proxy-mode/state.json 2>/dev/null || echo -1)
          mode=$(jq -r '.mode // "unknown"' /run/proxy-mode/state.json 2>/dev/null || echo unknown)
          new_status=blocked
          state_reason="mode-$mode: encrypted resolver only"
          servers=""
          dot_state=not-probed
          dot_result='{"status":"not-probed","rcode":null,"ede":[],"detail":"not-probed"}'
          if ! ${config.my.proxy.listenerCheck} dns >/dev/null 2>&1; then
            new_status=resolver-error; state_reason='local DNS listener identity mismatch'
          elif [ "$mode" = proxy ] && { ! $CLASH_ON || ! dns_listener_up; }; then
            new_status=resolver-error; state_reason='mihomo DNS listener identity mismatch'
            hits=0; misses=0
          elif [ "$mode" = proxy ]; then
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
            # A link without usable resolvers still has a bounded way out, so the
            # encrypted path failing is never an unqualified blackout.
            [ -n "$servers" ] || servers=${lib.escapeShellArg transportFallback}
            if [ "$ident" != "$last_ident" ]; then
              last_ident="$ident"; dot_fails=0; dot_grace_rounds=0; ever_secure=0
              load_budget
            fi
            if dot_ok; then
              dot_grace_rounds=0; dot_fails=0; no_resolver_logged=0; ever_secure=1
              new_status=dot
              state_reason='mode-a: encrypted upstream and DNSSEC validation healthy'
              # A recovered encrypted path earns a fresh plaintext budget, so a
              # later outage is not punished for one that already ended.
              if [ "$budget_until" != 0 ] || [ "$window_until" != 0 ]; then
                budget_until=0; window_until=0
                save_budget
              fi
            elif [ "$ever_secure" = 0 ] && [ "$dot_state" = indeterminate ] \
                 && [ "$dot_grace_rounds" -lt "$prime_rounds" ]; then
              # A cold validator has to prime its trust anchor before its first
              # verdict means anything; a 3s deadline used to turn that into a
              # permanent "validation failed".
              dot_grace_rounds=$((dot_grace_rounds + 1))
              new_status=dot-priming
              state_reason='mode-a: waiting for the first validated answer'
            else
              dot_fails=$((dot_fails + 1))
              new_status=degraded-dot-only
              state_reason='mode-a: encrypted resolver only; no plaintext upstream'
              if [ -n "$servers" ]; then
                no_resolver_logged=0
                [ "$dot_fails" != 1 ] || open_window
                now=$(now_mono)
                if [ -e "$runtime/force-plaintext" ]; then
                  new_status=degraded-plaintext
                  state_reason="mode-a: explicit plaintext override ($dot_state)"
                elif [ "$now" -lt "$window_until" ]; then
                  new_status=bootstrap-plaintext
                  state_reason="mode-a: bounded plaintext window ($dot_state)"
                elif [ "$now" -lt "$budget_until" ]; then
                  # Renew the window while the budget allows it: a flaky
                  # encrypted upstream must not end in a manual recovery, and the
                  # budget still caps the total plaintext time per session.
                  conn=$(nmcli -t networking connectivity 2>/dev/null || true)
                  open_window
                  if [ "$conn" = portal ] || [ "$conn" = limited ]; then
                    new_status=portal-plaintext
                    state_reason="mode-a: bounded portal plaintext window ($dot_state)"
                  else
                    new_status=bootstrap-plaintext
                    state_reason="mode-a: bounded plaintext window ($dot_state)"
                  fi
                else
                  state_reason="mode-a: plaintext budget spent; encrypted resolver only ($dot_state)"
                fi
              elif [ "$no_resolver_logged" = 0 ]; then
                no_resolver_logged=1
                echo 'dns-upstream: no DHCP or configured plaintext resolver; encrypted resolver only' >&2
              fi
            fi
          else
            hits=0; misses=0
          fi

          if [ "$split_ok" = 0 ]; then
            new_status=split-error; servers=""
            state_reason='split DNS policy unreadable: forwarding disabled'
          fi
          previous="$current"
          if ! apply_state "$new_status"; then
            # Keep last_conf unchanged so the next pass must retry. Do not claim
            # that a desired server list is already the active resolver state.
            current=pending
            state_reason="DNS apply failed: $apply_reason; retrying"
            publish_dns
            echo "dns-upstream: apply failed ($apply_reason); retrying" >&2
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
    "d /run/dns-policy 0755 root root -"
    "f /run/dns-policy/split-epoch 0644 root root - 0"
    "d /run/dns-upstream 0755 root root -"
    "f /run/dns-upstream/servers.conf 0644 root root - server=127.0.0.1#${toString port.unbound}"
  ];
}

