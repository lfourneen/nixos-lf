{ pkgs, ... }:

{
  programs.zed-editor = {
    enable = true;
    package = pkgs.unstable.zed-editor;

    extensions = [
      # Languages & Frameworks
      "assembly"
      "csharp"
      "lua"
      "nix"
      "nu"
      "php"
      "qml"
      "verilog"
      "vhdl"
      "vue"
      "zig"

      # Web
      "html"
      "xml"

      # Build & Tooling
      "dockerfile"
      "make"
      "meson"
      "neocmake"

      # Docs, Data & Markup
      "asciidoc"
      "latex"
      "markdown-oxide"
      "matlab"
      "sql"
      "toml"

      # Snippets
      "csharp-snippets"
      "go-snippets"
      "html-snippets"
      "javascript-snippets"
      "latex-snippets"
      "python-snippets"
      "react-typescript-snippets"
      "rust-snippets"
      "typescript-snippets"

      # Git
      "git-firefly"

      # Themes & Icons
      "jetbrains-themes"
      "material-icon-theme"

      # MCP servers
      "mcp-server-context7"
      "mcp-server-github"
      "mcp-server-playwright"
    ];

    userSettings = {
      calls = {
        mute_on_join = true;
      };

      proxy = "127.0.0.1:33332";

      agent_ui_font_family = "Maple Mono NF CN";
      buffer_font_family = "Maple Mono NF CN";

      terminal = {
        font_family = "Maple Mono NF CN";
      };

      collaboration_panel = {
        dock = "left";
      };

      agent = {
        default_model = {
          provider = "deepseek";
          model = "deepseek-flash";
          enable_thinking = true;
          effort = "high";
        };
        sidebar_side = "right";
        dock = "right";
        favorite_models = [ ];
        model_parameters = [ ];
      };

      git_panel = {
        dock = "left";
      };

      project_panel = {
        dock = "left";
      };

      bottom_dock_layout = "left_aligned";

      icon_theme = "Material Icon Theme";

      telemetry = {
        diagnostics = false;
        metrics = false;
        anthropic_retention = false;
      };

      theme = {
        mode = "dark";
        light = "Ayu Light";
        dark = "JetBrains Islands Dark";
      };

      languages = {
        Nix = {
          # Use nixd only; the nix extension also probes for `nil` otherwise.
          language_servers = [
            "nixd"
            "!nil"
          ];
        };
      };
    };
  };

  # Nix language server (nixd) required by Zed's Nix extension.
  home.packages = [ pkgs.nixd ];
}

