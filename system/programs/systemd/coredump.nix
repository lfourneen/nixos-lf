{ pkgs, ... }:

{
  # coredump: 16G cap, oldest purged on overflow; weekly 7-day purge
  systemd.coredump.settings.Coredump = {
    MaxUse = "16G";
    MaxAge = "7d";
  };

  systemd.services.coredump-clean = {
    description = "Delete coredumps older than 7 days";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.findutils}/bin/find /var/lib/systemd/coredump -type f -mtime +7 -delete";
    };
  };

  systemd.timers.coredump-clean = {
    description = "Weekly coredump cleanup";
    timerConfig = {
      OnCalendar = "weekly";
      Persistent = true;
    };
    wantedBy = [ "timers.target" ];
  };
}
