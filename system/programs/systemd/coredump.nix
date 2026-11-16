{ ... }:

{
  # No dumped memory is stored. The journal still records the crash event;
  # existing dump files are only removed by an explicit operator action.
  systemd.coredump.settings.Coredump = {
    Storage = "none";
    ProcessSizeMax = "0";
  };
  systemd.settings.Manager.DefaultLimitCORE = "0";
  systemd.user.extraConfig = "DefaultLimitCORE=0";
}
