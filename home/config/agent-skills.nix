{ ... }:

let
  # Referenced relative to this file, so the flake source (/nix/store/…-source)
  # carries the skill and the same expression works from a checkout and from
  # /etc/nixos after scripts/push-to-dir.sh. Editing the skill ships with the
  # next rebuild, like every other file in this repo.
  safeDelete = ./agent-skills/safe-delete;

in
{
  home.file = {
    # Three agent CLIs, one source file: ~/.dsh/skills and ~/.codex/skills
    # already mirror ~/.config/opencode/skill, so keep the same convention
    # for the skill this repo owns.
    ".config/opencode/skill/safe-delete".source = safeDelete;
    ".dsh/skills/safe-delete".source = safeDelete;
    ".codex/skills/safe-delete".source = safeDelete;
  };
}
