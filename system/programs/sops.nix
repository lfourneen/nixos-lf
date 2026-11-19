{ pkgs, config, lib, ... }:

let
  desktopKey = "${config.users.users.${config.my.machine.desktopUser}.home}/.config/sops/age/keys.txt";

  systemKey = config.my.secrets.systemAgeKey;

  # /persist and /var/lib are outside every desktop home.
  safeSystemKeyPath =
    lib.hasPrefix "/persist/" systemKey.file
    || lib.hasPrefix "/var/lib/" systemKey.file;

in
{
  options.my.secrets.systemAgeKey = {
    enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Decrypt system secrets with the root-only Age identity at
        my.secrets.systemAgeKey.file instead of the desktop key. Copy an
        existing identity with scripts/system-age-key.py --adopt; its recipient
        does not change. Set this to false only to fall back to
        ~/.config/sops/age/keys.txt.
      '';
    };

    file = lib.mkOption {
      type = lib.types.str;
      default = "/persist/secrets/age/system.key";
      description = ''
        Absolute path of the root-only Age private key. It must not live below
        a user home directory.
      '';
    };
  };

  config = {
    assertions = [
      {
        assertion = !systemKey.enable || safeSystemKeyPath;
        message = ''
          my.secrets.systemAgeKey.file (${systemKey.file}) must live under
          /persist or /var/lib so the desktop identity cannot read the system
          secrets key.
        '';
      }
    ];

    # The key directory exists only when the option is enabled.
    systemd.tmpfiles.rules = lib.optional systemKey.enable
      "d /persist/secrets/age 0700 root root - -";

    # Fail before services start instead of letting sops-install-secrets fail
    # without an actionable message when the key has not been adopted yet.
    system.activationScripts = lib.optionalAttrs systemKey.enable {
      system-secrets-key = ''
        if [ ! -f "${systemKey.file}" ] || [ -L "${systemKey.file}" ]; then
          echo "my.secrets.systemAgeKey is enabled but ${systemKey.file} is missing or not a regular file." >&2
          echo "Run scripts/system-age-key.py --adopt ~/.config/sops/age/keys.txt, or set my.secrets.systemAgeKey.enable = false." >&2
          exit 1
        fi
        key_mode="$(${pkgs.coreutils}/bin/stat -c %a -- "${systemKey.file}")"
        key_owner="$(${pkgs.coreutils}/bin/stat -c %u -- "${systemKey.file}")"
        case "$key_mode" in
          400|600) ;;
          *)
            echo "my.secrets.systemAgeKey.file must be mode 400 or 600, found $key_mode." >&2
            exit 1
            ;;
        esac
        if [ "$key_owner" != 0 ]; then
          echo "my.secrets.systemAgeKey.file must be owned by root, found uid $key_owner." >&2
          exit 1
        fi
      '';
    };

    sops = {
      defaultSopsFile = ../secrets/secrets.yaml;

      # System services decrypt with the root-only identity by default. With
      # the option disabled, the desktop identity remains the only key.
      age.keyFile = if systemKey.enable then systemKey.file else desktopKey;

      useSystemdActivation = true;  # render secrets at every boot (/run is tmpfs; activation-script mode loses secrets on reboot)

      secrets = {
        "hermes_api_key" = {};
        "qq_app_id" = {};
        "qq_client_secret" = {};
        "qq_group_allowed_users" = {};
        "hermes_soul" = { sopsFile = ../secrets/soul.yaml; key = "soul"; };
        "sandbox" = { sopsFile = ../secrets/skill.yaml; };

        # Private source catalog: proxy_source contains a JSON {"sources":[...]}
        # string; legacy single-source JSON remains supported. review/register
        # --id selects one definition; registered copies live in /var/lib/clash-source.
        "proxy_source" = {
          sopsFile = ../secrets/proxy.yaml;
          owner = "root";
          group = "root";
          mode = "0400";
        };

        # Registered sources allowed to drop unlisted insecure leaves on
        # import, one source id per line. Missing or invalid input stays strict.
        "insecure_sources" = {
          sopsFile = ../secrets/whitelist.yaml;
          owner = "root";
          group = "root";
          mode = "0400";
        };
      };

      templates."hermes.env" = {
        content = ''
          DEEPSEEK_API_KEY="${config.sops.placeholder."hermes_api_key"}"
          QQ_APP_ID="${config.sops.placeholder."qq_app_id"}"
          QQ_CLIENT_SECRET="${config.sops.placeholder."qq_client_secret"}"
          QQ_GROUP_ALLOWED_USERS="${config.sops.placeholder."qq_group_allowed_users"}"
        '';
        owner = "hermes";
        group = "hermes";
        mode = "0600";
      };

      # Persona file: hermes-agent's preStart installs it into HERMES_HOME.
      templates."SOUL.md" = {
        content = config.sops.placeholder."hermes_soul";
        owner = "hermes";
        group = "hermes";
        mode = "0400";
      };

      # Skill body: preStart installs it as skills/nix-missing-tool/SKILL.md.
      templates."sandbox.md" = {
        content = config.sops.placeholder."sandbox";
        owner = "hermes";
        group = "hermes";
        mode = "0400";
      };
    };
  };
}
