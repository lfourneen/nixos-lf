{ inputs, lib, pkgs, ... }:

let
  unstable-pkgs = import inputs.nixpkgs-unstable {
    system = pkgs.stdenv.hostPlatform.system;
    config.allowUnfree = true;
  };
in
{
  home.packages = with pkgs;[
    #discord
    #unstable-pkgs.wechat-uos 
    #unstable-pkgs.wemeet
    telegram-desktop
    #(unstable-pkgs.qq.override {
      #commandLineArgs = [
        #"--ozone-platform=wayland"
        #"--enable-wayland-ime"
        #"--wayland-text-input-version=3"
      #];
    #})
  ];
}

