{ ... }:

{
  # On a slow cold boot (DHCP/portal, slower sysinit) systemd prints "A start job
  # is running for <unit> ...", e.g. for the proxy-mode unit that loads the kill
  # switch; it is only console noise and clears by itself. `error` keeps boot
  # quiet except for real failures, so the red [FAILED] lines still show.
  boot.kernelParams = [ "systemd.show_status=error" ];

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

