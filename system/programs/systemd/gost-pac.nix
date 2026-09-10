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
      
      # Internal implementation of persistent loop monitoring and hot-reloading
      ExecStart = "${pkgs.writeShellScript "gost-launcher" ''
        current_status="none"
        proxy_pid=""
        good=0
        bad=0

        stop_gost() {
          if [ -n "$proxy_pid" ]; then
            kill "$proxy_pid" 2>/dev/null
            wait "$proxy_pid" 2>/dev/null
            # Wait for port 33332 to be released (bounded)
            i=0
            while ${pkgs.iproute2}/bin/ss -tuln | ${pkgs.gnugrep}/bin/grep -q ":33332 "; do
              i=$((i+1))
              [ "$i" -ge 10 ] && break
              sleep 0.2
            done
            proxy_pid=""
          fi
        }

        start_gost() {
          stop_gost
          if [ "$1" = "proxy" ]; then
            # Clash online: chain to the core at 7897
            env http_proxy= https_proxy= all_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= \
              ${pkgs.gost}/bin/gost -L=http://127.0.0.1:33332 -F=http://127.0.0.1:7897 &
          else
            # Clash offline: standalone direct proxy (fail-open)
            env http_proxy= https_proxy= all_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= \
              ${pkgs.gost}/bin/gost -L=http://127.0.0.1:33332 &
          fi
          proxy_pid=$!
          current_status="$1"
          echo "gost-pac status -> $1 (pid $proxy_pid)"
        }

        cleanup() {
          echo "Stopping proxy supervisor..."
          stop_gost
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

