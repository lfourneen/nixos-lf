{ pkgs, ... }:

let
  python = pkgs.python3.withPackages (p: [ p.pyyaml ]);

in
{
  systemd.user.services.clash-profile-refresh-notify = {
    Unit.Description = "Remind about rejected or stale local subscription updates";
    Service = {
      Type = "oneshot";
      ExecStart = "${python}/bin/python3 ${./refresh_notify.py} ${../../../system/config/network/proxy/state.py} ${pkgs.libnotify}/bin/notify-send";
      UMask = "0077";
      NoNewPrivileges = true;
    };
  };

  systemd.user.timers.clash-profile-refresh-notify = {
    Unit.Description = "Check local subscription health every five minutes";
    Timer = {
      OnStartupSec = "5m";
      OnUnitActiveSec = "5m";
      AccuracySec = "10s";
    };
    Install.WantedBy = [ "timers.target" ];
  };
}
