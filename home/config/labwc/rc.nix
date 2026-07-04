{
  wayland.windowManager.labwc.rc = {
    keyboard = {
      default = true;
        keybind = [
        {
          "@key" = "W-Return";
        }
        {
          "@key" = "W-x";
          action = {
            "@name" = "Close";
          };
        }
        {
          "@key" = "W-m";
          action = {
            "@name" = "Iconify";
          };
        }
        {
          "@key" = "W-z";
          action = {
            "@name" = "Execute";
            command = "kitty";
          };
        }
        {
          "@key" = "W-space";
          action = {
            "@name" = "Execute";
            command = "fuzzel";
          };
        }
        {
          "@key" = "W-1";
          action = {
            "@name" = "GoToDesktop";
            "@to" = "1";
          };
        }
        {
          "@key" = "W-2";
          action = {
            "@name" = "GoToDesktop";
            "@to" = "2";
          };
        }
        {
          "@key" = "W-3";
          action = {
            "@name" = "GoToDesktop";
            "@to" = "3";
          };
        }
        {
          "@key" = "W-4";
          action = {
            "@name" = "GoToDesktop";
            "@to" = "4";
          };
        }
        {
          "@key" = "W-5";
          action = {
            "@name" = "GoToDesktop";
            "@to" = "5";
          };
        }
        {
          "@key" = "W-6";
          action = {
            "@name" = "GoToDesktop";
            "@to" = "6";
          };
        }
        {
          "@key" = "W-7";
          action = {
            "@name" = "GoToDesktop";
            "@to" = "7";
          };
        }
        {
          "@key" = "W-8";
          action = {
            "@name" = "GoToDesktop";
            "@to" = "8";
          };
        }
        {
          "@key" = "W-9";
          action = {
            "@name" = "GoToDesktop";
            "@to" = "9";
          };
        }
        {
          "@key" = "W-0";
          action = {
            "@name" = "GoToDesktop";
            "@to" = "10";
          };
        }
        {
          "@key" = "W-S-1";
          action = {
            "@name" = "SendToDesktop";
            "@to" = "1";
            "@follow" = "false";
          };
        }
        {
          "@key" = "W-S-2";
          action = {
            "@name" = "SendToDesktop";
            "@to" = "2";
            "@follow" = "false";
          };
        }
        {
          "@key" = "W-S-3";
          action = {
            "@name" = "SendToDesktop";
            "@to" = "3";
            "@follow" = "false";
          };
        }
        {
          "@key" = "W-S-4";
          action = {
            "@name" = "SendToDesktop";
            "@to" = "4";
            "@follow" = "false";
          };
        }
        {
          "@key" = "W-S-5";
          action = {
            "@name" = "SendToDesktop";
            "@to" = "5";
            "@follow" = "false";
          };
        }
        {
          "@key" = "W-S-6";
          action = {
            "@name" = "SendToDesktop";
            "@to" = "6";
            "@follow" = "false";
          };
        }
        {
          "@key" = "W-S-7";
          action = {
            "@name" = "SendToDesktop";
            "@to" = "7";
            "@follow" = "false";
          };
        }
        {
          "@key" = "W-S-8";
          action = {
            "@name" = "SendToDesktop";
            "@to" = "8";
            "@follow" = "false";
          };
        }
        {
          "@key" = "W-S-9";
          action = {
            "@name" = "SendToDesktop";
            "@to" = "9";
            "@follow" = "false";
          };
        }
        {
          "@key" = "W-S-0";
          action = {
            "@name" = "SendToDesktop";
            "@to" = "10";
            "@follow" = "false";
          };
        }
        {
          "@key" = "W-S-Left";
          action = {
            "@name" = "GoToDesktop";
            "@to" = "left-occupied";
          };
        }
        {
          "@key" = "W-S-Right";
          action = {
            "@name" = "GoToDesktop";
            "@to" = "right-occupied";
          };
        }
        {
          "@key" = "W-l";
          action = {
            "@name" = "GoToDesktop";
            "@to" = "right";
            "@wrap" = "yes";
          };
        }
      ];
    };

    desktops = {
      "@number" = 10;
      names = [
        "1"
        "2"
        "3"
        "4"
        "5"
        "6"
        "7"
        "8"
        "9"
        "10"
      ];
    };

    mouse = {
      default = true;
      context = [
        {
          "@name" = "Root";
          mousebind = [
            {
              "@button" = "Left";
              "@action" = "Press";
            }
            {
              "@button" = "Left";
              "@action" = "Click";
            }
            {
              "@button" = "Right";
              "@action" = "Press";
              action = {
                "@name" = "ShowMenu";
                "@menu" = "root-menu";
              };
            }
          ];
        }
      ];
    };

    theme = {
      cornerRadius = 16;
      font = {
        "@place" = "ActiveWindow";
        name = "Maple Mono NF CN";
        size = 10;
        slant = "normal";
        weight = "normal";
      };
    };
  };
}

