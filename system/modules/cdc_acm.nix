{ pkgs, ... }:

{
  boot.kernelModules = [ "cdc_acm" ];

  services.udev.extraRules = ''
  ACTION=="add", SUBSYSTEM=="usb", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="55d2", \
  RUN+="${pkgs.bash}/bin/sh -c 'echo 1a86 55d2 > /sys/bus/usb/drivers/cdc_acm/new_id'"
'';
}

