{ pkgs, ... }:

{
  # Alert channel for the units whose failure means the enforcement is not what the
  # status files claim. It only reports; repair stays a separate opt-in unit. Used
  # as OnFailure by nftables.service, proxy-mode.service and nftables-verify.service.
  #
  # `%i` is only expanded by systemd on the ExecStart command line, not inside a
  # `script` file (NixOS writes `script` to a standalone script), so the instance
  # is passed as an argument and read back as `$1`.
  systemd.services."netsec-alert@" = {
    description = "Alert that %i failed and network enforcement may be off";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.writeShellScript "netsec-alert" ''
        set -eu
        unit="$1"
        ${pkgs.coreutils}/bin/mkdir -p /run/netsec
        printf '%s\n' "$unit" > /run/netsec/failed
        ${pkgs.systemd}/bin/systemd-cat -t netsec-alert -p err ${pkgs.coreutils}/bin/echo \
          "netsec-alert: unit $unit failed -- network enforcement may be off (journalctl -u $unit)"
        ${pkgs.util-linux}/bin/wall "NETSEC ALERT: $unit failed -- the network may be unenforced (journalctl -u netsec-alert)" || true
      ''} %i";
    };
  };
}

