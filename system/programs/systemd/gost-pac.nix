{ pkgs, lib, config, ... }:

let
  # Health-probe target: single source of truth, shared by the probe and the
  # WARN message. Keep it a domestic, highly reachable URL - an overseas target
  # makes the return-to-proxy depend on the current node reaching that site.
  healthProbeUrl = "https://www.baidu.com/";
in
{
  # Dedicated user so clash's tun.exclude-uid and network.nix nft rules can
  # target the proxy by UID without touching the desktop user's traffic.
  users.groups.gost = { };
  users.users.gost = {
    isSystemUser = true;
    group = "gost";
    uid = 987;   # static: dynamic system users have a null uid at eval time
  };

  # nftables and clash's tun.exclude-uid bake the literal 987; activation never
  # re-checks an explicit uid, so a second account claiming it would silently
  # widen the trusted egress set. Fail the build instead.
  assertions = [
    {
      assertion =
        lib.count (u: u.uid == 987) (lib.attrValues config.users.users) == 1;
      message = ''
        gost-pac.nix: uid 987 must be used by exactly one account (gost).
        The nftables killswitch exemption and Clash tun.exclude-uid bake the
        literal 987; a duplicate uid silently widens the trusted egress set.
      '';
    }
  ];

  # Network PAC (Fail-Open Architecture with Gost)
  systemd.services.gost-pac = {
    description = "Gost PAC High-Availability Proxy Daemon";
    after = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    # Never stop retrying (else a crash-loop leaves proxied apps without internet)
    unitConfig.StartLimitIntervalSec = 0;
    serviceConfig = {
      User = "gost";
      Group = "gost";
      StateDirectory = "gost";
      RuntimeDirectory = "gost-pac";
      # /run/gost-pac/status is read by the desktop user's `proxy-status`.
      RuntimeDirectoryMode = "0755";
      UMask = "0022";

      # Bound the blast radius of the :33333 redirect self-loop; systemd sets both
      # the soft and the hard limit from this one option.
      LimitNOFILE = 8192;

      # Unit-level backstop: the supervisor's own stop path is bounded, so a stuck
      # child can never wedge `systemctl stop`.
      TimeoutStopSec = 15;

      # Sandboxing. Deliberately not set (still need sandbox testing):
      # SystemCallFilter, RestrictAddressFamilies, ProtectProc/ProcSubset,
      # SystemCallArchitectures, LockPersonality. UMask/RuntimeDirectoryMode stay
      # as-is so `proxy-status` can read the state file.
      NoNewPrivileges = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      PrivateDevices = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      ProtectKernelLogs = true;
      ProtectClock = true;
      ProtectHostname = true;
      RestrictNamespaces = true;
      RestrictSUIDSGID = true;
      RestrictRealtime = true;
      RemoveIPC = true;
      CapabilityBoundingSet = "";

      # Internal implementation of persistent loop monitoring and hot-reloading
      ExecStart = "${pkgs.writeShellScript "gost-launcher" ''
        current_status="none"
        proxy_pid=""
        good=0
        last_flip=0
        listen_fail=0
        degraded=0
        last_live=""

        # nft/Clash bake 987; a drifted runtime uid means the exemption no longer
        # matches this process, so say so instead of failing silently.
        if [ "$(${pkgs.coreutils}/bin/id -u)" != "987" ]; then
          echo "gost-pac: WARN running as uid $(${pkgs.coreutils}/bin/id -u); killswitch/TUN exemption expects 987" >&2
        fi

        # State-file contract: exactly one mode word plus a newline; the desktop
        # user's `proxy-status` reads this path and format.
        write_state() {
          printf '%s\n' "$1" > /run/gost-pac/status
        }

        # Observe the sockets with ss(8): never connect() to :33333 - one
        # connection starts the redirect self-loop. Empty output means "cannot
        # inspect": treat as ok and let the pid check stay authoritative.
        listen_ok() {
          listening="$(${pkgs.iproute2}/bin/ss -ltn 2>/dev/null)"
          [ -n "$listening" ] || return 0
          case "$listening" in
            *127.0.0.1:33332*) ;;
            *) return 1 ;;
          esac
          case "$listening" in
            *127.0.0.1:33333*) ;;
            *) return 1 ;;
          esac
          return 0
        }

        stop_current() {
          if [ -n "$proxy_pid" ]; then
            kill "$proxy_pid" 2>/dev/null
            # Poll for up to 5s, then SIGKILL: never block the supervisor on a
            # stuck child (only an uninterruptible one can outlast this).
            for _ in 1 2 3 4 5; do
              kill -0 "$proxy_pid" 2>/dev/null || break
              sleep 1
            done
            if kill -0 "$proxy_pid" 2>/dev/null; then
              echo "gost-pac: pid $proxy_pid ignored SIGTERM; sending SIGKILL" >&2
              kill -9 "$proxy_pid" 2>/dev/null
            fi
            wait "$proxy_pid" 2>/dev/null
            proxy_pid=""
          fi
        }

        start_gost() {
          mode="$1"
          # :33332 HTTP proxy for proxy-aware apps; :33333 redirect
          # (transparent) for the rest (nftables sends their TCP here).
          # Loopback-only, like :33332. SO_REUSEPORT keeps a mode flip
          # from refusing connections.
          if [ "$mode" = "proxy" ]; then
            # Clash online: chain to the core at 7897
            env http_proxy= https_proxy= all_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= \
              "${pkgs.gost}/bin/gost" \
                "-L=http://127.0.0.1:33332?reuseport=true" \
                "-L=redirect://127.0.0.1:33333?reuseport=true" \
                "-L=redirect://[::1]:33333?reuseport=true" \
                -F=http://127.0.0.1:7897 &
          else
            # Clash offline: direct proxy (tun.exclude-uid keeps it out of TUN).
            env http_proxy= https_proxy= all_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= \
              "${pkgs.gost}/bin/gost" \
                "-L=http://127.0.0.1:33332?reuseport=true" \
                "-L=redirect://127.0.0.1:33333?reuseport=true" \
                "-L=redirect://[::1]:33333?reuseport=true" &
          fi
          new_pid=$!

          sleep 1
          if ! kill -0 "$new_pid" 2>/dev/null; then
            # Roll back instead of lying: keep the old instance and the state file
            # untouched, reap the dead child, and let the 5s loop retry.
            echo "gost-pac: gost failed to start in [$mode]; keeping current instance" >&2
            ${pkgs.systemd}/bin/systemd-cat -t gost-pac -p err ${pkgs.coreutils}/bin/echo \
              "gost-pac: gost failed to start in [$mode]; proxy may be stale" || true
            wait "$new_pid" 2>/dev/null
            return 1
          fi

          old_pid="$proxy_pid"
          proxy_pid="$new_pid"
          current_status="$mode"
          # State for proxy-status.
          write_state "$mode"
          echo "gost-pac status -> $mode (pid $new_pid)"

          if [ -n "$old_pid" ]; then
            # Drain only when the new mode is proxy: going proxy -> direct the old
            # instance is chained to the dead core, so overlapping it serves
            # failures.
            if [ "$mode" = "proxy" ]; then
              sleep 2
            fi
            kill "$old_pid" 2>/dev/null
            wait "$old_pid" 2>/dev/null
          fi

          # A mode with no bound listeners is a lie; check after the old instance
          # is retired so its sockets cannot answer for the new one.
          if ! listen_ok; then
            echo "gost-pac: WARN status=$mode but 127.0.0.1:33332/33333 are not both listening" >&2
            ${pkgs.systemd}/bin/systemd-cat -t gost-pac -p warning ${pkgs.coreutils}/bin/echo \
              "gost-pac: status=$mode without bound listeners" || true
          fi
        }

        cleanup() {
          echo "Stopping proxy supervisor..."
          stop_current
          exit 0
        }
        trap cleanup TERM INT

        while true; do
          # A crash after the 1s start check would leave both ports unbound while
          # the state file still claims the old mode. Heal it first.
          if [ -n "$proxy_pid" ] && ! kill -0 "$proxy_pid" 2>/dev/null; then
            echo "gost-pac: instance pid $proxy_pid ($current_status) is gone; restarting" >&2
            proxy_pid=""
            # Remember what the dead instance served: the re-selection below must
            # not demote a crashed proxy to direct (see the `none` branch).
            last_live="$current_status"
            current_status="none"
            listen_fail=0
          fi

          if [ -z "$proxy_pid" ]; then
            # No live instance => mode unknown; the flip logic below starts one
            # and keeps retrying every loop until a start succeeds.
            current_status="none"
            listen_fail=0
          else
            # Process alive but its listeners disappeared: restart after two
            # misses in a row, so a mode flip cannot cause churn.
            if listen_ok; then
              listen_fail=0
            else
              listen_fail=$((listen_fail + 1))
              if [ "$listen_fail" -ge 2 ]; then
                echo "gost-pac: pid $proxy_pid is alive but 127.0.0.1:33332/33333 are not listening; restarting" >&2
                listen_fail=0
                stop_current
                # It was live: remember its mode, as in the dead-child path.
                last_live="$current_status"
                # Mode unknown again; the flip logic below starts a fresh
                # instance.
                current_status="none"
              fi
            fi
          fi

          # Refresh the state file only while an instance is alive, so its mtime
          # is a real liveness signal for `proxy-status`.
          if [ -n "$proxy_pid" ] && kill -0 "$proxy_pid" 2>/dev/null; then
            write_state "$current_status"
          fi

          # Two-tier health judge.
          # Tier 1 (proxy -> direct) only asks whether the core's mixed port still
          # accepts TCP: an open port means the core is alive, and direct is not
          # better while the node cannot reach the probed site.
          # Tier 2 (boot, direct -> proxy) needs a real proxied answer twice in a
          # row. Local processes can bind :7897 while the core is down, but a dumb
          # socket cannot answer a request, so an impostor can never pull a direct
          # gost onto itself. Port open + tier 2 failing stays on proxy and warns;
          # direct is not better either. Probe target: healthProbeUrl above.
          if ${pkgs.coreutils}/bin/timeout 2 ${pkgs.bash}/bin/bash -c \
               'echo > /dev/tcp/127.0.0.1/7897' 2>/dev/null; then
            core_port_up=1
          else
            core_port_up=0
          fi

          if ${pkgs.coreutils}/bin/timeout 3 ${pkgs.curl}/bin/curl -s -o /dev/null \
               -w '%{http_code}' --noproxy "" -x http://127.0.0.1:7897 \
               ${healthProbeUrl} 2>/dev/null \
             | ${pkgs.gnugrep}/bin/grep -qE '^(200|204)$'; then
            core_e2e=1
          else
            core_e2e=0
          fi

          if [ "$core_e2e" = 1 ]; then good=$((good+1)); else good=0; fi

          new_status="$current_status"
          if [ "$current_status" = "none" ]; then
            # Boot: serve direct at once (fail-open) and upgrade only after two
            # tier-2 confirmations. "No live instance" also happens on the
            # self-heal paths above: with the port open they restore last_live
            # instead of demoting a previously-live proxy to direct. Cold start
            # has last_live empty, so it starts direct.
            if [ "$core_port_up" = 1 ] && { [ "$good" -ge 2 ] || [ "$last_live" = "proxy" ]; }; then
              new_status="proxy"
            else
              new_status="direct"
            fi
          elif [ "$core_port_up" = 0 ]; then
            # The only signal-driven (periodic) downgrade.
            new_status="direct"
          elif [ "$current_status" = "direct" ] && [ "$good" -ge 2 ]; then
            new_status="proxy"
          fi

          # Port open but no proxied answer while on proxy: nothing flips (direct
          # is not better), log it - once, then every 12th round.
          if [ "$current_status" = "proxy" ] && [ "$core_port_up" = 1 ] && [ "$core_e2e" = 0 ]; then
            degraded=$((degraded + 1))
            if [ "$degraded" = 1 ] || [ $((degraded % 12)) -eq 0 ]; then
              echo "gost-pac: WARN port 7897 accepts but the proxied probe ${healthProbeUrl} failed for $degraded round(s); keeping proxy (direct would not be better)" >&2
            fi
          else
            if [ "$degraded" -gt 0 ]; then
              # The streak also ends when the port closes or the instance/mode
              # changes; only a fresh tier-2 answer is a recovery.
              if [ "$core_e2e" = 1 ]; then
                echo "gost-pac: proxied probe recovered after $degraded degraded round(s)" >&2
              else
                echo "gost-pac: degraded streak ended (port closed or mode/instance changed) after $degraded round(s)" >&2
              fi
            fi
            degraded=0
          fi

          if [ "$new_status" != "$current_status" ]; then
            # Minimum residency for the return to proxy; direct is never cooled
            # (fail-open). The boot-time selection is initialisation, not an
            # oscillation, so it must not start the cooldown. Read the previous
            # status: start_gost() overwrites current_status.
            now="$(${pkgs.coreutils}/bin/date +%s)"
            prev_status="$current_status"
            if [ "$prev_status" = "none" ] || [ "$new_status" = "direct" ] || [ $((now - last_flip)) -ge 30 ]; then
              if start_gost "$new_status"; then
                good=0
                if [ "$prev_status" != "none" ]; then
                  last_flip="$now"
                fi
              fi
            fi
          fi

          sleep 5
        done
      ''
      }";

      # Sandbox the supervisor process environment
      Environment = [
        # Point HOME at the writable StateDirectory: /var/empty is read-only, so
        # gost logged a "failed to create certificate directory" warning on every
        # start and could not persist its .gost directory.
        "HOME=/var/lib/gost"
        "no_proxy=127.0.0.1,localhost,::1"
        "NO_PROXY=127.0.0.1,localhost,::1"
        "http_proxy="
        "https_proxy="
        "all_proxy="
        "HTTP_PROXY="
        "HTTPS_PROXY="
        "ALL_PROXY="
        # quieter gost: it logs every connection at info by default
        "GOST_LOGGER_LEVEL=warn"
      ];
      Restart = "always";
      RestartSec = "5";
    };
  };
}

