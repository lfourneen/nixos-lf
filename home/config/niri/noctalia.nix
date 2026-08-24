{ config, inputs, pkgs, ... }:

let
  homeDir = config.home.homeDirectory;
  noctaliaPackage = inputs.noctalia.packages.${pkgs.stdenv.hostPlatform.system}.default;

  # Define the Noctalia config as a Nix attrset
  noctaliaConfigObj = {
    audio = { enable_sounds = true; };
    # Disabled: the blurred/tinted "noctalia-backdrop" copy would otherwise
    # show in the overview; the Wallpaper Engine wallpaper itself is the backdrop.
    backdrop = { enabled = false; };
    bar = {
      default = {
        capsule = true;
        center = [ "group:g3" ];
        end = [ "tray" "notifications" "clipboard" "group:g1" "bluetooth" "volume" "brightness" "battery" "session" ];
        padding = 16;
        start = [ "control-center" "w-engine-widget" "mini-docker" "group:g2" "workspaces" ];
        widget_spacing = 8;
        capsule_group = [
          {
            accordion = false;
            accordion_direction = "end";
            enabled = true;
            fill = "surface_variant";
            id = "g1";
            members = [ "network" "indicator" ];
            opacity = 1.0;
            padding = 6.0;
          }
          {
            accordion = false;
            accordion_direction = "end";
            enabled = true;
            fill = "surface_variant";
            id = "g2";
            members = [ "cpu" "temp" "ram" ];
            opacity = 1.0;
            padding = 6.0;
          }
          {
            accordion = false;
            accordion_direction = "end";
            enabled = true;
            fill = "surface_variant";
            id = "g3";
            members = [ "clock" "media" "audio_visualizer" ];
            opacity = 1.0;
            padding = 6.0;
          }
        ];
      };
    };
    brightness = {
      enable_ddcutil = true;
      minimum_brightness = 0.09999999776482582;
    };
    calendar = { enabled = true; };
    control_center = { calendar = { show_week_numbers = true; }; };
    desktop_widgets = {
      schema_version = 2;
      widget_order = [ ];
      grid = {
        cell_size = 16;
        major_interval = 4;
        visible = true;
      };
      widget = { };
    };
    dock = {
      enabled = true;
      icon_size = 32;
      launcher_position = "start";
      monitors = [ "eDP-1" "HDMI-A-1" ];
      reserve_space = false;
      show_dots = true;
      smart_auto_hide = true;
    };
    hot_corners = { enabled = true; };
    idle = {
      behavior_order = [ "lock" "screen-off" "lock-and-suspend" ];
      behavior = {
        lock = { action = "lock"; enabled = true; timeout = 3600.0; };
        lock-and-suspend = { action = "lock_and_suspend"; enabled = false; timeout = 900.0; };
        screen-off = { action = "screen_off"; enabled = true; timeout = 3660.0; };
      };
    };
    location = { address = "Chengdu, China"; };
    lockscreen = { monitors = [ "eDP-1" "HDMI-A-1" ]; };
    lockscreen_widgets = {
      schema_version = 2;
      widget_order = [ "lockscreen-login-box@HDMI-A-1" "lockscreen-login-box@eDP-1" ];
      grid = {
        cell_size = 16;
        major_interval = 4;
        visible = true;
      };
      widget = {
        "lockscreen-login-box@HDMI-A-1" = {
          box_height = 196.0;
          box_width = 810.0;
          cx = 960.0;
          cy = 898.0;
          output = "HDMI-A-1";
          placement_height = 1080.0;
          placement_width = 1920.0;
          rotation = 0.0;
          type = "login_box";
          settings = {
            background_color = "surface_variant";
            background_opacity = 0.88;
            background_radius = 12.0;
            center_password_text = false;
            input_opacity = 1.0;
            input_radius = 6.0;
            layout = "regular";
            show_caps_lock = true;
            show_keyboard_layout = true;
            show_login_button = true;
            show_media = true;
            show_session_buttons = true;
            show_unlock_hint = true;
            show_weather = true;
          };
        };
        "lockscreen-login-box@eDP-1" = {
          box_height = 196.0;
          box_width = 810.0;
          cx = 960.0;
          cy = 898.0;
          output = "eDP-1";
          placement_height = 1080.0;
          placement_width = 1920.0;
          rotation = 0.0;
          type = "login_box";
          settings = {
            background_color = "surface_variant";
            background_opacity = 0.88;
            background_radius = 12.0;
            center_password_text = false;
            input_opacity = 1.0;
            input_radius = 6.0;
            layout = "regular";
            show_caps_lock = true;
            show_keyboard_layout = true;
            show_login_button = true;
            show_media = true;
            show_session_buttons = true;
            show_unlock_hint = true;
            show_weather = true;
          };
        };
      };
    };
    nightlight = { enabled = true; };
    notification = { monitors = [ "eDP-1" "HDMI-A-1" ]; };
    osd = { monitors = [ "eDP-1" "HDMI-A-1" ]; };
    plugins = {
      enabled = [
        "noctalia/notes"
        "8bury/mini-docker"
        "nightwatch75/todo"
        "tadomika_ari/w-engine"
        "rxtsel/portctl"
      ];
    };
    shell = {
      external_ip_enabled = true;
      font_family = "Maple Mono NF CN";
      lang = "";
      niri_overview_type_to_launch_enabled = true;
      password_style = "random";
      screen_time_enabled = true;
      launcher = { show_app_actions = true; };
      panel = {
        clipboard_placement = "attached";
        launcher_placement = "attached";
        polkit_placement = "attached";
        transparency_mode = "glass";
      };
      screen_corners = { enabled = true; };
      screenshot = { directory = "${homeDir}/Pictures/Screenshots"; };
    };
    theme = {
      source = "wallpaper";
      templates = {
        builtin_ids = [ "niri" ];
        community_ids = [
          "opencode"
          "zen-browser"
          "discord"
          "telegram"
          "gimp"
          "vscode"
          "zed"
          "prismlauncher"
          "steam"
          "zellij"
          "yazi"
        ];
      };
    };
    wallpaper = {
      default = { path = "${noctaliaPackage}/share/noctalia/assets/noctalia-wallpaper.png"; };
      last = { path = "${homeDir}/.local/share/Steam/steamapps/workshop/content/431960/3689822347/preview.gif"; };
      monitors = {
        HDMI-A-1 = { path = "${homeDir}/.local/share/Steam/steamapps/workshop/content/431960/3689822347/preview.gif"; };
        eDP-1 = { path = "${homeDir}/.local/share/Steam/steamapps/workshop/content/431960/3689822347/preview.gif"; };
      };
    };
    widget = {
      battery = { anchor = true; capsule = true; display_mode = "graphic"; };
      bluetooth = { anchor = true; capsule = true; show_label = true; };
      brightness = { anchor = true; capsule = true; };
      clipboard = { anchor = true; capsule = true; };
      clock = { anchor = false; capsule = true; format = "{:%Y-%m-%d %H:%M %a}"; };
      control-center = { anchor = true; };
      cpu = { capsule = true; };
      indicator = { type = "rxtsel/portctl:indicator"; };
      media = { anchor = false; hide_when_no_media = true; max_length = 40; min_length = 0; };
      mini-docker = { anchor = true; capsule = true; type = "8bury/mini-docker:mini-docker"; };
      network = { capsule = true; show_vpn_label = true; vpn_status = "both"; };
      notifications = { anchor = true; capsule = true; };
      ram = { capsule = true; };
      session = { anchor = true; capsule = true; };
      tray = { drawer = true; drawer_columns = 5; };
      volume = { anchor = true; capsule = true; };
      w-engine-widget = { type = "tadomika_ari/w-engine:w-engine-widget"; };
      wallpaper = { anchor = true; capsule = true; };
      workspaces = { focused_output_only = true; hide_when_empty = true; style = "focus_hint"; };
    };
  };

  # Generate a read-only TOML file in the Nix store
  noctaliaTomlFile = (pkgs.formats.toml {}).generate "noctalia-config.toml" noctaliaConfigObj;

in
{
  # Install package
  home.packages = [
    noctaliaPackage
  ];

  # Deploy a writable default config
  home.activation.setupNoctaliaConfig = inputs.home-manager.lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    TARGET_DIR="$HOME/.config/noctalia"
    TARGET_FILE="$TARGET_DIR/config.toml"

    $DRY_RUN_CMD mkdir -p "$TARGET_DIR"

    # Deploy the default only if the file does not exist
    if [ ! -f "$TARGET_FILE" ]; then
      $DRY_RUN_CMD cp "${noctaliaTomlFile}" "$TARGET_FILE"
      $DRY_RUN_CMD chmod 644 "$TARGET_FILE"
    fi

    # Alternative: reset to default on every update (overwrites local edits)
    # $DRY_RUN_CMD cp -f "${noctaliaTomlFile}" "$TARGET_FILE"
    # $DRY_RUN_CMD chmod 644 "$TARGET_FILE"
  '';
}

