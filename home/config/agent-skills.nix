{ lib, ... }:

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

in
{
  # Each client points to the same store source, without a dependency on
  # another client's mutable local skill directory.
  home.file = lib.listToAttrs (lib.concatMap
    (root: lib.mapAttrsToList (name: source: {
      name = "${root}/${name}";
      value = { inherit source; };
    }) skills)
    skillRoots);
}
