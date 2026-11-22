{ pkgs, ... }:

{
  programs.antigravity = {
    enable = true;
    package = pkgs.unstable.antigravity-ide;
  };

  programs.antigravity-cli = {
    enable = true;
    package = pkgs.unstable.antigravity-cli;
  };
}

