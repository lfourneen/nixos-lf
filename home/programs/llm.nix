{ inputs, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
in
{
  services.ollama = {
    enable = true;
    acceleration = "cuda";
    environmentVariables = {
      OLLAMA_ORIGINS = "localhost,127.0.0.1";
    };
  };

  home.packages = with pkgs; [
    unstable-pkgs.llama-cpp 
    unstable-pkgs.lmstudio
  ];
}

