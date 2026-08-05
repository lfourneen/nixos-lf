{ inputs, lib, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
in
{
  home.packages = with pkgs;[
    arduino-ide
    blender
    #davinci-resolve
    freecad
    kicad
    kicadAddons.kikit
    octaveFull
    #stm32cubemx
    unstable-pkgs.jetbrains.clion
    unstable-pkgs.jetbrains.pycharm
    unstable-pkgs.opencode
    #unstable-pkgs.unityhub
  ];
}

