{ config, lib, pkgs, inputs, ... }:

let
  userName = config.my.machine.desktopUser;
  userPreferredLang = "Chinese";
  userRole = "Advanced NixOS User";

  # The pinned release lives in the flake input ref (flake.nix, tag vX.Y.Z);
  # read it back from there so the package version cannot drift from the tag.
  # Upstream ships `version = "0.0.0"` in pyproject.toml and its Nix build never
  # learns the git tag, so without this override the install stamp — and thus
  # `hermes --version` — reports v0.0.0.
  hermesVersion =
    let
      m = builtins.match ".*hermes-agent/v([0-9]+\\.[0-9]+\\.[0-9]+).*"
        (lib.findFirst
          (l: builtins.match ".*hermes-agent/v[0-9].*" l != null)
          ""
          (lib.splitString "\n" (builtins.readFile ../../../flake.nix)));
    in
    if m == null then
      throw "hermes.nix: cannot parse hermes-agent version from flake.nix (expected a '.../hermes-agent/vX.Y.Z' input URL)"
    else
      builtins.head m;

  # Seed for memories/USER.md; installed only if absent.
  hermesUserProfile = ''
    # User Profile
    Name: ${userName}
    Preferred Language: ${userPreferredLang}
    Role: ${userRole}
  '';
  userMdFile = pkgs.writeText "USER.md" hermesUserProfile;

  # Persona lives in sops (system/secrets/soul.yaml) and is installed by
  # hermes-agent's preStart; nothing in this file is world-readable.

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

  proxy = import ../../config/network/proxy/env.nix { inherit lib; };
  gostHttp = proxy.gostHttp config.my.machine.ports.gostHttp;
  gostHttpSlash = proxy.gostHttpSlash config.my.machine.ports.gostHttp;
  noProxy = proxy.noProxy {
    privateV4 = config.my.machine.privateV4;
    extra = [ "192.168.1.1" "*.local" ];
  };

  # Proxy env for MCP servers that reach the network. Hermes only passes the
  # env keys listed per-server (plus safe defaults) to the MCP subprocess, so
  # every network-facing server has to opt the proxy in explicitly.
  mcpProxyEnv = {
    HTTP_PROXY = gostHttp;
    HTTPS_PROXY = gostHttp;
    # Lowercase twins: some clients read only these.
    http_proxy = gostHttp;
    https_proxy = gostHttp;
    NO_PROXY = noProxy;
  };

  # Agent Mail (Agently / Tencent QQ Mail). The package and its `agently-check`
  # upstream-drift checker live in agently-cli.nix; the skill beside it is
  # installed into HERMES_HOME/skills/. Credentials land in /var/lib/hermes
  # (persisted), so one interactive `agently-cli auth login` as the hermes user
  # covers the service too.
  agently-cli = pkgs.callPackage ./agently-cli.nix { };
  agently-check = agently-cli.passthru.check;

  cfg = config.services.hermes-agent;

  # Shared state rendering runs without root privileges.
  common = import "${inputs.hermes-agent.outPath}/nix/moduleCommon.nix" { inherit lib; };

  stateDir = cfg.stateDir;
  hermesHome = "${stateDir}/.hermes";

  # Root-owned state check and layout creation. It only touches fixed entries
  # through directory descriptors with O_NOFOLLOW.
  stateGuard = "${pkgs.python3}/bin/python3 ${./hermes-state.py}";
  managedStateFiles = builtins.attrNames cfg.hermesHomeFiles ++ [ "skills/sandbox/SKILL.md" ];
  managedFileArgs = lib.concatMapStrings (name: " --managed-file ${lib.escapeShellArg name}") managedStateFiles;

  # The files that upstream writes into HERMES_HOME. It runs as the hermes
  # user, so it cannot chown and cannot modify the state root itself.
  hermesStateScript = pkgs.writeShellScript "hermes-state-files" ''
    set -eu
    export PATH=${lib.makeBinPath [ pkgs.coreutils pkgs.findutils ]}:$PATH
    ${common.mkStateScript {
      inherit pkgs cfg;
      hermesHome = hermesHome;
      workingDirectory = cfg.workingDirectory;
      owner = null;
      modes = {
        config = "0660";
        env = "0600";
        managed = "0644";
        auth = "0600";
        document = "0640";
      };
      stateDirs = [ ];
    }}
  '';

  # Writable state for the service. The state root, the backup directory and
  # the version marker stay read-only, so the service cannot rename or replace
  # the entries that activation verifies.
  writablePaths = [
    "${stateDir}/.hermes"
    "${stateDir}/home"
    "${stateDir}/workspace"
    "${stateDir}/.config"
    "${stateDir}/.local"
    "${stateDir}/.cache"
    "${stateDir}/.playwright-profiles"
    "/nix/var/nix/profiles/per-user/hermes"
  ];

