{ config, lib, pkgs, ... }:

let
  cfg = config.my.browser;

  # Chrome / Chromium managed policy. Flatpak Chrome declares `host-etc` (host
  # /etc appears at /run/host/etc) and its launcher (chrome.sh) symlinks
  # /run/host/etc/opt/chrome/policies/{managed,recommended}/*.json into the
  # sandbox, so a host file at /etc/opt/chrome/policies/managed is what it reads.
  # Verify with chrome://policy.
  chromePolicyFile = pkgs.writeText "chrome-managed-policies.json" (builtins.toJSON cfg.chrome.policies);

  # Extension ID -> AMO install entry. AMO resolves the latest signed XPI by ID
  # (`/downloads/latest/<id>/latest.xpi`), so no version/hash is pinned. Braces
  # in the GUID-style IDs are percent-encoded so the URL stays valid.
  encId = id: lib.replaceStrings [ "{" "}" ] [ "%7B" "%7D" ] id;
  extensionSettings = lib.mapAttrs
    (id: mode: {
      installation_mode = mode;
      install_url = "https://addons.mozilla.org/firefox/downloads/latest/${encId id}/latest.xpi";
    })
    cfg.zen.extensions;

  # Firefox / Zen policy. The `preferences` option is folded into the policy
  # tree's `Preferences` key; an explicit `policies.Preferences`/`ExtensionSettings`
  # is merged under it so freeform policies can still extend those keys.
  zenPolicy = builtins.toJSON {
    policies = cfg.zen.policies // {
      Preferences = (cfg.zen.policies.Preferences or { }) // cfg.zen.preferences;
      ExtensionSettings = (cfg.zen.policies.ExtensionSettings or { }) // extensionSettings;
    };
  };
  zenPolicyFile = pkgs.writeText "zen-policies.json" zenPolicy;

  # Flatpak system-wide extension directory (Zen is installed system-wide). The
  # arch/branch mirror the installed ref (app/app.zen_browser.zen/x86_64/stable).
  zenExt = "/var/lib/flatpak/extension/app.zen_browser.zen.systemconfig/x86_64/stable";

in
{
  config = lib.mkMerge [
    (lib.mkIf cfg.chrome.enable {
      # Chrome's flatpak launcher symlinks this into its sandbox (see above).
      environment.etc."opt/chrome/policies/managed/browser.json" = {
        source = chromePolicyFile;
        mode = "0644";
      };
    })

    (lib.mkIf cfg.zen.enable {
      # Zen's systemconfig extension: create the directory tree Flatpak looks for
      # and place the policy file in it. `C+` copies a real file rather than a
      # store symlink, since Flatpak mounts the directory read-only and a
      # dangling link is one more failure mode.
      systemd.tmpfiles.rules = [
        "d ${zenExt} 0755 root root -"
        "d ${zenExt}/policies 0755 root root -"
        "C+ ${zenExt}/policies/policies.json 0644 root root - ${zenPolicyFile}"
      ];
    })
  ];
}

