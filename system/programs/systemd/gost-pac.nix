{ pkgs, ... }:

{
  # Network PAC (Fail-Open Architecture with Gost)
  systemd.services.gost-pac = {
    description = "Gost PAC High-Availability Proxy Daemon";
    after = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    # Never stop retrying (else a crash-loop leaves proxied apps without internet)
    unitConfig.StartLimitIntervalSec = 0;
    serviceConfig = {
      User = "lfour";
      Group = "users";
      StateDirectory = "gost";
      RuntimeDirectory = "gost-pac";
      
      # Internal implementation of persistent loop monitoring and hot-reloading
      ExecStart = "${pkgs.writeShellScript "gost-launcher" ''
        current_status="none"
        proxy_pid=""
        good=0
        bad=0

        stop_current() {
          if [ -n "$proxy_pid" ]; then
            kill "$proxy_pid" 2>/dev/null
            wait "$proxy_pid" 2>/dev/null
            proxy_pid=""
          fi
        }

        start_gost() {
          mode="$1"
          # SO_REUSEPORT: new instance shares :33332 with the old one, so a
          # mode flip never refuses a connection.
          if [ "$mode" = "proxy" ]; then
            # Clash online: chain to the core at 7897
            env http_proxy= https_proxy= all_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= \
              "${pkgs.gost}/bin/gost" "-L=http://127.0.0.1:33332?reuseport=true" -F=http://127.0.0.1:7897 &
          else
            # Clash offline: standalone direct proxy (fail-open)
            env http_proxy= https_proxy= all_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= \
              "${pkgs.gost}/bin/gost" "-L=http://127.0.0.1:33332?reuseport=true" &
          fi
          new_pid=$!

          sleep 1
          if ! kill -0 "$new_pid" 2>/dev/null; then
            echo "gost failed to start in [$mode]; keeping current instance"
            return 1
          fi

          old_pid="$proxy_pid"
          proxy_pid="$new_pid"
          current_status="$mode"
          # State for proxy-status.
          printf '%s\n' "$mode" > /run/gost-pac/status
          echo "gost-pac status -> $mode (pid $new_pid)"

          if [ -n "$old_pid" ]; then
            # Drain in-flight connections, then retire the old instance.
            sleep 2
            kill "$old_pid" 2>/dev/null
            wait "$old_pid" 2>/dev/null
          fi
        }

        cleanup() {
          echo "Stopping proxy supervisor..."
          stop_current
          exit 0
        }
        trap cleanup TERM INT

        while true; do
          # Core port must answer AND a proxied request must actually work.
          if ${pkgs.coreutils}/bin/timeout 2 ${pkgs.bash}/bin/bash -c 'echo > /dev/tcp/127.0.0.1/7897' 2>/dev/null; then
            core_up=1
          else
            core_up=0
          fi

          if [ "$core_up" = 1 ] && ${pkgs.curl}/bin/curl -x http://127.0.0.1:7897 --max-time 4 -fsS https://www.google.com/generate_204 -o /dev/null 2>/dev/null; then
            good=$((good+1))
            bad=0
          else
            bad=$((bad+1))
            good=0
          fi

          new_status="$current_status"
          if [ "$current_status" = "none" ]; then
            # First tick: start now, fail-open to direct unless proxy is proven
            if [ "$core_up" = 1 ] && [ "$bad" = 0 ]; then new_status="proxy"; else new_status="direct"; fi
          elif [ "$core_up" = 0 ]; then
            # Core gone: fall back immediately
            new_status="direct"
          elif [ "$good" -ge 2 ]; then
            new_status="proxy"
          elif [ "$bad" -ge 2 ]; then
            new_status="direct"
          fi

          if [ "$new_status" != "$current_status" ]; then
            start_gost "$new_status"
            good=0
            bad=0
          fi

          sleep 5
        done
      ''
      }";

      # Sandbox the supervisor process environment
      Environment = [
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

