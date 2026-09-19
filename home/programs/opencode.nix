{ pkgs, ... }:

{
  programs.opencode = {
    enable = true;
    web.enable = true;
    package = pkgs.opencode;
  };

  home.packages = with pkgs;[
    #opencode-desktop
  ];
}

