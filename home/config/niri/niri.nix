{
  xdg.configFile."niri/config.kdl".text = ''
    // niri config

    // Autostart
    spawn-at-startup "noctalia-shell"

    // General Settings
    prefer-no-csd
    screenshot-path "~/Pictures/Screenshots/%Y-%m-%d-%H-%M-%S.jpg"

    // Environment Variables
    environment {
      CLUTTER_BACKEND "wayland"
      GDK_BACKEND "wayland,x11"
      MOZ_ENABLE_WAYLAND "1"
      NIXOS_OZONE_WL "1"
      QT_QPA_PLATFORM "wayland"
      QT_WAYLAND_DISABLE_WINDOWDECORATION "1"
      ELECTRON_OZONE_PLATFORM_HINT "auto"

      XDG_SESSION_TYPE "wayland"
      XDG_CURRENT_DESKTOP "niri"
      DISPLAY ":0"

      // Proxy
      //http_proxy "http://127.0.0.1:7890"
      //https_proxy "http://127.0.0.1:7890"
      //all_proxy "socks5://127.0.0.1:7890"
      auto_proxy "http://127.0.0.1:33331/commands/pac"
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
      xcursor-theme "M200"
      xcursor-size 48
    }

    // Overview appearance
    overview {
      backdrop-color "#00000000"   // transparent so wallpaper shows through
      workspace-shadow {
        off
      }
    }

    //layer-rule {
      //match namespace="^swww-daemon$"
      //place-within-backdrop true
    //}

    // Wallpaper layer (noctalia-shell)
    layer-rule {
      match namespace="^noctalia-wallpaper*"
      place-within-backdrop true
    }

    layer-rule {
      match namespace="^noctalia-overview*"
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
      // preset-window-heights { }
      default-column-width { proportion 0.5; }
      // default-column-width {}

      // Focus ring (active window highlight)
      focus-ring {
        width 1
	      active-color "#E6B450"      // ayu accent color
	      inactive-color "#0A0E14"
      }

      // Window border
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

      }
    }

    hotkey-overlay {

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
    window-rule {
      match title=r#".*(Open|Save|Select).*"#
      open-floating true
      default-column-width { proportion 0.0; }
      max-width 800
      max-height 1000
    }

    window-rule {
      match title=r#".*File.*"#
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

    window-rule {
      match title=r#".*Sign in - Google Accounts — Mozilla Firefox"#
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

    // Global window appearance
    window-rule {
      geometry-corner-radius 0
      clip-to-geometry true
    }

    // Inactive window transparency
    window-rule {
      match is-active=false
      opacity 0.9
    }

    binds {
      Mod+Shift+Slash { show-hotkey-overlay; }
      Mod+Z hotkey-overlay-title="Open a Terminal: kitty" { spawn "kitty"; }
      Mod+Space hotkey-overlay-title="Run an Application: fuzzel" { spawn "fuzzel"; }
      Super+Alt+L hotkey-overlay-title="Lock the Screen: noctalia-shell" { spawn "noctalia-shell" "ipc" "call" "lockScreen" "lock"; }

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
  '';
}

