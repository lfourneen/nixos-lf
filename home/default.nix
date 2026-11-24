{ inputs, ... }:

{
  imports = [
    # Folders
    ./config
    ./programs
    ./userpkgs

    # Files
    ./home-manager.nix

    # External modules
    inputs.noctalia.homeModules.default
  ];
}

