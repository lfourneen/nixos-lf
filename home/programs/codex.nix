{ config, pkgs, lib, ... }:

let
  codexConfig = config.home.file.".codex/config.toml".source;

in
{
  programs.codex = {
    enable = true;
    package = pkgs.unstable.codex;
    enableMcpIntegration = true;
    settings = {
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

