{ pkgs, ... }:

{
  systemd.user.services.wallpaper = {
    Unit = {
      Description = "wallpaper for niri(linux-wallpaperengine)";
      After = [ "graphical-session.target" ];
      PartOf = [ "graphical-session.target" ];
    };

    Service = {
      Type = "simple";
      PassEnvironment = [
        "WAYLAND_DISPLAY"
        "XDG_RUNTIME_DIR"
      ];

    ExecStart = ''
      ${pkgs.linux-wallpaperengine}/bin/linux-wallpaperengine \
        --screen-root HDMI-A-1 \
        --bg xxxxxxxxxx \
        --screen-root eDP-1 \
        --bg xxxxxxxxxx \
        --fps 144 \
        --scaling fit \
        --clamp border \
        --no-fullscreen-pause
    '';

    #Restart = "always";
    #RestartSec = 2;
    Restart = "no";
    };

    Install.WantedBy = [ "graphical-session.target" ];
  };
}

