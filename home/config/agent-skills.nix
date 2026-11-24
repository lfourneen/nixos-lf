{ config, lib, pkgs, ... }:

let
  # Referenced relative to this file, so the flake source (/nix/store/…-source)
  # carries the skill and the same expression works from a checkout and from
  # /etc/nixos after scripts/push-to-dir.sh. Editing the skill ships with the
  # next rebuild, like every other file in this repo.
  skills = {
    safe-delete = ./agent-skills/safe-delete;
    safe-process-stop = ./agent-skills/safe-process-stop;
    nix-missing-tool = ./agent-skills/nix-missing-tool;
  };
  skillRoots = [
    ".config/opencode/skill"
    ".dsh/skills"
    ".codex/skills"
  ];
  home = config.home.homeDirectory;
  storeSource = name: source: builtins.path {
    path = source;
    name = config.lib.strings.storeFileName name;
  };
  managed = lib.mapAttrs storeSource skills;
  auditSettings = pkgs.writeText "desktop-skill-paths.json" (builtins.toJSON {
    inherit home;
    roots = lib.genAttrs (map (root: "${home}/${root}") skillRoots) (_: {
      links = managed;
    }) // lib.genAttrs [
      "${home}/.agents/skills"
      "${home}/.local/share/dsh-tui/skills"
      "${home}/.config/opencode/skills"
      "${home}/.claude/skills"
      "/etc/codex/skills"
      "/etc/opencode/skills"
    ] (_: { });
    names = builtins.attrNames skills;
    projectRoots = [ ".agents/skills" ".dsh/skills" ".claude/skills" ".opencode/skill" ".opencode/skills" ];
    environment = {
      CODEX_HOME = "${home}/.codex";
      DSH_HOME = "${home}/.dsh";
      DSH_AGENTS_HOME = "${home}/.agents";
      DSH_TUI_HOME = "${home}/.local/share/dsh-tui";
      XDG_CONFIG_HOME = "${home}/.config";
      XDG_DATA_HOME = "${home}/.local/share";
      OPENCODE_CONFIG_DIR = "${home}/.config/opencode";
    };
  });
  python = pkgs.python3.withPackages (ps: [ ps.pyyaml ]);

in
{
  # Each client points to the same store source, without a dependency on
  # another client's mutable local skill directory.
  home.file = lib.listToAttrs (lib.concatMap
    (root: lib.mapAttrsToList (name: source: {
      name = "${root}/${name}";
      value = { inherit source; };
    }) managed)
    skillRoots);
  home.packages = [ (pkgs.writeShellScriptBin "skill-path-audit" ''
    exec ${python}/bin/python3 ${./skill-path-audit.py} ${auditSettings} "$@"
  '') ];
}
