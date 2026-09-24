{ pkgs, ... }:

# EasyEDA API Gateway Bridge Server (127.0.0.1:49620-49629). Run as an
# always-on service so the extension finds it regardless of OpenCode's start
# order; bridge-server.mjs is a singleton the MCP later adopts.
let
  skillDir = "/home/lfour/.config/opencode/skill/easyeda-api";
  bridgeServer = "${skillDir}/scripts/bridge-server.mjs";

in
{
  systemd.user.services.easyeda-bridge = {
    Unit = {
      Description = "EasyEDA API Gateway Bridge Server (easyeda-bridge)";
      PartOf = [ "default.target" ];
      After = [ "default.target" ];
    };

    Service = {
      ExecStart = "${pkgs.nodejs}/bin/node ${bridgeServer}";
      WorkingDirectory = skillDir;

      # on-failure: a competing bridge exits 0 and must not be respawned.
      Restart = "on-failure";
      RestartSec = 3;
      
      # The bridge is loopback-only; keep it off the transparent proxy.
      Environment = [
        "NO_PROXY=127.0.0.1,localhost,::1"
        "no_proxy=127.0.0.1,localhost,::1"
      ];
    };
    Install.WantedBy = [ "default.target" ];
  };
}

