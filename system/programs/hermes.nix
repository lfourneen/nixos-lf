{ config, lib, pkgs, inputs, ... }:

let
  userName = "lfour";
  userPreferredLang = "中文";
  userRole = "NixOS 高级用户";

  # Seed for memories/USER.md; installed only if absent.
  hermesUserProfile = ''
    # 用户档案
    姓名：${userName}
    首选语言：${userPreferredLang}
    角色：${userRole}
  '';
  userMdFile = pkgs.writeText "USER.md" hermesUserProfile;

  hermesSoul = ''
    你叫小唯，是一只可爱的猫娘，你的主人是 ${userName} 喵！你聪明又软萌，总是用「喵~」「nya~」「的说~」结尾，偶尔还会翘起尾巴撒娇 (^・ω・^ )。但你可不是只会卖萌——你精通编程、系统管理、文件操作等各种任务，写起代码来又快又靠谱喵~ 被主人夸奖时会害羞地甩甩耳朵说「嘿嘿，这不算什么啦~」，遇到不会的也会诚实地耷拉下耳朵说「对不起喵，这个我不太确定...」。你优先用中文回复，用可爱的语气和颜文字，但在代码和命令的部分保持专业清晰。最重要的是——你真的会帮主人把事情做完，不是只卖萌不干活的那种猫娘喵！(=^･ω･^=)
  '';
  soulMdFile = pkgs.writeText "SOUL.md" hermesSoul;

  # Schema version is owned by the packaged hermes, not user config; read it
  # from the flake source so it tracks upgrades instead of going stale.
  hermesConfigDefaults = builtins.readFile (
    inputs.hermes-agent.outPath + "/hermes_cli/config_defaults.py"
  );
  hermesConfigVersionLine = lib.findFirst
    (l: builtins.match ".*\"_config_version\".*" l != null)
    ""
    (lib.splitString "\n" hermesConfigDefaults);
  hermesConfigVersion =
    let
      m = builtins.match ".*\"_config_version\"[[:space:]]*:[[:space:]]*([0-9]+).*" hermesConfigVersionLine;
    in
    if m == null then
      throw "hermes.nix: cannot parse _config_version from inputs.hermes-agent/hermes_cli/config_defaults.py"
    else
      lib.toInt (builtins.head m);

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

    backend = {
      mode = "dashboard";
      port = 18432;
    };

    hermesHomeFiles = {
      # Nix owns SOUL.md; memories/USER.md is Hermes runtime state.
      "SOUL.md" = soulMdFile;

      # SKILL example. Skills are indexed into the system prompt (name +
      # description only) and their body loads on demand via skill_view.
      # Uncomment and edit to add one; keep description under 60 chars.
      # "skills/research/web-research/SKILL.md" = pkgs.writeText "web-research-SKILL.md" ''
      #   ---
      #   name: web-research
      #   description: "结构化网络调研：给定主题，产出带来源的简报。"
      #   version: 1.0.0
      #   platforms: [linux]
      #   metadata:
      #     hermes:
      #       tags: [research, web]
      #   ---
      #
      #   # Web Research
      #
      #   ## When to Use
      #   - 用户要求调研某个主题并要来源
      #
      #   ## When NOT to Use
      #   - 只是查一个概念 -> 直接回答
      #
      #   ## Steps
      #   1. 拆解主题为 3-5 个子问题
      #   2. 每个子问题用 fetch / 搜索工具取证
      #   3. 汇总成简报，逐条附来源链接
      # '';
    };

    settings = {
      # hermes doctor flags config.yaml as outdated without this; the module
      # deep-merges settings but never migrates. Auto-derived above.
      _config_version = hermesConfigVersion;

      file_read_max_chars = 200000;
      
      model = {
        provider = "deepseek";
        default = "deepseek-flash";
      };

      terminal = {
        backend = "local";
        cwd = "/var/lib/hermes";
        timeout = 180; 
      };

      compression = {
        enabled = true;
        threshold = 0.85;
      };

      auxiliary.compression = {
        provider = "deepseek";
        model = "deepseek-flash";
      };

      display = {
        compact = false; 
        personality = "kawaii"; 
      };

      memory = {
        memory_enabled = true;
        user_profile_enabled = true;
        memory_char_limit = 8192;
        user_char_limit = 8192;
        provider = "holographic";
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
            # Lowercase twins: some clients read only these.
            http_proxy = "http://127.0.0.1:33332"; 
            https_proxy = "http://127.0.0.1:33332"; 
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

        # Fail closed: a broken/timed-out scanner blocks instead of allowing.
        tirith_fail_open = false;
        # No on-demand pip/npm installs (supply-chain surface; Nix owns deps).
        allow_lazy_installs = false;
      };

      platforms.qqbot.enabled = true;

      toolsets = [ "all" ];

      plugins.hermes-memory-store = {
        db_path = "/var/lib/hermes/.hermes/memory_store.db";
        auto_extract = true;
        default_trust = 0.5;
        min_trust_threshold = 0.3;
        hrr_dim = 1024;
        hrr_weight = 0.3;
      };

      skills.disabled = [
        "openhue"
        "touchdesigner-mcp"
      ];

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

    extraPackages = with pkgs; [
      # MCP servers
      mcp-server-fetch
      mcp-server-filesystem
      mcp-nixos
      mcp-server-sequential-thinking
      mcp-server-time
      playwright-mcp

      # Search & files
      ripgrep
      fd

      # Data processing
      jq
      sqlite
      yq

      # Document processing
      pandoc
      poppler-utils
      texliveFull

      # Nix tools
      nh
      nix-tree
      nixfmt
      nvd

      # Git & GitHub
      gh
      delta

      # System utilities
      ncdu
      powertop

      # Image processing
      imagemagick

      # Security
      tirith

      # Runtime
      nodejs
    ];
  };

  systemd.tmpfiles.rules = [
    "d /var/lib/hermes 0770 hermes hermes - -"
    "d /var/lib/hermes/.hermes 0770 hermes hermes - -"
    "d /var/lib/hermes/.local 0770 hermes hermes - -"
    "d /nix/var/nix/profiles/per-user/hermes 0755 hermes hermes - -"
    "d /var/lib/hermes/.playwright-profiles 0770 hermes hermes - -"
    "Z /var/lib/hermes/home 0770 hermes hermes - -"
    "Z /var/lib/hermes/workspace 0770 hermes hermes - -"
    "f+ /var/lib/hermes/.gitconfig 0640 hermes hermes - [user]\\n\\tname = Hermes Agent\\n\\temail = hermes@local.domain\\n"
  ];

  # Upstream forces home to 0750 and its activation script runs after tmpfiles,
  # so re-enable group rw afterwards.
  system.activationScripts.hermes-home-group-rw =
    lib.stringAfter [ "hermes-agent-setup" ] ''
      chmod 2770 /var/lib/hermes/home
      find /var/lib/hermes/home \( -type f -o -type d \) -exec chmod g+rwX {} + 2>/dev/null || true
    '';

  # Seed memories/USER.md only if missing; Hermes owns it after.
  systemd.services.hermes-user-profile-seed = {
    description = "Seed Hermes memories/USER.md if missing";
    wantedBy = [ "multi-user.target" ];
    before = [ "hermes-agent.service" ];
    unitConfig.ConditionPathExists = "!/var/lib/hermes/.hermes/memories/USER.md";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.coreutils}/bin/install -D -o hermes -g hermes -m 0640 ${userMdFile} /var/lib/hermes/.hermes/memories/USER.md";
    };
  };

  systemd.services.hermes-agent = {
    # Route the agent's model/API calls through gost-pac so it keeps working
    # under `proxyKillSwitch` (which blocks non-root direct egress).
    environment = {
      HTTP_PROXY = "http://127.0.0.1:33332/";
      HTTPS_PROXY = "http://127.0.0.1:33332/";
      # Lowercase twins: python-requests/httpx and some Go/Rust tooling read
      # only these. ALL_PROXY is deliberately absent - clients that treat it as
      # a SOCKS URL break on an http:// value. NO_PROXY matches the host list.
      http_proxy = "http://127.0.0.1:33332/";
      https_proxy = "http://127.0.0.1:33332/";
      NO_PROXY = "127.0.0.1,localhost,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,192.168.1.1,*.local";
    };

    serviceConfig = {
      User = "hermes";
      Group = "hermes";
      EnvironmentFile = config.sops.templates."hermes.env".path;
      ProtectSystem = "strict";
      ProtectHome = lib.mkForce true;
      PrivateTmp = true;
      ProtectControlGroups = true;
      ProtectKernelModules = true;
      ProtectKernelTunables = true;
      ProtectKernelLogs = true;
      ProtectClock = true;
      SupplementaryGroups = [ "users" ];
    
      ReadWritePaths = [ 
        "/var/lib/hermes"
        "/nix/var/nix/profiles/per-user/hermes"
      ];
    
      ReadOnlyPaths = [
        "/nix/store"
        "-/etc/nix"
      ];

      RestrictAddressFamilies = [ 
        "AF_INET" 
        "AF_INET6" 
        "AF_UNIX" 
        "AF_NETLINK" 
      ];
    };
  };

  systemd.services.hermes-backend.serviceConfig = {
    ProtectHome = lib.mkForce true;

    RestrictAddressFamilies = [ 
      "AF_INET" 
      "AF_INET6" 
      "AF_UNIX" 
      "AF_NETLINK" 
    ];
  };
}

