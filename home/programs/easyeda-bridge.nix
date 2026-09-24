{ pkgs, ... }:

# EasyEDA API Gateway Bridge Server (`easyeda-bridge`, 127.0.0.1:49620-49629).
#
# The Run API Gateway extension inside EasyEDA scans the port range only 5
# times (3 s apart) at extension load, then permanently gives up. The MCP
# server (jlceda) auto-spawns this bridge, but only once OpenCode starts, which
# is often well after EasyEDA is already open — so the extension misses it and
# shows "连接中 / 未找到 Bridge 服务".
#
# Running the bridge as an always-on user service removes that startup-order
# race. bridge-server.mjs is a singleton: the copy the MCP tries to spawn sees
# this instance already on the port range and exits, after which the MCP adopts
# it.
#
# The bridge lives in the easyeda-api skill (the canonical upstream copy), so
# no dependency on the mutable jlcmcp checkout. The same skill ships a portable
# installer: scripts/install-service.sh / scripts/uninstall-service.sh.
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

      # on-failure (not always): when another bridge already holds the port,
      # this process detects the singleton and exits 0, so it must not be
      # respawned into a loop.
      Restart = "on-failure";
      RestartSec = 3;
      
      # The host session exports a transparent-proxy setup for external
      # traffic; the bridge only ever listens on loopback, so keep local
      # connections off the proxy explicitly.
      Environment = [
        "NO_PROXY=127.0.0.1,localhost,::1"
        "no_proxy=127.0.0.1,localhost,::1"
      ];
    };
    Install.WantedBy = [ "default.target" ];
  };
}

