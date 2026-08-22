{ inputs, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };

in
{
  programs.zed-editor = {
    package = unstable-pkgs.zed-editor;
    enable = true;
    extensions = [ 
      "nix"
    ];
  };
}

