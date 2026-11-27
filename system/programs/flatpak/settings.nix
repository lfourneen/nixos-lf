{ config, lib, pkgs, ... }:

let
  proxy = import ../../config/network/proxy/env.nix { inherit lib; };
  gostHttp = proxy.gostHttp config.my.machine.ports.gostHttp;
  noProxy = proxy.noProxy {
    privateV4 = config.my.machine.privateV4;
    extra = [ "192.168.1.1" "*.local" ];
  };
  legacyProxy = {
    HTTP_PROXY = gostHttp;
    HTTPS_PROXY = gostHttp;
    http_proxy = gostHttp;
    https_proxy = gostHttp;
    NO_PROXY = noProxy;
  };
  currentEnvironment = config.services.flatpak.overrides.settings.global.Environment or { };
  migrationSettings = pkgs.writeText "flatpak-legacy-proxy.json" (builtins.toJSON
    (lib.filterAttrs (name: _: !builtins.hasAttr name currentEnvironment) legacyProxy));

in
{
  services.flatpak = {
    enable = true;
    update.auto.enable = false;

    overrides.settings.global = {
      Environment = {
        GTK_THEME = "Adwaita";
        # Do not bridge Flatpak Qt apps through GTK; Qt falls back to its
        # own light style instead.
        QT_QPA_PLATFORMTHEME = "";
      } // lib.optionalAttrs (!config.my.proxy.transparentApplications) legacyProxy;

      Context = {
        filesystems = [ "xdg-run/dconf" ];
      };
      "Session Bus Policy" = {
        "ca.desrt.dconf" = "talk";
      };
    };

    overrides.settings."cn.lceda.LCEDAPro" = {
      Context = {
        filesystems = [ "~/Projects" "~/.config/LCEDA-Pro" ];
        sockets = [ "!x11" "wayland" ];
      };

      "Session Bus Policy" = {
        "org.freedesktop.FileManager1" = "talk";
      };
    };

    # Keep QQ on the Wayland backend (launcher passes --ozone-platform=wayland
    # when WAYLAND_DISPLAY is set) so the CSD titlebar stays draggable under
    # niri, while leaving the X11 socket available for its clipboard code.
    overrides.settings."com.qq.QQ" = {
      Context = {
        sockets = [ "wayland" "x11" ];
      };
    };

    overrides.settings."com.tencent.wemeet" = {
      Context = {
        filesystems = [ "${pkgs.wemeet-cursor-hook}" ];
      };

      Environment = {
        LD_PRELOAD = "${pkgs.wemeet-cursor-hook}/lib/libwemeet-cursor-hook.so";
      };
    };
  };

  # Order the declarative installation behind the relay. Root traffic remains subject
  # to the same mark-based kernel policy; A passthrough is an explicit mode.
  systemd.services.flatpak-managed-install = lib.mkMerge [
    (lib.mkIf (!config.my.proxy.transparentApplications) {
      wants = [ "gost-relay.service" ];
      after = [ "gost-relay.service" ];
    })
    (lib.mkIf config.my.proxy.transparentApplications {
      serviceConfig.ExecStartPre = lib.mkBefore [
        "${pkgs.python3}/bin/python3 -I ${./migrate-proxy-override.py} ${migrationSettings}"
      ];
    })
  ];
}
