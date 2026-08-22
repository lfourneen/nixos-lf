{ pkgs, ... }:

{
  boot.kernelModules = [ "i2c-dev" ];
  hardware.i2c.enable = true;
  environment.systemPackages = [ pkgs.ddcutil ];
}

