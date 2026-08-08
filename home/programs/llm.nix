{ inputs, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config = {
      allowUnfree = true;
      cudaSupport = true;
    };
  };

in
{
  services.ollama = {
    enable = true;
    package = pkgs.ollama;
    acceleration = "cuda";
    host = "127.0.0.1";
    port = 11434;
    environmentVariables = {
      OLLAMA_ORIGINS = "http://localhost,http://127.0.0.1,https://localhost,https://127.0.0.1,chrome-extension://*,moz-extension://*,app://*";
    };
  };

  home.packages = with pkgs; [
    llama-cpp
    unstable-pkgs.lmstudio
  ];
}

