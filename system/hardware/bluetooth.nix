{ pkgs, ... }:

{
  #enable bluetooth
  hardware.bluetooth = {
    enable = true;
    # Off at boot; only on when explicitly enabled (AutoEnable follows this).
    powerOnBoot = false;

    settings = {
      General = {
        # Keep true for LE Audio; set false to cut experimental surface.
        Experimental = true;
        FastConnectable = false;        # don't stay passively connectable
        Privacy = "device";             # LE resolvable private addresses
        JustWorksRepairing = "never";   # no silent re-pairing
      };
    };
  };

  hardware.enableRedistributableFirmware = true;

  services.blueman.enable = true;

  boot.kernelModules = [ 
    "btusb" 
    "bluetooth" 
  ];

  environment.systemPackages = with pkgs; [
    blueman 
    bluez 
    bluez-tools
  ];
}