in
{
  assertions = [
    {
      assertion = !cfg.container.enable;
      message = "system/programs/hermes/hermes.nix replaces the hermes-agent activation script and supports native mode only. Review this module before enabling services.hermes-agent.container.enable.";
    }
    {
      assertion = cfg.container.hostUsers == [ ];
      message = "system/programs/hermes/hermes.nix does not create the container host-user symlink bridge. Review this module before setting services.hermes-agent.container.hostUsers.";
    }
    {
      assertion = cfg.documents == { };
      message = "system/programs/hermes/hermes.nix does not install documents as root. Review this module before setting services.hermes-agent.documents.";
    }
    {
      assertion = cfg.authFile == null;
      message = "system/programs/hermes/hermes.nix does not install authFile as root. Review this module before setting services.hermes-agent.authFile.";
    }
    {
      assertion = cfg.extraPlugins == [ ];
      message = "system/programs/hermes/hermes.nix does not link extraPlugins as root. Review this module before setting services.hermes-agent.extraPlugins.";
    }
  ];

  imports = [
    # Fixed non-recursive modes take precedence over the upstream defaults.
    {
      systemd.tmpfiles.rules = lib.mkBefore [
        "d ${stateDir} 0750 root hermes - -"
        "d ${stateDir}/.hermes 0700 hermes hermes - -"
        "d ${stateDir}/home 0700 hermes hermes - -"
        "d ${stateDir}/workspace 2770 hermes hermes - -"
        "d ${stateDir}/.config 0700 hermes hermes - -"
        "d ${stateDir}/.local 0700 hermes hermes - -"
        "d ${stateDir}/.cache 0700 hermes hermes - -"
        "d ${stateDir}/.playwright-profiles 0700 hermes hermes - -"
        "d ${hermesHome}/cron 0700 hermes hermes - -"
        "d ${hermesHome}/sessions 0700 hermes hermes - -"
        "d ${hermesHome}/logs 0700 hermes hermes - -"
        "d ${hermesHome}/memories 0700 hermes hermes - -"
        "d ${hermesHome}/plugins 0700 hermes hermes - -"
        "d ${hermesHome}/skills 0700 hermes hermes - -"
        "d /nix/var/nix/profiles/per-user/hermes 0755 hermes hermes - -"
      ];
    }
  ];

  services.hermes-agent = {
    enable = true;
    package = inputs.hermes-agent.packages.${pkgs.stdenv.hostPlatform.system}.default.override {
      version = hermesVersion;
    };
    addToSystemPackages = true;
    restart = "always";
    restartSec = 5;

    # QQ Bot adapter needs aiohttp (and httpx, already a core dep).
    extraDependencyGroups = [ "messaging" ];

    environmentFiles = [ config.sops.templates."hermes.env".path ];

    workingDirectory = "${stateDir}/workspace";

    backend = {
      mode = "dashboard";
      port = 18432;
    };

    hermesHomeFiles = {
      # SOUL.md is not managed here: it comes from sops and is installed by
      # hermes-agent's preStart. memories/USER.md is Hermes runtime state.

      # Agently Mail skill: HERMES_HOME/skills/<name>/SKILL.md is indexed into
      # the system prompt and loaded on demand.
      "skills/agently-mail/SKILL.md" = ./hermes-skills/agently-mail/SKILL.md;
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
        cwd = "${stateDir}/workspace";
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

      # Disable the unattended post-turn review fork
      auxiliary.background_review = {
        enabled = false;
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
          env = mcpProxyEnv;
        };

        filesystem = {
          command = "${pkgs.mcp-server-filesystem}/bin/mcp-server-filesystem";
          args = [ "${stateDir}/workspace" "${stateDir}/home" "/tmp" ];
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

        # Up-to-date library/framework docs (no API key required).
        context7 = {
          command = "${lib.getExe pkgs.context7-mcp}";
          args = [ ];
          env = mcpProxyEnv;
        };

        # Convert any document to markdown; may fetch URLs, so needs the proxy.
        markitdown = {
          command = "${lib.getExe pkgs.markitdown-mcp}";
          args = [ ];
          env = mcpProxyEnv;
        };

        # Large-PDF chunked search (bundles tesseract for scanned pages).
        # pdf = {
        #   command = "${lib.getExe pkgs.unstable.pdf-mcp}";
        #   args = [ ];
        # };

        # Knowledge-graph memory (complements the holographic memory plugin).
        memory = {
          command = "${lib.getExe pkgs.mcp-server-memory}";
          args = [ ];
          env = {
            MEMORY_FILE_PATH = "${hermesHome}/memory.json";
          };
        };

        # git = {
        #   command = "${lib.getExe pkgs.mcp-server-git}";
        #   args = [ "--repository" "${stateDir}/workspace" ];
        # };

        # Web search, no API key. open-websearch intentionally ignores the
        # HTTP_PROXY env vars and uses USE_PROXY/PROXY_URL instead; SEARCH_MODE
        # is pinned to "request" so it never tries to download Playwright.
        open-websearch = {
          command = "${lib.getExe pkgs.unstable.open-websearch}";
          args = [ ];
          env = {
            MODE = "stdio";
            DEFAULT_SEARCH_ENGINE = "duckduckgo";
            SEARCH_MODE = "request";
            USE_PROXY = "true";
            PROXY_URL = gostHttp;
          };
        };

        # agent-browser (vercel-labs) as an MCP server, driving the Nix
        # Chromium through gost. --no-sandbox is required because the service
        # sandbox blocks Chrome's setuid/user-namespace sandbox.
        agent-browser = {
          command = "${lib.getExe pkgs.unstable.agent-browser}";
          args = [ "mcp" ];
          env = {
            AGENT_BROWSER_EXECUTABLE_PATH = "${lib.getExe pkgs.chromium}";
            AGENT_BROWSER_PROXY = gostHttp;
            AGENT_BROWSER_PROXY_BYPASS = "localhost,127.0.0.1,::1";
            AGENT_BROWSER_ARGS = "--no-sandbox";
          };
        };

        playwright = {
          command = "${pkgs.playwright-mcp}/bin/playwright-mcp";
          args = [
            "--executable-path"
            "${pkgs.playwright-driver.browsers}/chromium-${pkgs.playwright-driver.passthru.browsersJSON.chromium.revision}/chrome-linux64/chrome"
            "--proxy-server"
            "${gostHttp}"
          ];
          env = {
            PWMCP_PROFILES_DIR_FOR_TEST = "${stateDir}/.playwright-profiles";
          };
        };
      };

      security = {
        allowed_users = [ config.my.machine.desktopUser ];

        # Fail closed: a broken/timed-out scanner blocks instead of allowing.
        tirith_fail_open = false;

        # No on-demand pip/npm installs (supply-chain surface; Nix owns deps).
        allow_lazy_installs = false;
      };

      # Group ids come from QQ_GROUP_ALLOWED_USERS (sops): public repo, other people's groups.
      platforms.qqbot = {
        enabled = true;
        group_policy = "allowlist";
      };

      toolsets = [ "all" ];

      plugins.hermes-memory-store = {
        db_path = "${hermesHome}/memory_store.db";
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
      python313Packages.numpy
      uv

      # Browser automation tooling
      chromium
      pkgs.unstable.agent-browser

      # Code intelligence
      ast-grep
      repomix
      difftastic
      tokei
      hyperfine

      # Data processing
      duckdb
      qsv
      csvkit
      miller

      # Document & media
      typst
      qpdf
      exiftool
      tesseract
      ffmpeg
      yt-dlp

      # Web & markup parsing
      htmlq
      xmlstarlet
      xh

      # Security scanning & sandbox
      semgrep
      gitleaks
      trufflehog
      nono

      # Terminal UX
      bat
      glow
      zoxide
      fzf

      # Agent Mail (Agently / Tencent QQ Mail)
      agently-cli
      agently-check
    ];
  };

  users.users.hermes.createHome = lib.mkForce false;

  environment.systemPackages = [
    agently-cli
    agently-check
  ];

  # Activation validates fixed state paths before rendering as the service user.
  system.activationScripts."hermes-agent-setup" = lib.mkForce (
    lib.stringAfter
      ([ "users" ] ++ lib.optional (config.system.activationScripts ? setupSecrets) "setupSecrets")
      ''
        (
          set -eu
          ${stateGuard} --root ${stateDir} --user ${cfg.user} --group ${cfg.group}${managedFileArgs}
          ${lib.getExe' pkgs.util-linux "setpriv"} \
            --reuid=${cfg.user} --regid=${cfg.group} --init-groups \
            ${hermesStateScript}
        )
      ''
  );

  # Seed memories/USER.md only if missing; Hermes owns it after.
  systemd.services.hermes-user-profile-seed = {
    description = "Seed Hermes memories/USER.md if missing";
    wantedBy = [ "multi-user.target" ];
    before = [ "hermes-agent.service" ];
    unitConfig.ConditionPathExists = "!${hermesHome}/memories/USER.md";
    serviceConfig = {
      Type = "oneshot";
      User = "hermes";
      Group = "hermes";
      ExecStart = "${pkgs.coreutils}/bin/install -D -m 0640 ${userMdFile} ${hermesHome}/memories/USER.md";
    };
  };

  systemd.services.hermes-agent = {
    # SOUL.md comes from sops: copy it in before the gateway reads it. An empty
    # render (secret still unfilled) leaves the existing file untouched.
    # The dbus rebind covers a recreated runtime dir (cron dispatch needs it).
    preStart = lib.mkBefore ''
      if [ -s /run/secrets/rendered/SOUL.md ]; then
        ${pkgs.coreutils}/bin/install -m 0640 /run/secrets/rendered/SOUL.md "$HERMES_HOME/SOUL.md"
      fi
      if [ -s /run/secrets/rendered/sandbox.md ]; then
        ${pkgs.coreutils}/bin/install -D -m 0640 /run/secrets/rendered/sandbox.md \
          "$HERMES_HOME/skills/sandbox/SKILL.md"
      fi
      export XDG_RUNTIME_DIR="/run/user/$(id -u)"
      if [ ! -S "$XDG_RUNTIME_DIR/bus" ]; then
        ${config.systemd.package}/bin/systemctl --user restart dbus.socket >/dev/null 2>&1 || true
        ${config.systemd.package}/bin/systemctl --user restart dbus-broker.service >/dev/null 2>&1 || true
      fi
    '';

    # preStart reads the rendered secrets, so they must exist first.
    after = lib.mkAfter [ "sops-install-secrets.service" ];
    wants = lib.mkAfter [ "sops-install-secrets.service" ];

    # Route the agent's model/API calls through gost-relay so it keeps working
    # under `proxyKillSwitch` (which blocks non-root direct egress).
    environment = {
      HTTP_PROXY = gostHttpSlash;
      HTTPS_PROXY = gostHttpSlash;

      # Lowercase twins: python-requests/httpx and some Go/Rust tooling read
      # only these. ALL_PROXY is deliberately absent - clients that treat it as
      # a SOCKS URL break on an http:// value. NO_PROXY matches the host list.
      http_proxy = gostHttpSlash;
      https_proxy = gostHttpSlash;
      NO_PROXY = noProxy;
    };

    serviceConfig = {
      User = "hermes";
      Group = "hermes";
      Slice = "hermes.slice";
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
      ReadWritePaths = lib.mkForce writablePaths;
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
    Slice = "hermes.slice";
    ProtectHome = lib.mkForce true;
    ReadWritePaths = lib.mkForce writablePaths;
    RestrictAddressFamilies = [
      "AF_INET"
      "AF_INET6"
      "AF_UNIX"
      "AF_NETLINK"
    ];
  };
}

