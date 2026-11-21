{ config, lib, pkgs, ... }:

let
  # Upstream tsconst.LinuxBypassMarkNum, not a locally allocated routing mark.
  # Keep it read-only and fail normal system builds if the upstream contract
  # changes. Network changes and ephemeral peer ports do not change SO_MARK.
  bypassMark = 524288;

  transportCheck = pkgs.runCommand "tailscale-transport-contract" {
    nativeBuildInputs = [ pkgs.python3 ];
  } ''
    python3 ${./tailscale_contract.py} \
      ${config.services.tailscale.package.src} ${toString bypassMark}
    touch "$out"
  '';

in
{
  options.my.hardening.tailscale = {
    ingressPort = lib.mkOption {
      type = lib.types.nullOr lib.types.port;
      internal = true;
      readOnly = true;
      default = if config.services.tailscale.enable && config.services.tailscale.port != 0
        then config.services.tailscale.port else null;
      description = "Fixed magicsock UDP port eligible for physical ingress and reserved from local allocation.";
    };

    transportMark = lib.mkOption {
      type = lib.types.int;
      internal = true;
      readOnly = true;
      default = bypassMark;
      description = "Upstream LinuxBypassMarkNum; verified against the selected Tailscale source during system builds.";
    };

    transportCheck = lib.mkOption {
      type = lib.types.package;
      internal = true;
      readOnly = true;
      default = transportCheck;
      description = "Build-time check of the Tailscale UDP socket-mark contract.";
    };
  };

  config.system.checks = lib.optional config.services.tailscale.enable transportCheck;
}
