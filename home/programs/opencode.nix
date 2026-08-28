{ inputs, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };

in
{
  programs.opencode = {
    enable = true;
    web.enable = true;
    package = unstable-pkgs.opencode;
  };
}

