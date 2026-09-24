{ ... }:

{
  # Bootloader
  boot.loader = {
    systemd-boot.enable = true;
    efi.canTouchEfiVariables = true;

    # Keep only recent generations, otherwise the ESP fills up and rebuilds fail.
    systemd-boot.configurationLimit = 3;

    # Physical access must not get a root shell by editing the boot cmdline.
    #systemd-boot.editor = false;
  };
}

