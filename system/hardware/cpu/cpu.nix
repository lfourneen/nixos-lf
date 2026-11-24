{ config, lib, ... }:

{
  hardware = {
  enableAllFirmware = true;
  acpilight.enable = true;
  cpu.amd.updateMicrocode = lib.mkDefault config.hardware.enableRedistributableFirmware;
  };
}

