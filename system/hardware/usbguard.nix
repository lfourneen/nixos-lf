{ pkgs, ... }:

{
  # Kernel-side fail-closed: external (removable) USB stays unauthorized by
  # default even if the USBGuard daemon is down; internal fixed devices
  # (keyboard/camera/BT) remain authorized so there is no lockout.
  boot.kernelParams = [ "usbcore.authorized_default=2" ];

  # USBGuard: hash-pinned allowlist of current devices (generate-policy).
  # Boot-time devices stay allowed (no lockout); hotplug is default-deny.
  # USE: usbguard list-devices / usbguard allow-device <id>
  # Regenerate: nix shell nixpkgs#usbguard -c usbguard generate-policy
  services.usbguard = {
    enable = true;
    dbus.enable = true;

    presentDevicePolicy = "allow";
    presentControllerPolicy = "allow";
    insertedDevicePolicy = "apply-policy";
    implicitPolicyTarget = "block";

    # root is required for usbguard-dbus; lfour for interactive use.
    IPCAllowedUsers = [ "root" "lfour" ];

    rules = ''
      allow id 1d6b:0002 serial "0000:06:00.3" name "xHCI Host Controller" hash "+0s5mKAEDBjZasKfFR9ExKfjpMr/J4C4yq4bgYdLJSM=" parent-hash "KTj0i1ONjkGo2CJx42BsIwl+RMi6YVks67qrDYNrwPo=" with-interface 09:00:00 with-connect-type ""
      allow id 1d6b:0003 serial "0000:06:00.3" name "xHCI Host Controller" hash "mY33/owHioZsFcJ98I14TeQ+B9tqBjX0nE9Ogy2f4Hk=" parent-hash "KTj0i1ONjkGo2CJx42BsIwl+RMi6YVks67qrDYNrwPo=" with-interface 09:00:00 with-connect-type ""
      allow id 1d6b:0002 serial "0000:06:00.4" name "xHCI Host Controller" hash "TaKoMrgQrk94nyzpOQk+iNVB0H+ZSnYN/X7lY+QzAn0=" parent-hash "Uk+Btxo+I+tLT7+0bnE1NSmtnqPXUzmSqOXpyVHnU+o=" with-interface 09:00:00 with-connect-type ""
      allow id 1d6b:0003 serial "0000:06:00.4" name "xHCI Host Controller" hash "loJYlkuv5WE4gGiX77MET1+gKtJiS8m4G4M0xhh1V6M=" parent-hash "Uk+Btxo+I+tLT7+0bnE1NSmtnqPXUzmSqOXpyVHnU+o=" with-interface 09:00:00 with-connect-type ""
      allow id 1d6b:0002 serial "0000:08:00.4" name "xHCI Host Controller" hash "p5Lcn0WXdgC5G1Nqx+3BPaBV640ADCPXVEDSfrZYS7A=" parent-hash "Nu8J974UEScRIxgDtIYEO/fbfHHA9FTTU20Vm4kojYg=" with-interface 09:00:00 with-connect-type ""
      allow id 1d6b:0003 serial "0000:08:00.4" name "xHCI Host Controller" hash "JhF1nswb/C/Ap7lv5aIK+3D1wkfY8xHkdkai55x6Z2c=" parent-hash "Nu8J974UEScRIxgDtIYEO/fbfHHA9FTTU20Vm4kojYg=" with-interface 09:00:00 with-connect-type ""
      allow id 0bda:5411 serial "" name "USB2.1 Hub" hash "g/+iB1DQ0KhZLxPXGjcKbQj2R3XydzpZT6lp+326KOc=" parent-hash "+0s5mKAEDBjZasKfFR9ExKfjpMr/J4C4yq4bgYdLJSM=" via-port "1-2" with-interface { 09:00:01 09:00:02 } with-connect-type "hardwired"
      allow id 5986:118a serial "0001" name "Integrated Camera" hash "iEW7MCs5ek/X8OX5AB2aGBvw/r0Lc/A4D2kxH/uEqxI=" parent-hash "+0s5mKAEDBjZasKfFR9ExKfjpMr/J4C4yq4bgYdLJSM=" with-interface { 0e:01:01 0e:02:01 0e:02:01 0e:02:01 0e:02:01 0e:02:01 0e:02:01 0e:02:01 0e:02:01 fe:01:01 } with-connect-type "not used"
      allow id 048d:c102 serial "" name "ITE Device(8910)" hash "SQdPOlghoqGFVkLdvxWN8X5OFttgq4HK1DaZkMI0vAQ=" parent-hash "+0s5mKAEDBjZasKfFR9ExKfjpMr/J4C4yq4bgYdLJSM=" via-port "1-4" with-interface 03:01:01 with-connect-type "not used"
      allow id 0489:e0cd serial "000000000" name "Wireless_Device" hash "3JopVFWGRS5OUECbrpyI91sYwRcWP7uB1x2MwHhAtnM=" parent-hash "+0s5mKAEDBjZasKfFR9ExKfjpMr/J4C4yq4bgYdLJSM=" with-interface { e0:01:01 e0:01:01 e0:01:01 e0:01:01 e0:01:01 e0:01:01 e0:01:01 e0:01:01 e0:01:01 e0:01:01 } with-connect-type "not used"
      allow id 0bda:0411 serial "" name "USB3.2 Hub" hash "4E/UMyvBMITEgJDI7m7ihv6v1k5sVxc4/8JOlWhR3HA=" parent-hash "mY33/owHioZsFcJ98I14TeQ+B9tqBjX0nE9Ogy2f4Hk=" via-port "2-2" with-interface 09:00:00 with-connect-type "hardwired"
      allow id 05e3:0610 serial "" name "USB2.1 Hub" hash "CbRB9LX/JdGjNWCYSOcIwMVXE0UpOR03LCotWrTbuCM=" parent-hash "g/+iB1DQ0KhZLxPXGjcKbQj2R3XydzpZT6lp+326KOc=" via-port "1-2.1" with-interface 09:00:00 with-connect-type "hotplug"
      allow id 05e3:0626 serial "" name "USB3.1 Hub" hash "15SBGsOo8K+JjtOKSCn7t0i6ifer4wmhzep1yEB5pLQ=" parent-hash "4E/UMyvBMITEgJDI7m7ihv6v1k5sVxc4/8JOlWhR3HA=" via-port "2-2.1" with-interface 09:00:00 with-connect-type "hotplug"
      allow id 05e3:0610 serial "" name "USB2.1 Hub" hash "CbRB9LX/JdGjNWCYSOcIwMVXE0UpOR03LCotWrTbuCM=" parent-hash "CbRB9LX/JdGjNWCYSOcIwMVXE0UpOR03LCotWrTbuCM=" via-port "1-2.1.2" with-interface 09:00:00 with-connect-type "unknown"
      allow id 0a67:d077 serial "AP5980_20220308" name "XIBERIA S31U" hash "qpV7xvCYB1OWrCY1FbcpZYuBZMZdMhQRI4ME0WwX7LI=" parent-hash "CbRB9LX/JdGjNWCYSOcIwMVXE0UpOR03LCotWrTbuCM=" with-interface { 01:01:00 01:02:00 01:02:00 01:02:00 01:02:00 01:02:00 01:02:00 03:00:00 } with-connect-type "unknown"
      allow id 05e3:0626 serial "" name "USB3.1 Hub" hash "15SBGsOo8K+JjtOKSCn7t0i6ifer4wmhzep1yEB5pLQ=" parent-hash "15SBGsOo8K+JjtOKSCn7t0i6ifer4wmhzep1yEB5pLQ=" via-port "2-2.1.2" with-interface 09:00:00 with-connect-type "unknown"
      allow id 3554:fa09 serial "" name "2.4G Wireless Receiver" hash "y06wIzkEZxKsXnv5jEWfKMHIY1cRecYI78733ZfXdZk=" parent-hash "CbRB9LX/JdGjNWCYSOcIwMVXE0UpOR03LCotWrTbuCM=" via-port "1-2.1.2.2" with-interface { 03:01:01 03:01:02 } with-connect-type "unknown"
      allow id 373b:11fe serial "541505796617" name "Wireless mouse 8k NANO dongle-L" hash "xfG6U24DoOJG/TDOY8ezuDfLtgZZROy+48YuyhWWgzA=" parent-hash "CbRB9LX/JdGjNWCYSOcIwMVXE0UpOR03LCotWrTbuCM=" with-interface { 03:01:02 03:00:00 03:01:01 } with-connect-type "unknown"
    '';
  };

  environment.systemPackages = [ pkgs.usbguard ];
}

