{ pkgs, lib, osConfig, ... }:

let
  # gost's HTTP listener; machine.nix owns the port.
  gostHttp = toString osConfig.my.machine.ports.gostHttp;

  # Define the Niri config as raw KDL text
  niriConfigContent = ''
    // niri config

    // Autostart
    spawn-at-startup "noctalia"

    // General Settings
    prefer-no-csd
    screenshot-path "~/Pictures/Screenshots/%Y-%m-%d-%H-%M-%S.jpg"

    // Environment Variables
    environment {
      CLUTTER_BACKEND "wayland"
      GDK_BACKEND "wayland"
      MOZ_ENABLE_WAYLAND "1"
      NIXOS_OZONE_WL "1"

      // XWayland fallback for Qt5; no KDE platform theme/services in niri.
      QT_QPA_PLATFORM "wayland"
      QT_QPA_PLATFORMTHEME ""
      QT_WAYLAND_DISABLE_WINDOWDECORATION "1"
      
      ELECTRON_OZONE_PLATFORM_HINT "wayland"
      _JAVA_AWT_WM_NONREPARENTING "1"

      XDG_SESSION_TYPE "wayland"
      XDG_CURRENT_DESKTOP "niri"
      // DISPLAY is managed automatically by niri's xwayland-satellite
      // integration (niri creates the X11 socket and sets DISPLAY). Do not
      // hardcode ":0" here: it would override niri's value and break X apps.

      // Proxy
      http_proxy "http://127.0.0.1:${gostHttp}"
      https_proxy "http://127.0.0.1:${gostHttp}"
      all_proxy "http://127.0.0.1:${gostHttp}"
    }

    input {
      keyboard {
        xkb {
          layout "us"
        }
        numlock
      }

      touchpad {
        tap
        natural-scroll
      }

      mouse {

      }

      trackpoint {

      }
    }

    cursor {
      hide-when-typing
      hide-after-inactive-ms 5000
      xcursor-theme "Iochi Mari (Gym ver.)"
      xcursor-size 48
    }

    // Overview appearance
    overview {
      backdrop-color "#00000000"   // transparent so wallpaper shows through
      workspace-shadow {
        off
      }
    }

    // Wallpaper layer (noctalia) — stationary wallpaper drawn in the backdrop.
    // opacity 0 keeps Noctalia's wallpaper instance alive (Home-tab thumbnail and
    // palette) while making its layer invisible, so waywallen's live wallpaper
    // shows through regardless of background-layer stacking order.
    layer-rule {
      match namespace="^noctalia-wallpaper*"
      place-within-backdrop true
      opacity 0.0
    }

    // Linux Wallpaper Engine (w-engine plugin → linux-wallpaperengine): draw the
    // live wallpaper in the backdrop so the overview shows it once at full size
    // instead of cloning it into every workspace capsule. Requires the plugin's
    // engine setting "Wayland layer: background".
    layer-rule {
      match namespace="linux-wallpaperengine"
      place-within-backdrop true
    }

    // Video wallpaper via mpvpaper: same treatment, otherwise the overview
    // clones the video into every workspace capsule and the backdrop stays black.
    layer-rule {
      match namespace="mpvpaper"
      place-within-backdrop true
    }

    // waywallen layer-shell wallpaper: same treatment as the others, otherwise
    // the overview clones it into every workspace capsule instead of showing the
    // single backdrop wallpaper.
    layer-rule {
      match namespace="waywallen-wallpaper"
      place-within-backdrop true
    }

    // Monitors
    // Left（1080p@100Hz）
    output "HDMI-A-1" {
      mode "1920x1080@99.999001"
      scale 1
      transform "normal"
      position x=-1920 y=0
    }

    // Right（1080p@144Hz）
    output "eDP-1" {
      mode "1920x1080@144.001007"
      scale 1
      transform "normal"
      position x=0 y=0
    }

    layout {
      gaps 8
      center-focused-column "on-overflow"
      background-color "#00000000"  // transparent → noctalia wallpaper becomes background
      preset-column-widths {
        proportion 0.33333
        proportion 0.5
        proportion 0.66667
        fixed 1920
      }
      default-column-width { proportion 0.5; }

      // Focus ring
      focus-ring {
        width 1
        active-color "#8CBD8C"    // Change windows border (active)
        inactive-color "#0A0E14"
      }

      border {
        off
        width 1
        inactive-color "#0A0E14"
        urgent-color "#9b0000"
      }

      // Window shadow
      shadow {
        softness 30
        spread 5
        offset x=0 y=5
        color "#0007"
      }

      struts {
        left 5
        right 5
        top 5
        bottom 5
      }
    }

    hotkey-overlay {
      skip-at-startup
    }

    animations {
      workspace-switch {
        spring damping-ratio=1.0 stiffness=1000 epsilon=0.0001
      }
      window-open {
        duration-ms 200
        curve "ease-out-quad"
      }
      window-close {
        duration-ms 200
        curve "ease-out-cubic"
      }
      horizontal-view-movement {
        spring damping-ratio=1.0 stiffness=900 epsilon=0.0001
      }
      window-movement {
        spring damping-ratio=1.0 stiffness=800 epsilon=0.0001
      }
      window-resize {
        spring damping-ratio=1.0 stiffness=1000 epsilon=0.0001
      }
      config-notification-open-close {
        spring damping-ratio=0.6 stiffness=1200 epsilon=0.001
      }
      screenshot-ui-open {
        duration-ms 300
        curve "ease-out-quad"
      }
      overview-open-close {
        spring damping-ratio=1.0 stiffness=900 epsilon=0.0001
      }
    }

    // Window Rules
    // Application-specific rules
    window-rule {
      match app-id=r#"firefox$"# title="^Picture-in-Picture$"
      open-floating true
    }

    window-rule {
      match app-id=r#"code"#
      open-maximized true
    }

    window-rule {
      match app-id=r#"firefox$"#
      open-maximized true
    }

    // Firefox login/OAuth pop-ups, including Google, GitHub and Microsoft.
    // Override the main Firefox rule above; match English and Chinese titles.
    window-rule {
      match app-id=r#"firefox$"# title=r#"(?i)^(sign[ -]?in\b|log[ -]?in\b|登录|登入|登錄)"#
      open-floating true
      open-maximized false
    }

    window-rule {
      match app-id=r#"obsidian$"#
      open-maximized true
    }

    window-rule {
      match app-id=r#"protonvpn-app"#
      open-floating true
      min-width 400
      min-height 600
    }

    // File dialogs - Open/Save/Select
    // Title regexes must be ANCHORED. An unanchored `.*File.*` / `.*Open.*`
    // also matches browser tab titles (a window's title is the page title),
    // and then max-width 800 clamps the whole browser window to ~half screen
    // and overrides Mod+F. See niri issue #3779.
    window-rule {
      match title=r#"^(Open|Save|Select)( a)?( File| Folder| Files| As)?$"#
      open-floating true
      default-column-width { proportion 0.0; }
      max-width 800
      max-height 1000
    }

    window-rule {
      match app-id=r#"org\.gtk\.FileChooserDialog"#
      open-floating true
      default-column-width { proportion 0.0; }
      max-width 800
      max-height 1000
    }

    // GNOME applications: keep these utility windows floating. Keep them in
    // one app-id alternation instead of a separate window-rule per app.
    window-rule {
      match app-id=r#"^(org\.gnome\.Console|org\.gnome\.Loupe|org\.gnome\.Calendar|org\.gnome\.calendar|org\.gnome\.Calculator|org\.gnome\.Nautilus|org\.gnome\.TextEditor)$"#
      open-floating true
    }

    // Common KDE applications: same floating treatment as the GNOME apps
    // above. Keep them in one app-id alternation so the list stays compact.
    window-rule {
      match app-id=r#"^(org\.kde\.dolphin|org\.kde\.konsole|org\.kde\.systemsettings|systemsettings|org\.kde\.discover|org\.kde\.spectacle|org\.kde\.gwenview|org\.kde\.okular|org\.kde\.ark|org\.kde\.kate|org\.kde\.kwrite|org\.kde\.kinfocenter|org\.kde\.plasma-systemmonitor|org\.kde\.kwalletmanager|org\.kde\.khelpcenter|org\.kde\.kmenuedit|org\.kde\.kfontview|org\.kde\.kcolorschemeeditor|org\.kde\.kcalc|org\.kde\.kdeconnect\.app|org\.kde\.filelight|org\.kde\.partitionmanager|org\.kde\.ksystemlog|org\.kde\.skanpage|org\.kde\.kamoso|org\.kde\.plasma\.emojier|org\.kde\.qrca|org\.kde\.bluedevilwizard|org\.kde\.knetattach|org\.kde\.PrintQueue|org\.kde\.ConfigurePrinter|org\.kde\.drkonqi|org\.kde\.drkonqi\.coredump\.gui)$"#
      open-floating true
    }

    // System dialogs
    window-rule {
      match title=r#".*(Dialog|Properties|Preferences|Settings|Rename).*"#
      open-floating true
    }

    window-rule {
      match app-id=r#"zenity"#
      open-floating true
    }

    // Authentication dialogs
    window-rule {
      match app-id=r#"org\.kde\.polkit-kde-authentication-agent-1"#
      open-floating true
    }

    window-rule {
      match title=r#".*Authentication.*"#
      open-floating true
    }

    // Password managers
    window-rule {
      match app-id=r#"org\.keepassxc\.KeePassXC"# title=r#".*Auto-Type.*"#
      open-floating true
    }

    window-rule {
      match app-id=r#"Bitwarden"# title=r#".*unlock.*"#
      open-floating true
    }

    // Notification and system utilities
    window-rule {
      match app-id=r#"nm-connection-editor"#
      open-floating true
    }

    window-rule {
      match app-id=r#"blueman-manager"#
      open-floating true
    }

    window-rule {
      match app-id=r#"pavucontrol"#
      open-floating true
    }

    // Steam client
    window-rule {
      match app-id=r#"^steam$"#
      open-floating true
    }

    // Steam proton software
    window-rule {
      match app-id=r#"^steam_proton$"#
      open-floating true
    }

    // Electron
    window-rule {
      match app-id=r#"^electron$"#
      open-floating true
    }

    // Imv
    window-rule {
      match app-id=r#"^imv$"#
      open-floating true
    }

    // Bilibili
    window-rule {
      match app-id=r#"^bilibili$"#
      open-floating true
    }

    // QQ
    window-rule {
      match app-id=r#"^QQ$"#
      open-floating true
    }

    // Wechat
    window-rule {
      match app-id=r#"^wechat$"#
      match app-id=r#"^Weixin$"#
      match title=r#"(?i)wechat"#
      match title=r#"(?i)weixin"#
      match title=r#"微信"#
      match title=r#"^Official Accounts$"#
      match title=r#"^Service Accounts$"#
      open-floating true
    }

    // DingTalk
    window-rule {
      match app-id=r#"^com.alibabainc.dingtalk$"#
      match app-id=r#"^Com.alibabainc.dingtalk$"#
      match title=r#"钉钉"#
      match title=r#"保存"#
      open-floating true
    }

    // STM32CubeMX
    window-rule {
      match app-id=r#"^com-st-microxplorer-maingui-STM32CubeMX$"#
      match title=r#"STM32CubeMX Untitled"#
      open-floating true
    }

    // Peazip
    window-rule {
      match app-id=r#"^peazip$"#
      open-floating true
    }

    // Prismlauncher
    window-rule {
      match app-id=r#"^org.prismlauncher.PrismLauncher$"#
      open-floating true
    }

    // Lutris
    window-rule {
      match app-id=r#"^net.lutris.Lutris$"#
      open-floating true
    }

    // Lutris apps
    window-rule {
      match app-id=r#"^steam_app_default$"#
      open-floating true
    }

    // Unknown apps
    window-rule {
      match app-id=r#"^null$"#
      open-floating true
    }

    // Global window appearance
    window-rule {
      geometry-corner-radius 16
      clip-to-geometry true
    }
    window-rule {
      geometry-corner-radius 16
      clip-to-geometry true
      opacity 0.9
      background-effect {
        blur true
      }
    }
    window-rule {
      match is-active=false
      opacity 0.8
      background-effect {
        blur true
      }
    }

    // Keyboard binds
    binds {
      Mod+Shift+Slash { show-hotkey-overlay; }
      Mod+Z hotkey-overlay-title="Open a Terminal: kitty" { spawn "kitty"; }
      Mod+Space hotkey-overlay-title="Run ..." { spawn "noctalia" "msg" "panel-toggle" "launcher"; }
      Super+Alt+L hotkey-overlay-title="Lock the Screen" { spawn "noctalia" "msg" "session" "lock"; }

      Super+Alt+S allow-when-locked=true hotkey-overlay-title=null { spawn-sh "pkill orca || exec orca"; }

      XF86AudioRaiseVolume allow-when-locked=true { spawn-sh "wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.1+"; }
      XF86AudioLowerVolume allow-when-locked=true { spawn-sh "wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.1-"; }
      XF86AudioMute        allow-when-locked=true { spawn-sh "wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"; }
      XF86AudioMicMute     allow-when-locked=true { spawn-sh "wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle  "; }

      XF86MonBrightnessUp allow-when-locked=true { spawn "brightnessctl" "--class=backlight" "set" "+10%";   }
      XF86MonBrightnessDown allow-when-locked=true { spawn "brightnessctl" "--class=backlight" "set" "10%-  "; }

      Mod+A repeat=false { toggle-overview; } 
      Mod+X repeat=false { close-window; }

      Mod+Left  { focus-column-left; }
      Mod+Down  { focus-window-down; }
      Mod+Up    { focus-window-up; }
      Mod+Right { focus-column-right; }
      Mod+Ctrl+Left  { move-column-left; }
      Mod+Ctrl+Down  { move-window-down; }
      Mod+Ctrl+Up    { move-window-up; }
      Mod+Ctrl+Right { move-column-right; }
      Mod+J     { focus-window-or-workspace-down; }
      Mod+K     { focus-window-or-workspace-up; }
      Mod+Ctrl+J     { move-window-down-or-to-workspace-down; }
      Mod+Ctrl+K     { move-window-up-or-to-workspace-up; }
      Mod+Home { focus-column-first; }
      Mod+End  { focus-column-last; }
      Mod+Ctrl+Home { move-column-to-first; }
      Mod+Ctrl+End  { move-column-to-last; }
      Mod+Shift+Left  { focus-monitor-left; }
      Mod+Shift+Down  { focus-monitor-down; }
      Mod+Shift+Up    { focus-monitor-up; }
      Mod+Shift+Right { focus-monitor-right; }
      Mod+Shift+Ctrl+Left  { move-column-to-monitor-left; }
      Mod+Shift+Ctrl+Down  { move-column-to-monitor-down; }
      Mod+Shift+Ctrl+Up    { move-column-to-monitor-up; }
      Mod+Shift+Ctrl+Right { move-column-to-monitor-right; }

      Mod+Page_Down      { focus-workspace-down; }
      Mod+Page_Up        { focus-workspace-up; }
      Mod+U              { focus-workspace-down; }
      Mod+I              { focus-workspace-up; }
      Mod+Ctrl+Page_Down { move-column-to-workspace-down; }
      Mod+Ctrl+Page_Up   { move-column-to-workspace-up; }
      Mod+Ctrl+U         { move-column-to-workspace-down; }
      Mod+Ctrl+I         { move-column-to-workspace-up; }

      Mod+Shift+Page_Down { move-workspace-down; }
      Mod+Shift+Page_Up   { move-workspace-up; }
      Mod+Shift+U         { move-workspace-down; }
      Mod+Shift+I         { move-workspace-up; }

      Mod+WheelScrollDown      cooldown-ms=150 { focus-workspace-down; }
      Mod+WheelScrollUp        cooldown-ms=150 { focus-workspace-up; }
      Mod+Ctrl+WheelScrollDown cooldown-ms=150 { move-column-to-workspace-down; }
      Mod+Ctrl+WheelScrollUp   cooldown-ms=150 { move-column-to-workspace-up; }

      Mod+WheelScrollRight      { focus-column-right; }
      Mod+WheelScrollLeft       { focus-column-left; }
      Mod+Ctrl+WheelScrollRight { move-column-right; }
      Mod+Ctrl+WheelScrollLeft  { move-column-left; }

      Mod+Shift+WheelScrollDown      { focus-column-right; }
      Mod+Shift+WheelScrollUp        { focus-column-left; }
      Mod+Ctrl+Shift+WheelScrollDown { move-column-right; }
      Mod+Ctrl+Shift+WheelScrollUp   { move-column-left; }

      Mod+1 { focus-workspace 1; }
      Mod+2 { focus-workspace 2; }
      Mod+3 { focus-workspace 3; }
      Mod+4 { focus-workspace 4; }
      Mod+5 { focus-workspace 5; }
      Mod+6 { focus-workspace 6; }
      Mod+7 { focus-workspace 7; }
      Mod+8 { focus-workspace 8; }
      Mod+9 { focus-workspace 9; }
      Mod+Ctrl+1 { move-column-to-workspace 1; }
      Mod+Ctrl+2 { move-column-to-workspace 2; }
      Mod+Ctrl+3 { move-column-to-workspace 3; }
      Mod+Ctrl+4 { move-column-to-workspace 4; }
      Mod+Ctrl+5 { move-column-to-workspace 5; }
      Mod+Ctrl+6 { move-column-to-workspace 6; }
      Mod+Ctrl+7 { move-column-to-workspace 7; }
      Mod+Ctrl+8 { move-column-to-workspace 8; }
      Mod+Ctrl+9 { move-column-to-workspace 9; }

      Mod+BracketLeft  { consume-or-expel-window-left; }
      Mod+BracketRight { consume-or-expel-window-right; }

      Mod+Comma  { consume-window-into-column; }
      Mod+Period { expel-window-from-column; }

      Mod+R { switch-preset-column-width; }
      Mod+Shift+R { switch-preset-window-height; }
      Mod+Ctrl+R { reset-window-height; }
      Mod+F { maximize-column; }
      Mod+Shift+F { fullscreen-window; }

      Mod+Ctrl+F { expand-column-to-available-width; }

      Mod+C { center-column; }

      Mod+Ctrl+C { center-visible-columns; }

      Mod+Minus { set-column-width "-10%"; }
      Mod+Equal { set-column-width "+10%"; }
      Mod+Shift+Minus { set-window-height "-10%"; }
      Mod+Shift+Equal { set-window-height "+10%"; }

      Mod+V       { toggle-window-floating; }
      Mod+Shift+V { switch-focus-between-floating-and-tiling; }
      Mod+W { toggle-column-tabbed-display; }

      Print { screenshot; }
      Ctrl+Print { screenshot-screen; }
      Alt+Print { screenshot-window; }

      Mod+Escape allow-inhibiting=false { toggle-keyboard-shortcuts-inhibit; }
      Mod+Shift+E { quit; }
      Ctrl+Alt+Delete { quit; }
      Mod+Shift+P { power-off-monitors; }
    }

    // Noctalia wallpaper-adaptive theme
    include "noctalia.kdl"
  '';

  # waywallen remembers the last wallpaper as a *library item id*
  # (~/.config/waywallen/config.toml `last_wallpaper`). When that item later
  # disappears (its file was renamed or deleted), startup restore aborts with
  # "wallpaper '<id>' not found" and paints nothing -> black desktop; upstream
  # has no fallback. Repair any id that no longer maps to an existing file
  # before the daemon reads its config, so its own restore always has a valid
  # target. Valid ids are left untouched, so the last applied wallpaper is
  # preserved. Wired as the service's ExecStartPre below; upstream v0.5.0 and
  # main still return WallpaperNotFound with no fallback.
  waywallenFallbackWallpaper = "Iochi_Mari.mp4"; # basename to prefer; "" = any video
  waywallenFixRestore = pkgs.writeText "waywallen-fix-restore.py" ''
    import os, re, sqlite3, sys

    home = os.environ.get("HOME", "")
    cfg = os.path.join(home, ".config/waywallen/config.toml")
    db = os.path.join(home, ".local/share/waywallen/waywallen-v2.db")
    preferred = "${waywallenFallbackWallpaper}"

    if not (os.path.isfile(cfg) and os.path.isfile(db)):
        sys.exit(0)

    con = sqlite3.connect(db)
    rows = list(con.execute(
        "SELECT i.id, i.path, l.path, i.type"
        "  FROM item i JOIN library l ON i.library_id = l.id"))
    con.close()

    present = []
    for iid, ipath, lpath, itype in rows:
        if os.path.exists(os.path.join(lpath, ipath)):
            present.append((str(iid), ipath, itype))

    def pick():
        if preferred:
            for iid, ipath, itype in present:
                if itype == "video" and (os.path.basename(ipath) == preferred or ipath == preferred):
                    return iid
        for iid, ipath, itype in present:
            if itype == "video":
                return iid
        return present[0][0] if present else None

    valid = set(iid for iid, _, _ in present)
    text = open(cfg, "r", encoding="utf-8").read()
    chosen = []

    def repl(m):
        val = m.group(1)
        if val in valid:
            return m.group(0)
        if not chosen:
            chosen.append(pick())
        if chosen[0] is None:
            return m.group(0)
        return 'last_wallpaper = "' + chosen[0] + '"'

    new = re.sub(r'last_wallpaper *= *"([^"]*)"', repl, text)
    if new != text:
        with open(cfg + ".tmp", "w", encoding="utf-8") as fh:
            fh.write(new)
        os.replace(cfg + ".tmp", cfg)
        sys.stderr.write("waywallen: stale last_wallpaper -> " + str(chosen[0]) + "\n")
  '';

  # Generate a read-only config directory in the Nix store
  niriConfigDir = pkgs.runCommand "niri-config-dir" { } ''
    mkdir -p $out
    cp ${pkgs.writeText "niri-config.kdl" niriConfigContent} $out/config.kdl
  '';

