{
  description = "NixOS Configuration";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixos-unstable";

    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    impermanence = {
      url = "github:nix-community/impermanence";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    home-manager = {
      url = "github:nix-community/home-manager/release-26.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    noctalia = {
      url = "github:noctalia-dev/noctalia/legacy-v4";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    nix-flatpak.url = "github:gmodena/nix-flatpak";

    hermes-agent.url = "github:NousResearch/hermes-agent";

  };

  outputs = { self, nixpkgs, disko, impermanence, home-manager, ... }@inputs:
  let
    system = "x86_64-linux";
  in {
    nixosConfigurations.lfour = nixpkgs.lib.nixosSystem {
      inherit system;

      specialArgs = { inherit inputs; };

      modules = [
        # System config
        ./system

        # Proper nixpkgs configuration module
        {
          nixpkgs = {
            overlays = [
              (import ./overlays)
            ];
            config.allowUnfree = true;
          };
        }

        # Home Manager Integration
        home-manager.nixosModules.home-manager
        {
          home-manager = {
            useGlobalPkgs = true;
            useUserPackages = true;
            backupFileExtension = "backup";

            extraSpecialArgs = {
              inherit inputs;

            };
            users.lfour = {
              imports = [ ./home ];
            };
          };
        }

        # File System
        disko.nixosModules.default
        impermanence.nixosModules.impermanence

        # Flatpak
        inputs.nix-flatpak.nixosModules.nix-flatpak

        # Hermes Agent
        inputs.hermes-agent.nixosModules.default
      ];
    };
  };
}

