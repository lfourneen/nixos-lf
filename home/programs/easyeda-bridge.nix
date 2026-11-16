{ pkgs, lib, osConfig, ... }:

# EasyEDA API Gateway Bridge Server (127.0.0.1:49620-49629). Run as an
# always-on service so the extension finds it regardless of OpenCode's start
# order; bridge-server.mjs is a singleton the MCP later adopts.
let
  proxy = import ../../system/config/network/proxy/env.nix { inherit lib; };
  home = osConfig.users.users.${osConfig.my.machine.desktopUser}.home;
  skillDir = "${home}/.config/opencode/skill/easyeda-api";
  bridgeServer = "${skillDir}/scripts/bridge-server.mjs";
  loopbackNoProxy = proxy.join proxy.loopback;

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
        "NO_PROXY=${loopbackNoProxy}"
        "no_proxy=${loopbackNoProxy}"
      ];
    };
    Install.WantedBy = [ "default.target" ];
  };
}

