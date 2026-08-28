{ inputs, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };

in
{
  programs.zed-editor = {
    enable = true;
    package = unstable-pkgs.zed-editor;

    extensions = [ 
      "nix"
    ];
  };
}

