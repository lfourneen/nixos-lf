{ pkgs, lib, config, ... }:

let
  m = config.my.machine;
  port = m.ports;
  gostUid = m.uids.gost;
  sources = lib.fileset.toSource {
    root = ./netsec;
    fileset = lib.fileset.unions [ ./netsec/patch_gost.py ./netsec/gost_http.go ./netsec/gost_http_test.go ];
  };
  gost = pkgs.gost.overrideAttrs (old: {
    doCheck = true;
    postConfigure = (old.postConfigure or "") + ''
      chmod -R u+w vendor/github.com/go-gost/x/handler/http
      ${pkgs.python3}/bin/python3 ${sources}/patch_gost.py .
    '';
    checkPhase = ''
      runHook preCheck
      go test -mod=vendor github.com/go-gost/x/handler/http -run '^TestLF' -count=1
      runHook postCheck
    '';
  });

in
{
  my.proxy.gostPackage = gost;
  # Dedicated user so clash's tun.exclude-uid and network.nix nft rules can
  # target the proxy by UID without touching the desktop user's traffic.
  users.groups.gost = { };
  users.users.gost = {
    isSystemUser = true;
    group = "gost";
    uid = gostUid;   # static: dynamic system users have a null uid at eval time
  };

  # The nft redirect excludes this uid; a second account claiming it would widen
  # the exclusion silently.
  assertions = [
    {
      assertion =
        lib.count (u: u.uid == gostUid) (lib.attrValues config.users.users) == 1;
      message = ''
        gost-relay.nix: my.machine.uids.gost (${toString gostUid}) must belong to
        exactly one account (gost): the nft redirect exclusion uses it.
      '';
    }
  ];

  # Gost relay: with Clash on, gost only forwards to mihomo; with Clash off it is a
  # plain passthrough, so env-proxy consumers keep working without a warm-up.
  systemd.services.gost-relay = {
    description = "Gost relay (forwards to mihomo, or passthrough when Clash is off)";
    after = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    # Never stop retrying (else a crash-loop leaves proxied apps without internet)
    unitConfig.StartLimitIntervalSec = 0;
    serviceConfig = {
      User = "gost";
      Group = "gost";
      StateDirectory = "gost";
      RuntimeDirectory = "gost-relay";
      # /run/gost-relay/status is read by the desktop user's `proxy-status`.
      RuntimeDirectoryMode = "0755";
      UMask = "0022";

      # Bound the blast radius of the :${toString port.gostRedirect} redirect self-loop; systemd sets both
      # the soft and the hard limit from this one option.
      LimitNOFILE = 8192;
      MemoryHigh = "128M";
      MemoryMax = "512M";
      TasksMax = 512;
      LockPersonality = true;
      SystemCallArchitectures = "native";
      RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" "AF_NETLINK" ];

      # Unit-level backstop: the supervisor's own stop path is bounded, so a stuck
      # child can never wedge `systemctl stop`.
      TimeoutStopSec = 15;

      # Go still needs its normal syscall set; process/address-space confinement
      # is applied separately from a syscall allowlist. UMask/RuntimeDirectoryMode stay
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
        listen_fail=0

        # The nat redirect excludes this uid so a passthrough relay cannot dial itself;
        # a drifted runtime uid would silently widen that exclusion.
        if [ "$(${pkgs.coreutils}/bin/id -u)" != "${toString gostUid}" ]; then
          echo "gost-relay: WARN running as uid $(${pkgs.coreutils}/bin/id -u); the nat redirect exclusion expects ${toString gostUid}" >&2
        fi

        # Event-driven, adaptive sleep (see proxy-mode.nix): proxy-net-wake sends
        # SIGWINCH on a Clash core change, which interrupts `wait`; SIGWINCH is
        # ignore-by-default, so an early wake cannot kill this process before the
        # trap is installed. The interval adapts -- fast while a transition is
        # pending or the probe is degraded, up to 30 s when the mode is settled.
        nap_pid=""
        interval=5
        MIN=2
        MAX=30
        trap 'woken=1' WINCH
        # woken=1 when a wake arrived (during a nap OR while a probe was running);
        # the flag is consumed at the next decision round, so an event that lands
        # inside a probe is not lost.
        woken=1
        nap() {
          ${pkgs.coreutils}/bin/sleep "$1" &
          nap_pid=$!
          if [ "$woken" = 0 ]; then wait "$nap_pid" 2>/dev/null; rc=$?; else rc=129; fi
          kill "$nap_pid" 2>/dev/null || true
          nap_pid=""
          [ "$rc" -gt 128 ] && woken=1
        }

        # State-file contract: one mode word plus a newline, rewritten every round
        # (so its mtime only proves the supervisor is alive); `proxy-status` reads it.
        write_state() {
          read -r monotonic _ < /proc/uptime
          version=$(${pkgs.jq}/bin/jq -r '.policy_version // .version // -1' /run/proxy-mode/state.json 2>/dev/null || echo -1)
          ${pkgs.jq}/bin/jq -n --arg mode "$1" --argjson now "$monotonic" --argjson version "$version" \
            '{mode:$mode,policy_version:$version,monotonic_seconds:$now}' > /run/gost-relay/state.json.$$
          mv -f /run/gost-relay/state.json.$$ /run/gost-relay/state.json
          printf '%s\n' "$1" > /run/gost-relay/status.$$
          mv -f /run/gost-relay/status.$$ /run/gost-relay/status
        }

        # Identity, not reachability: an impostor can bind :${toString port.mihomoMixed} but cannot land in
        # clash-verge's cgroup, which the kernel reports through ss(8).
        core_up() {
          ${config.my.proxy.listenerCheck} core-mixed >/dev/null 2>&1
        }

        # Observe the sockets with ss(8): never connect() to the redirect port -
        # one connection starts the redirect self-loop. Exactly one listener per
        # port: a leftover child from an earlier start shares the ports through
        # SO_REUSEPORT and does not serve the same way, so count, never merely
        # grep. Empty ss output means "cannot inspect": treat as failure.
        listen_ok() {
          ${config.my.proxy.listenerCheck} relay >/dev/null 2>&1
        }

        # A direct connect() to the redirect port makes gost re-enter its own
        # listener and dial itself until the fd limit. The signature is an
        # established socket whose peer is the redirect listener; a legitimate
        # redirected flow's peer is the real destination. Count them so the loop
        # can restart instead of wedging the unit.
        selfloop_count() {
          ${pkgs.iproute2}/bin/ss -tnH 2>/dev/null \
            | ${pkgs.gawk}/bin/awk -v p4="127.0.0.1:${toString port.gostRedirect}" -v p6="[::1]:${toString port.gostRedirect}" \
                '$1 == "ESTAB" && ($5 == p4 || $5 == p6) { n++ } END { print n + 0 }'
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
              echo "gost-relay: pid $proxy_pid ignored SIGTERM; sending SIGKILL" >&2
              kill -9 "$proxy_pid" 2>/dev/null
            fi
            wait "$proxy_pid" 2>/dev/null
            proxy_pid=""
          fi
        }

        start_gost() {
          # Reap the previous child first: this function is only reached when the
          # mode actually changed, and without this the old process keeps its
          # SO_REUSEPORT sockets and serves a share of the traffic forever.
          stop_current
          # proxy: only forward to mihomo, never dialing a target itself. direct:
          # passthrough, which is what "Clash is off" means.
          if [ "$1" = "proxy" ]; then
            "${gost}/bin/gost" \
                "-L=http://127.0.0.1:${toString port.gostHttp}" \
                "-L=redirect://127.0.0.1:${toString port.gostRedirect}" \
                "-L=redirect://[::1]:${toString port.gostRedirect}" \
                -F=http://127.0.0.1:${toString port.mihomoMixed} &
          else
            "${gost}/bin/gost" \
                "-L=http://127.0.0.1:${toString port.gostHttp}" \
                "-L=redirect://127.0.0.1:${toString port.gostRedirect}" \
                "-L=redirect://[::1]:${toString port.gostRedirect}" &
          fi
          new_pid=$!

          sleep 1
          if ! kill -0 "$new_pid" 2>/dev/null; then
            # Stay as we are instead of lying: reap the dead child, keep the state.
            echo "gost-relay: gost failed to start in [$1]" >&2
            ${pkgs.systemd}/bin/systemd-cat -t gost-relay -p err ${pkgs.coreutils}/bin/echo \
              "gost-relay: gost failed to start in [$1]" || true
            wait "$new_pid" 2>/dev/null
            return 1
          fi

          proxy_pid="$new_pid"
          echo "gost-relay status -> $1 (pid $new_pid)"

          # Do not record a mode with nothing serving it: give the child one more
          # second to bind, then fail so the caller records `closed`.
          if ! listen_ok; then sleep 1; fi
          if ! listen_ok; then
            echo "gost-relay: status=$1 but 127.0.0.1:${toString port.gostHttp}/${toString port.gostRedirect} are not exactly one listener each" >&2
            ${pkgs.systemd}/bin/systemd-cat -t gost-relay -p warning ${pkgs.coreutils}/bin/echo \
              "gost-relay: status=$1 without exactly one listener per port" || true
            stop_current
            return 1
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
          woken=0
          # A crash after the 1s start check would leave both ports unbound while
          # the state file still claims a mode. Heal it first, back to unknown.
          if [ -n "$proxy_pid" ] && ! kill -0 "$proxy_pid" 2>/dev/null; then
            echo "gost-relay: instance pid $proxy_pid is gone; restarting" >&2
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
                echo "gost-relay: pid $proxy_pid is alive but 127.0.0.1:${toString port.gostHttp}/${toString port.gostRedirect} are not listening; restarting" >&2
                listen_fail=0
                stop_current
                mode="closed"
              fi
            fi
            # One stray connect() to the redirect port can spawn hundreds of
            # self-connected sockets; restart before it reaches the fd limit.
            if [ -n "$proxy_pid" ]; then
              nloop=$(selfloop_count)
              if [ "$nloop" -ge 128 ]; then
                echo "gost-relay: redirect self-loop detected ($nloop sockets on :${toString port.gostRedirect}); restarting" >&2
                stop_current
                mode="closed"
              fi
            fi
          fi

          # Only an explicit `direct` status is a passthrough. Every other value
          # -- including `unenforced` and an unreadable/absent file -- means
          # enforcement is missing, so offer nothing rather than a bare relay.
          status="$(${pkgs.jq}/bin/jq -r '.mode // "unknown"' /run/proxy-mode/state.json 2>/dev/null || true)"
          want="closed"
          [ "$status" = "direct" ] && want="direct"

          core_e2e=0
          if [ "$status" = "proxy" ]; then
            # proxy-mode already checked the selected node and TUN before
            # publishing proxy. Do not impose a second site's warm-up outage.
            if core_up; then
              core_e2e=1; want="proxy"
            fi
          fi

          if [ "$want" != "$mode" ]; then
            case "$want" in
              proxy)
                if start_gost proxy; then mode="proxy"; else mode="closed"; fi
                ;;
              direct)
                # Passthrough from the first round: nothing points at a core when
                # Clash is off, so env-proxy consumers must not be left refused.
                if start_gost direct; then
                  mode="direct"
                  echo "gost-relay: Clash is off; passthrough relay on 127.0.0.1:${toString port.gostHttp}" >&2
                else
                  mode="closed"
                fi
                ;;
              *)
                if [ "$mode" = "proxy" ]; then
                  echo "gost-relay: core gone (or not clash-verge's); closing" >&2
                fi
                stop_current
                mode="closed"
                ;;
            esac
          fi

          # A verified-core relay or direct passthrough may back off. Closed
          # and undecided states continue retrying quickly.
          changed=1
          if [ "$mode" = "$want" ]; then
            case "$mode" in
              proxy) [ "$core_e2e" = 1 ] && changed=0 ;;
              direct) changed=0 ;;
            esac
          fi

          write_state "$mode"
          if [ "$changed" = 1 ]; then
            interval=$MIN
          elif [ "$interval" -lt "$MAX" ]; then
            interval=$(( interval * 2 > MAX ? MAX : interval * 2 ))
          fi
          # An event wake while a switch is still in flight must not wait a whole
          # interval for the second confirmation: re-evaluate at once (the woken
          # flag is pre-cleared so this repeats at most once).
          if [ "$woken" = 1 ]; then
            woken=0
            continue
          fi
          nap "$interval"
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

