{ inputs, ... }:

{
  imports = [
    # Modules
    inputs.noctalia.homeModules.default
    
    # Home-manager module
    ./home-manager.nix

    # Users config
    ./config
    
    # Users programs
    ./programs
    
    # Users packages
    ./userpkgs
  ];
}

