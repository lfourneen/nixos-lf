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
  services.llama-cpp = {
    package = unstable-pkgs.llama-cpp.override {
      cudaSupport = true;
    };
    enable = true;
    host = "127.0.0.1";
    port = "8080";
    model = "/path/to/your/model.gguf";
    extraFlags = [
      "--gpu-layers" "99"
      "--ctx-size" "8192"
      "--threads" "8"
      "--flash-attn"
      "--mlock"
      "--numa" "distribute"
      "--n-predict" "-1"
    ];
  };

  services.llama-swap = {
    package = unstable-pkgs.llama-swap;
    enable = true;
  };
}

