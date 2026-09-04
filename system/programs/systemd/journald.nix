{ pkgs, ... }:

{
  # Persistent journal storage (reboots/rollback survive); bounded to 500M
  services.journald = {
    storage = "persistent";
    extraConfig = ''
      SystemMaxUse=512M
    '';
  };

  systemd.services.journald-clean = {
    description = "Vacuum journal older than 7 days";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.systemd}/bin/journalctl --vacuum-time=7d";
    };
  };

  systemd.timers.journald-clean = {
    description = "Weekly journal vacuum";
    timerConfig = {
      OnCalendar = "weekly";
      Persistent = true;
    };
    wantedBy = [ "timers.target" ];
  };
}

