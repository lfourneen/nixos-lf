{ pkgs, ... }:

let
  # Sort after 50-udev-default.rules (which sets ID_USB_INTERFACES) and before
  # 73-seat-late.rules (which applies uaccess).
  deviceRules = pkgs.writeTextDir "lib/udev/rules.d/61-local-devices.rules" ''
    # CMSIS-DAP (W-ELEMENTS 0483:572a)
    SUBSYSTEM=="usb", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="572a", MODE="0660", GROUP="plugdev", TAG+="uaccess"
    KERNEL=="hidraw*", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="572a", MODE="0660", GROUP="plugdev", TAG+="uaccess"

    # ST-Link / V2 / V2-1 / V3
    SUBSYSTEM=="block", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="5720", TAG+="uaccess", GROUP="plugdev", MODE="0660"
    ATTRS{idVendor}=="0483", ATTRS{idProduct}=="3748", TAG+="uaccess"
    ATTRS{idVendor}=="0483", ATTRS{idProduct}=="374b", TAG+="uaccess"
    ATTRS{idVendor}=="0483", ATTRS{idProduct}=="3752", TAG+="uaccess"

    # FTDI serial adapters
    SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", TAG+="uaccess"

    # CP210x (Silabs)
    ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", TAG+="uaccess"
    ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea70", TAG+="uaccess"

    # CH340/CH341
    ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7522", TAG+="uaccess", GROUP="plugdev", MODE="0660"
    ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", TAG+="uaccess", GROUP="plugdev", MODE="0660"
    ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="5523", TAG+="uaccess", GROUP="plugdev", MODE="0660"

    # Prolific PL2303
    ATTRS{idVendor}=="067b", ATTRS{idProduct}=="2303", TAG+="uaccess"

    # J-Link
    ATTRS{idVendor}=="1366", ATTRS{idProduct}=="0101", TAG+="uaccess"
    ATTRS{idVendor}=="1366", ATTRS{idProduct}=="0105", TAG+="uaccess"

    # Altera USB Blaster
    ATTRS{idVendor}=="09fb", ATTRS{idProduct}=="6001", TAG+="uaccess"

    # ADB interface; adbusers covers headless use, uaccess covers the active seat.
    SUBSYSTEM=="usb", ENV{ID_USB_INTERFACES}=="*:ff4201:*", MODE="0660", GROUP="adbusers", TAG+="uaccess"

    # Proton uses ntsync; plugdev and the active seat get rw access to the node.
    KERNEL=="ntsync", MODE="0660", GROUP="plugdev", TAG+="uaccess"

    # Grant the active local session access before 73-seat-late.rules runs uaccess.
    SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="0547", ATTR{idProduct}=="1002", MODE="0660", TAG+="uaccess"
  '';

in
{
  services.udev.packages = [ deviceRules ];

  # Extra rules (special cases)
  services.udev.extraRules = ''
    # When a CPU device is added, set the frequency modulation write interface to read-only
    SUBSYSTEM=="cpu", ACTION=="add", RUN+="${pkgs.coreutils}/bin/chmod 444 /sys$devpath/cpufreq/scaling_setspeed"

    # Hwmon pwm
    SUBSYSTEM=="hwmon", KERNEL=="pwm*", MODE="0444"
  '';
}
