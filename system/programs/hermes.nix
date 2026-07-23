{ config, lib, pkgs, ... }:

let
  hermesUserProfile = ''
    # User Profile
    Name: lfour
    Preferred Language: Chinese
    Role: NixOS Power User
  '';
  userMdFile = pkgs.writeText "USER.md" hermesUserProfile;

in
{
  sops = {
    defaultSopsFile = ./secrets/secrets.yaml;
    age.keyFile = "/home/lfour/.config/sops/age/keys.txt";

    secrets = {
      "hermes_api_key" = {};
    };

    templates."hermes.env" = {
      content = ''
        DEEPSEEK_API_KEY="${config.sops.placeholder."hermes_api_key"}"
      '';
      owner = "hermes";
      group = "hermes";
      mode = "0600";
    };
  };

  services.hermes-agent = {
    enable = true;
    addToSystemPackages = true;
    environmentFiles = [ config.sops.templates."hermes.env".path ];
    restart = "always";
    restartSec = 5;
    documents = {
      "USER.md" = "/var/lib/hermes/config/USER.md";
    };
    settings = {
      model = {
        provider = "deepseek";
        default = "deepseek-v4-pro";
      };
      toolsets = [ "all" ];
      terminal = {
        backend = "local";
        cwd = "/var/lib/hermes";
        timeout = 180; 
      };
      compression = {
        enabled = true;
        threshold = 0.85;
        summary_model = "deepseek-v4-flash";
      };
      display = {
        compact = false; 
        personality = "kawaii"; 
      };
      memory = { 
        memory_enabled = true; 
        user_profile_enabled = true;
      };
      agent = { 
        max_turns = 60; 
        verbose = true;
      };
      gateway = {
        web = {
          enabled = true;
          bind = "127.0.0.1:8080";
        };
      };
      security = {
        allowed_users = [ "lfour" ];
      };
      plugins.enabled = [
        "hermes-lcm"
        "rtk-rewrite"
      ];
    };
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/hermes 0770 hermes hermes - -"
    "d /var/lib/hermes/workspace 0770 hermes hermes - -"
    "d /var/lib/hermes/config 0770 hermes hermes - -"
    "C /var/lib/hermes/config/USER.md 0640 hermes hermes - ${userMdFile}"
  ];

  systemd.services.hermes-agent.serviceConfig = {
    User = "hermes";
    Group = "hermes";
    EnvironmentFile = config.sops.templates."hermes.env".path;
    ProtectSystem = "strict";
    ProtectHome = lib.mkForce true;
    SupplementaryGroups = [ "users" ];
    ReadWritePaths = [ "/var/lib/hermes" ];
    PrivateTmp = true;
    ProtectControlGroups = true;
    ProtectKernelModules = true;
    RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" ];
  };
}

