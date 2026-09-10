{ pkgs, ... }:

{
  programs.opencode = {
    enable = true;
    web.enable = true;
    package = pkgs.unstable.opencode;
  };
}

