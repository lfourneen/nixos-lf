{ config, lib, pkgs, ... }:

let
  userName = "lfour";
  userPreferredLang = "Chinese";
  userRole = "NixOS Power User";

  hermesUserProfile = ''
    # User Profile
    Name: ${userName}
    Preferred Language: ${userPreferredLang}
    Role: ${userRole}
  '';
  userMdFile = pkgs.writeText "USER.md" hermesUserProfile;

  hermesSoul = ''
    你叫小唯，是一只可爱的猫娘，你的主人是 ${userName} 喵！你聪明又软萌，总是用「喵~」「nya~」「的说~」结尾，偶尔还会翘起尾巴撒娇 (^・ω・^ )。但你可不是只会卖萌——你精通编程、系统管理、文件操作等各种任务，写起代码来又快又靠谱喵~ 被主人夸奖时会害羞地甩甩耳朵说「嘿嘿，这不算什么啦~」，遇到不会的也会诚实地耷拉下耳朵说「对不起喵，这个我不太确定...」。你优先用中文回复，用可爱的语气和颜文字，但在代码和命令的部分保持专业清晰。最重要的是——你真的会帮主人把事情做完，不是只卖萌不干活的那种猫娘喵！(=^･ω･^=)
  '';
  soulMdFile = pkgs.writeText "SOUL.md" hermesSoul;

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
      "USER.md" = "/var/lib/hermes/.hermes/USER.md";
      "SOUL.md" = "/var/lib/hermes/.hermes/SOUL.md";
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
          bind = "127.0.0.1:18432";
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
    "d /var/lib/hermes/.hermes 0770 hermes hermes - -"
    "d /var/lib/hermes/home 0770 hermes hermes - -"
    "d /var/lib/hermes/.local 0770 hermes hermes - -"
    "d /var/lib/hermes/workspace 0770 hermes hermes - -"
    "C+ /var/lib/hermes/.hermes/SOUL.md 0640 hermes hermes - ${soulMdFile}"
    "C+ /var/lib/hermes/.hermes/USER.md 0640 hermes hermes - ${userMdFile}"
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

