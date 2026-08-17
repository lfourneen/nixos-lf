{ pkgs, ... }:

{
  # Network PAC (Fail-Open Architecture with Gost)
  systemd.services.gost-pac = {
    description = "Gost PAC High-Availability Proxy Daemon";
    after = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      User = "lfour";
      Group = "users";
      StateDirectory = "gost";
      
      # Internal implementation of persistent loop monitoring and hot-reloading
      ExecStart = "${pkgs.writeShellScript "gost-launcher" ''
        current_status="none"
        proxy_pid=""

        # Cleanup handler to ensure child processes are terminated on service stop
        cleanup() {
          echo "Stopping proxy supervisor..."
          if [ -n "$proxy_pid" ]; then
            kill "$proxy_pid"
          fi
          exit 0
        }
        trap cleanup TERM INT

        # Infinite keepalive and health-check loop
        while true; do
          # Use --noproxy "*" to bypass global env variables and avoid Nftables/Zapret interference during probing
          if ${pkgs.curl}/bin/curl -sf -m 2 --noproxy "*" http://127.0.0.1:33331/commands/pac > /dev/null; then
            new_status="proxy"
          else
            new_status="direct"
          fi

          # Trigger hot-reload only when backend state changes
          if [ "$new_status" != "$current_status" ]; then
            echo "Proxy status changed from [$current_status] to [$new_status]. Reloading..."
            
            # Terminate the active gost instance cleanly before spawning a new one
            if [ -n "$proxy_pid" ]; then
              kill "$proxy_pid"
              wait "$proxy_pid" 2>/dev/null

              # Wait for port 33332 to be released (bounded to avoid infinite loop)
              i=0
              while ${pkgs.iproute2}/bin/ss -tuln | grep -q ":33332 "; do
                i=$((i+1))
                [ "$i" -ge 10 ] && break
                sleep 0.2
              done
            fi

            # Explicitly strip proxy env variables prior to execution to prevent infinite loop regressions
            if [ "$new_status" = "proxy" ]; then
              # Clash Online: Listen on 33332 and forward traffic to Clash core at 7897
              env http_proxy= https_proxy= all_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= \
              ${pkgs.gost}/bin/gost -L=http://127.0.0.1:33332 -F=http://127.0.0.1:7897 &
            else
              # Clash Offline: Listen on 33332 and act as a standalone HTTP proxy for direct fallback
              env http_proxy= https_proxy= all_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= \
              ${pkgs.gost}/bin/gost -L=http://127.0.0.1:33332 &
            fi
            
            proxy_pid=$!
            current_status="$new_status"
          fi

          # Health check interval (seconds)
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
      ];
      Restart = "always";
      RestartSec = "5";
    };
  };
}
