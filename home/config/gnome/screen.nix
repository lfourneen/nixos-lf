{ lib, ... }:

let
  mkUint = lib.hm.gvariant.mkUint32;
in
{
  dconf.settings = {
    # Blank screen after 1 hour of idle
    "org/gnome/desktop/session" = {
      idle-delay = mkUint 3600;  # 3600 seconds = 1 hour, 0 = never
    };

    # Power management plugin
    "org/gnome/settings-daemon/plugins/power" = {
      # Disable automatic suspend
      sleep-inactive-ac-timeout     = mkUint 0;
      sleep-inactive-ac-type        = "nothing";

      sleep-inactive-battery-timeout = mkUint 0;
      sleep-inactive-battery-type    = "nothing";

      # On lid close, only blank screen, do not suspend
      lid-close-ac-action      = "blank";
      lid-close-battery-action = "blank";

      # Dim screen before blanking
      idle-dim = true;
    };

    # Screensaver
    "org/gnome/desktop/screensaver" = {
      lock-enabled = true;
      lock-delay = mkUint 60;
      idle-activation-enabled = true;
    };
  };
}

