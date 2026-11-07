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
      "qq_group_allowed_users" = {};
      "hermes_soul" = { sopsFile = ../secrets/soul.yaml; key = "soul"; };
      "sandbox" = { sopsFile = ../secrets/skill.yaml; };
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
}

