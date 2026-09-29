{ config, pkgs, lib, osConfig, ... }:

let
  # DeepSeek key from sops, injected at runtime (never written to the store).
  deepseekKeyFile = lib.attrByPath
    [ "sops" "secrets" "deepseek-codex-api-key" "path" ]
    "/run/secrets/deepseek-codex-api-key"
    osConfig;

  codex = pkgs.symlinkJoin {
    name = "codex-${pkgs.unstable.codex.version}";
    inherit (pkgs.unstable.codex) version;
    paths = [ pkgs.unstable.codex ];
    nativeBuildInputs = [ pkgs.makeWrapper ];
    postBuild = ''
      wrapProgram $out/bin/codex \
        --run 'if [ -r ${deepseekKeyFile} ]; then export DEEPSEEK_API_KEY="$(cat ${deepseekKeyFile})"; fi'
    '';
  };

  codexConfig = config.home.file.".codex/config.toml".source;

in
{
  programs.codex = {
    enable = true;
    package = codex;
    enableMcpIntegration = true;
    settings = {
      model = "deepseek-flash";
      model_provider = "deepseek";
      model_providers = {
        deepseek = {
          name = "DeepSeek";
          base_url = "https://api.deepseek.com/v1";
          env_key = "DEEPSEEK_API_KEY";
          wire_api = "responses";
        };
      };
      mcp_servers = {
        mcp-nixos = {
          command = "mcp-nixos";
        };
      };
    };
  };

  # Install a writable config.toml (Nix settings merged at activation) so Codex
  # can persist project trust; see home-manager#9397.
  home.file.".codex/config.toml".enable = lib.mkForce false;

  home.activation.codex-config = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
    target="$HOME/.codex/config.toml"
    base=${codexConfig}
    run mkdir -p "$HOME/.codex"
    if [ -e "$target" ] || [ -L "$target" ]; then
      ${pkgs.yq-go}/bin/yq eval-all -p toml -o toml \
        '. as $i ireduce ({}; . * $i)' "$target" "$base" > "$target.tmp"
      run mv -f "$target.tmp" "$target"
    else
      run cp "$base" "$target"
    fi
    run chmod u+w "$target"
  '';
}

