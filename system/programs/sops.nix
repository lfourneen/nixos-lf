{ config, ... }:

{
  sops = {
    defaultSopsFile = ../secrets/secrets.yaml;
    age.keyFile = "${config.users.users.lfour.home}/.config/sops/age/keys.txt";
    useSystemdActivation = true;  # render secrets at every boot (/run is tmpfs; activation-script mode loses secrets on reboot)

    secrets = {
      "hermes_api_key" = {};
      "qq_app_id" = {};
      "qq_client_secret" = {};

      # Codex DeepSeek key (used by home/programs/codex.nix).
      "deepseek-codex-api-key".owner = "lfour";
    };

    templates."hermes.env" = {
      content = ''
        DEEPSEEK_API_KEY="${config.sops.placeholder."hermes_api_key"}"
        QQ_APP_ID="${config.sops.placeholder."qq_app_id"}"
        QQ_CLIENT_SECRET="${config.sops.placeholder."qq_client_secret"}"
      '';
      owner = "hermes";
      group = "hermes";
      mode = "0600";
    };
  };
}

