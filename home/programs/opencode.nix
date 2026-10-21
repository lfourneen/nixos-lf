{ pkgs, ... }:

{
  programs.opencode = {
    enable = true;
    web.enable = true;
    package = pkgs.unstable.opencode;
  };

  home.packages = with pkgs;[
    pkgs.unstable.opencode-desktop
  ];
}

