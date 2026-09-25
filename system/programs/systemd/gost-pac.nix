{ pkgs, lib, config, ... }:

let
  # Health-probe target for the "core really answers" gate: domestic and highly
  # reachable, so it proves mihomo's own chain rather than the node.
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

  # The nft redirect excludes the literal 987 so a passthrough relay cannot dial
  # itself; a second account claiming that uid would silently widen the exclusion.
  assertions = [
    {
      assertion =
        lib.count (u: u.uid == 987) (lib.attrValues config.users.users) == 1;
      message = ''
        gost-pac.nix: uid 987 must be used by exactly one account (gost).
        The nft redirect exclusion bakes the literal 987; a duplicate uid would
        silently widen it.
      '';
    }
  ];

  # Network PAC: with Clash on, gost only forwards to mihomo; with Clash off it is a
  # plain passthrough, so env-proxy consumers keep working without a warm-up.
  systemd.services.gost-pac = {
    description = "Gost PAC (forwards to mihomo, or passthrough when Clash is off)";
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
        mode="closed"          # closed = nothing listening; proxy = chained to mihomo
        proxy_pid=""
        good=0
        listen_fail=0
        degraded=0

        # The nat redirect excludes 987 so a passthrough relay cannot dial itself;
        # a drifted runtime uid would silently widen that exclusion.
        if [ "$(${pkgs.coreutils}/bin/id -u)" != "987" ]; then
          echo "gost-pac: WARN running as uid $(${pkgs.coreutils}/bin/id -u); the nat redirect exclusion expects 987" >&2
        fi

        # State-file contract: one mode word plus a newline, rewritten every round
        # (so its mtime only proves the supervisor is alive); `proxy-status` reads it.
        write_state() {
          printf '%s\n' "$1" > /run/gost-pac/status
        }

        # Identity, not reachability: an impostor can bind :7897 but cannot land in
        # clash-verge's cgroup, which the kernel reports through ss(8).
        core_up() {
          ${pkgs.iproute2}/bin/ss -lnteH 'sport = :7897' 2>/dev/null \
            | ${pkgs.gnugrep}/bin/grep -q 'cgroup:/system.slice/clash-verge.service'
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
          # proxy: only forward to mihomo, never dialing a target itself. direct:
          # passthrough, which is what "Clash is off" means.
          if [ "$1" = "proxy" ]; then
            env http_proxy= https_proxy= all_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= \
              "${pkgs.gost}/bin/gost" \
                "-L=http://127.0.0.1:33332?reuseport=true" \
                "-L=redirect://127.0.0.1:33333?reuseport=true" \
                "-L=redirect://[::1]:33333?reuseport=true" \
                -F=http://127.0.0.1:7897 &
          else
            env http_proxy= https_proxy= all_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= \
              "${pkgs.gost}/bin/gost" \
                "-L=http://127.0.0.1:33332?reuseport=true" \
                "-L=redirect://127.0.0.1:33333?reuseport=true" \
                "-L=redirect://[::1]:33333?reuseport=true" &
          fi
          new_pid=$!

          sleep 1
          if ! kill -0 "$new_pid" 2>/dev/null; then
            # Stay as we are instead of lying: reap the dead child, keep the state.
            echo "gost-pac: gost failed to start in [$1]" >&2
            ${pkgs.systemd}/bin/systemd-cat -t gost-pac -p err ${pkgs.coreutils}/bin/echo \
              "gost-pac: gost failed to start in [$1]" || true
            wait "$new_pid" 2>/dev/null
            return 1
          fi

          proxy_pid="$new_pid"
          echo "gost-pac status -> $1 (pid $new_pid)"

          if ! listen_ok; then
            echo "gost-pac: WARN status=$1 but 127.0.0.1:33332/33333 are not both listening" >&2
            ${pkgs.systemd}/bin/systemd-cat -t gost-pac -p warning ${pkgs.coreutils}/bin/echo \
              "gost-pac: status=$1 without bound listeners" || true
          fi
        }

        cleanup() {
          echo "Stopping proxy supervisor..."
          stop_current
          exit 0
        }
        trap cleanup TERM INT

        # Nothing is served until the first round decides between proxy and passthrough.
        write_state closed

        while true; do
          # A crash after the 1s start check would leave both ports unbound while
          # the state file still claims a mode. Heal it first, back to unknown.
          if [ -n "$proxy_pid" ] && ! kill -0 "$proxy_pid" 2>/dev/null; then
            echo "gost-pac: instance pid $proxy_pid is gone; restarting" >&2
            proxy_pid=""
            mode="closed"
            listen_fail=0
          fi

          if [ -n "$proxy_pid" ]; then
            # Process alive but its listeners disappeared: close after two misses
            # in a row, so a start-up race cannot cause churn.
            if listen_ok; then
              listen_fail=0
            else
              listen_fail=$((listen_fail + 1))
              if [ "$listen_fail" -ge 2 ]; then
                echo "gost-pac: pid $proxy_pid is alive but 127.0.0.1:33332/33333 are not listening; restarting" >&2
                listen_fail=0
                stop_current
                mode="closed"
              fi
            fi
          fi

          # Proxy mode owns the redirect + killswitch; with it off gost is a plain
          # passthrough (real IP, nothing loaded), which is what "direct" means.
          if ${pkgs.coreutils}/bin/cat /run/proxy-mode/status 2>/dev/null | ${pkgs.gnugrep}/bin/grep -q '^proxy$'; then
            proxy_mode=1
          else
            proxy_mode=0
          fi

          core_id=0
          core_e2e=0
          want="direct"
          if [ "$proxy_mode" = 1 ]; then
            # Two signals: :7897 must be clash-verge's (identity) and answer a real
            # proxied request twice in a row; staying in proxy only needs identity.
            if core_up; then core_id=1; fi

            if ${pkgs.coreutils}/bin/timeout 3 ${pkgs.curl}/bin/curl -s -o /dev/null \
                 -w '%{http_code}' --noproxy "" -x http://127.0.0.1:7897 \
                 ${healthProbeUrl} 2>/dev/null \
               | ${pkgs.gnugrep}/bin/grep -qE '^(200|204)$'; then
              core_e2e=1
            fi
            if [ "$core_e2e" = 1 ]; then good=$((good+1)); else good=0; fi

            want="closed"
            if [ "$core_id" = 1 ] && { [ "$mode" = "proxy" ] || [ "$good" -ge 2 ]; }; then
              want="proxy"
            fi
          fi

          if [ "$want" != "$mode" ]; then
            case "$want" in
              proxy)
                if start_gost proxy; then mode="proxy"; good=0; fi
                ;;
              direct)
                # Passthrough from the first round: nothing points at a core when
                # Clash is off, so env-proxy consumers must not be left refused.
                if start_gost direct; then
                  mode="direct"
                  echo "gost-pac: Clash is off; passthrough relay on 127.0.0.1:33332" >&2
                fi
                ;;
              *)
                if [ "$mode" = "proxy" ]; then
                  echo "gost-pac: core gone (or not clash-verge's); closing" >&2
                fi
                stop_current
                mode="closed"
                ;;
            esac
          fi

          # Identity holds but the proxied probe fails: there is nothing better to
          # fail over to, so keep serving and log it - once, then every 12th round.
          if [ "$mode" = "proxy" ] && [ "$core_id" = 1 ] && [ "$core_e2e" = 0 ]; then
            degraded=$((degraded + 1))
            if [ "$degraded" = 1 ] || [ $((degraded % 12)) -eq 0 ]; then
              echo "gost-pac: WARN :7897 is clash-verge's but the proxied probe ${healthProbeUrl} failed for $degraded round(s); staying proxy" >&2
            fi
          else
            if [ "$degraded" -gt 0 ]; then
              if [ "$core_e2e" = 1 ]; then
                echo "gost-pac: proxied probe recovered after $degraded degraded round(s)" >&2
              else
                echo "gost-pac: degraded streak ended (core unverified or mode changed) after $degraded round(s)" >&2
              fi
            fi
            degraded=0
          fi

          write_state "$mode"
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

