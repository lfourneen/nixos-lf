{ pkgs, ... }:

{
  #enable bluetooth
  hardware.bluetooth = {
    enable = true;
    powerOnBoot = true;
    settings = {
      General = {
        Experimental = true;
        FastConnectable = true;
      };
      Policy = {
        AutoEnable = true;
      };
    };
  };
  hardware.enableRedistributableFirmware = true;
  services.blueman.enable = true;
  boot.kernelModules = [ "btusb" "bluetooth" ];
  environment.systemPackages = with pkgs; [
    blueman bluez bluez-tools
  ];
}

