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

  # Network PAC, proxy-only and fail-closed: gost runs only while clash-verge's own
  # listener is verified, otherwise :33332/:33333 refuse instead of dialing out.
  systemd.services.gost-pac = {
    description = "Gost PAC (proxy-only, fail-closed)";
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

        # nft/Clash bake 987; a drifted runtime uid means the exemption no longer
        # matches this process, so say so instead of failing silently.
        if [ "$(${pkgs.coreutils}/bin/id -u)" != "987" ]; then
          echo "gost-pac: WARN running as uid $(${pkgs.coreutils}/bin/id -u); killswitch/TUN exemption expects 987" >&2
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

        start_proxy() {
          # Proxy-only: gost never dials a target itself, so there is no real-IP
          # egress path. Loopback-only; SO_REUSEPORT keeps a restart from refusing.
          env http_proxy= https_proxy= all_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= \
            "${pkgs.gost}/bin/gost" \
              "-L=http://127.0.0.1:33332?reuseport=true" \
              "-L=redirect://127.0.0.1:33333?reuseport=true" \
              "-L=redirect://[::1]:33333?reuseport=true" \
              -F=http://127.0.0.1:7897 &
          new_pid=$!

          sleep 1
          if ! kill -0 "$new_pid" 2>/dev/null; then
            # Stay closed instead of lying: reap the dead child, keep the state.
            echo "gost-pac: gost failed to start; staying closed" >&2
            ${pkgs.systemd}/bin/systemd-cat -t gost-pac -p err ${pkgs.coreutils}/bin/echo \
              "gost-pac: gost failed to start; proxy stays closed" || true
            wait "$new_pid" 2>/dev/null
            return 1
          fi

          proxy_pid="$new_pid"
          echo "gost-pac status -> proxy (pid $new_pid)"

          if ! listen_ok; then
            echo "gost-pac: WARN status=proxy but 127.0.0.1:33332/33333 are not both listening" >&2
            ${pkgs.systemd}/bin/systemd-cat -t gost-pac -p warning ${pkgs.coreutils}/bin/echo \
              "gost-pac: status=proxy without bound listeners" || true
          fi
        }

        cleanup() {
          echo "Stopping proxy supervisor..."
          stop_current
          exit 0
        }
        trap cleanup TERM INT

        # Fail closed from the first second: no listener until the core is verified.
        write_state closed

        while true; do
          # A crash after the 1s start check would leave both ports unbound while
          # the state file still claims proxy. Heal it first, back to closed.
          if [ -n "$proxy_pid" ] && ! kill -0 "$proxy_pid" 2>/dev/null; then
            echo "gost-pac: instance pid $proxy_pid is gone; closing" >&2
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
                echo "gost-pac: pid $proxy_pid is alive but 127.0.0.1:33332/33333 are not listening; closing" >&2
                listen_fail=0
                stop_current
                mode="closed"
              fi
            fi
          fi

          # Two signals: :7897 must be clash-verge's (identity) and answer a real
          # proxied request twice in a row; staying in proxy only needs identity.
          if core_up; then core_id=1; else core_id=0; fi

          if ${pkgs.coreutils}/bin/timeout 3 ${pkgs.curl}/bin/curl -s -o /dev/null \
               -w '%{http_code}' --noproxy "" -x http://127.0.0.1:7897 \
               ${healthProbeUrl} 2>/dev/null \
             | ${pkgs.gnugrep}/bin/grep -qE '^(200|204)$'; then
            core_e2e=1
          else
            core_e2e=0
          fi

          if [ "$core_e2e" = 1 ]; then good=$((good+1)); else good=0; fi

          want="closed"
          if [ "$core_id" = 1 ] && { [ "$mode" = "proxy" ] || [ "$good" -ge 2 ]; }; then
            want="proxy"
          fi

          if [ "$want" != "$mode" ]; then
            if [ "$want" = "proxy" ]; then
              if start_proxy; then mode="proxy"; good=0; fi
            else
              # Fail closed: retire the instance, so callers get refused instead
              # of being forwarded into a core that is gone or is not ours.
              echo "gost-pac: core gone (or not clash-verge's); closing the proxy" >&2
              stop_current
              mode="closed"
            fi
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

