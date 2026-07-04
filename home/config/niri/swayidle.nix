{ pkgs, ... }: 

{
  systemd.user.services.swayidle = {
    Unit = {
      Description = "Idle management daemon for niri";
      PartOf = [ "graphical-session.target" ];
      After  = [ "graphical-session.target" ];
      BindsTo = [ "graphical-session.target" ];
    };

    Service = {
      Restart = "always";
      ExecStart = "${pkgs.swayidle}/bin/swayidle -w";
    };

    Install = {
      WantedBy = [ "graphical-session.target" ];
    };
  };

  xdg.configFile."swayidle/config".text = ''
    # --- Hour (3600s) Idle Logic ---
    
    # Trigger Noctalia native lock screen
    # This uses the IPC call to invoke Noctalia's built-in lock UI
    timeout 3600 "noctalia-shell ipc call lockScreen lock"
    
    # Turn off monitors via Niri
    # The 'resume' command ensures monitors wake up when activity is detected
    timeout 3600 "${pkgs.niri}/bin/niri msg action power-off-monitors" resume "${pkgs.niri}/bin/niri msg action power-off-monitors false"

    # --- System Hooks ---
    
    # Lock the screen before the system goes to sleep (if manually triggered)
    before-sleep "noctalia-shell ipc call lockScreen lock"
    
    # Ensure Niri turns monitors back on during resume to avoid a black screen
    before-sleep "${pkgs.niri}/bin/niri msg action power-off-monitors false"
  '';
}