in
{
  # Overwrite Nix-managed files, but leave runtime files (noctalia.kdl) alone.
  home.activation.setupNiriConfig = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    TARGET_DIR="$HOME/.config/niri"

    $DRY_RUN_CMD mkdir -p "$TARGET_DIR"
    $DRY_RUN_CMD cp -r --no-preserve=mode "${niriConfigDir}/." "$TARGET_DIR/"
    $DRY_RUN_CMD chmod 644 "$TARGET_DIR/config.kdl"
  '';

  # waywallen (AppImage) runs as a daemon and spawns its own display client.
  # Since v0.5.0 the daemon picks the backend itself from XDG_CURRENT_DESKTOP:
  # `layer-shell` on niri (wlroots protocol, paints into the `waywallen-wallpaper`
  # namespace matched by the layer-rule above), `gnome-shell` on GNOME (Mutter
  # does not expose zwlr_layer_shell_v1) and `kde-plasma` on Plasma, so no
  # --display-backend is passed. `--no-ui` keeps it silent in the tray; the
  # window opens on demand from the tray icon.
  systemd.user.services.waywallen = {
    Unit = {
      Description = "waywallen wallpaper daemon (tray, no GUI)";
      PartOf = [ "graphical-session.target" ];
      After = [ "graphical-session.target" ];
    };
    Service = {
      # Best effort: a failed repair must not keep the daemon down, so the
      # leading '-' makes systemd log the exit code but continue.
      ExecStartPre = [ "-${pkgs.python3}/bin/python3 ${waywallenFixRestore}" ];
      ExecStart = "${pkgs.waywallen}/bin/waywallen --no-ui";
      Restart = "always";
      RestartSec = 5;
    };
    Install.WantedBy = [ "graphical-session.target" ];
  };
}

