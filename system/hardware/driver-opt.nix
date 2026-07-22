{ ... }:

{
  # Hardware
  hardware = {
    acpilight.enable = true;
    brillo.enable = true;
    #parallels.enable = true;
    ksm.enable = true;
    #sensor.hddtemp = {
      #enable = true;
      #drives = [ "/dev/sda" ];
    #};
    steam-hardware.enable = true;
  };
}
