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
    defaultSopsFile = ../secrets/secrets.yaml;
    age.keyFile = "${config.users.users.lfour.home}/.config/sops/age/keys.txt";
    useSystemdActivation = true;  # render secrets at every boot (/run is tmpfs; activation-script mode loses secrets on reboot)

    secrets = {
      "hermes_api_key" = {};
      "qq_app_id" = {};
      "qq_client_secret" = {};
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

  services.hermes-agent = {
    enable = true;
    
    addToSystemPackages = true;
    
    restart = "always";
    restartSec = 5;

    # QQ Bot adapter needs aiohttp (and httpx, already a core dep).
    extraDependencyGroups = [ "messaging" ];
    
    environmentFiles = [ config.sops.templates."hermes.env".path ];

    workingDirectory = "/var/lib/hermes/workspace";

    documents = {
      "USER.md" = "/var/lib/hermes/.hermes/USER.md";
      "SOUL.md" = "/var/lib/hermes/.hermes/SOUL.md";
    };

    settings = {
      file_read_max_chars = 200000;
      
      model = {
        provider = "deepseek";
        default = "deepseek-v4-flash-vision-exp";
      };

      terminal = {
        backend = "local";
        cwd = "/var/lib/hermes";
        timeout = 180; 
      };

      compression = {
        enabled = true;
        threshold = 0.85;
        summary_model = "deepseek-v4-flash-vision-exp";
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

      mcp_servers = {
        fetch = {
          command = "${pkgs.mcp-server-fetch}/bin/mcp-server-fetch";
          args = [];

          env = { 
            HTTP_PROXY = "http://127.0.0.1:33332"; 
            HTTPS_PROXY = "http://127.0.0.1:33332"; 
          };
        };

        filesystem = {
          command = "${pkgs.mcp-server-filesystem}/bin/mcp-server-filesystem";
          args = [ "/var/lib/hermes/workspace" "/var/lib/hermes/home" "/tmp" ];
        };

        nixos = {
          command = "${pkgs.mcp-nixos}/bin/mcp-nixos";
          args = [];
        };

        sequential-thinking = {
          command = "${pkgs.mcp-server-sequential-thinking}/bin/mcp-server-sequential-thinking";
          args = [];
        };

        time = {
          command = "${pkgs.mcp-server-time}/bin/mcp-server-time";
          args = [];
        };

        playwright = {
          command = "${pkgs.playwright-mcp}/bin/playwright-mcp";

          args = [
            "--executable-path"
            "${pkgs.playwright-driver.browsers}/chromium-${pkgs.playwright-driver.passthru.browsersJSON.chromium.revision}/chrome-linux64/chrome"
            "--proxy-server"
            "http://127.0.0.1:33332"
          ];

          env = {
            PWMCP_PROFILES_DIR_FOR_TEST = "/var/lib/hermes/.playwright-profiles";
          };
        };
      };

      security = {
        allowed_users = [ "lfour" ];
      };

      platforms.qqbot.enabled = true;
      
      toolsets = [ "all" ];

      plugins.enabled = [
        "hermes-lcm"
        "rtk-rewrite"
        "terminal-exec"
        "web-scraper"
        "auto-coder"
        "git-ops"
        "cron-tasks"
        "code-analyzer"
      ];
    };
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/hermes 0770 hermes hermes - -"
    "d /var/lib/hermes/.hermes 0770 hermes hermes - -"
    "d /var/lib/hermes/.local 0770 hermes hermes - -"
    "d /nix/var/nix/profiles/per-user/hermes 0755 hermes hermes - -"
    "d /var/lib/hermes/.playwright-profiles 0770 hermes hermes - -"
    "Z /var/lib/hermes/home 0770 hermes hermes - -"
    "Z /var/lib/hermes/workspace 0770 hermes hermes - -"
    "C+ /var/lib/hermes/.hermes/SOUL.md 0640 hermes hermes - ${soulMdFile}"
    "C+ /var/lib/hermes/.hermes/USER.md 0640 hermes hermes - ${userMdFile}"
    "f+ /var/lib/hermes/.gitconfig 0640 hermes hermes - [user]\n\tname = Hermes Agent\n\temail = hermes@local.domain\n"
  ];

  systemd.services.hermes-agent = {
    path = with pkgs; [
      bash
      coreutils
      git
      ripgrep
      fd
      jq
      gh
      nh
      nix-tree
      nodejs
      powertop
    ];
    
    serviceConfig = {
      User = "hermes";
      Group = "hermes";
      EnvironmentFile = config.sops.templates."hermes.env".path;
      ProtectSystem = "strict";
      ProtectHome = lib.mkForce true;
      SupplementaryGroups = [ "users" ];
    
      ReadWritePaths = [ 
        "/var/lib/hermes"
        "/nix/var/nix/profiles/per-user/hermes"
      ];
    
      ReadOnlyPaths = [
        "/nix/store"
        "-/etc/nix"
      ];
      RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" "AF_NETLINK" ];
   
      PrivateTmp = true;
      ProtectControlGroups = true;
      ProtectKernelModules = true;
    };
  };

  systemd.services.hermes-dashboard = {
    description = "Hermes Web Dashboard";
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];

    environment = {
      HERMES_HOME = "/var/lib/hermes/.hermes";
      HOME = "/var/lib/hermes";
    };

    serviceConfig = {
      User = "hermes";
      Group = "hermes";
      WorkingDirectory = "/var/lib/hermes/workspace";
      ExecStart = "${config.services.hermes-agent.package}/bin/hermes dashboard --host 127.0.0.1 --port 18432 --no-open";
      Restart = "always";
      RestartSec = 5;
    };
  };

  environment.systemPackages = with pkgs; [
    mcp-server-fetch
    mcp-server-filesystem
    mcp-nixos
    mcp-server-sequential-thinking
    mcp-server-time
    playwright-mcp
  ];
}

