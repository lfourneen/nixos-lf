{ pkgs, ... }:

{
  # Kernel-side fail-closed: external USB stays unauthorized even if USBGuard is down;
  # internal fixed devices (keyboard/camera/BT) stay authorized, so there is no lockout.
  boot.kernelParams = [ "usbcore.authorized_default=2" ];

  # "allow" auto-authorizes boot-present devices; apply-policy would lock them out.
  # `usbguard generate-policy` prints full device descriptions; the rules below
  # deliberately keep only serial/id. Recover a blocked device (temporary until
  # restart) with: usbguard allow-device <id>
  services.usbguard = {
    enable = true;
    dbus.enable = true;

    presentDevicePolicy = "allow";
    presentControllerPolicy = "allow";
    insertedDevicePolicy = "apply-policy";
    implicitPolicyTarget = "block";

    # IPCAllowedUsers alone does not enable polkit (the rule only checks IPCAllowedGroups),
    # so wheel is listed to keep a non-root recovery path for blocked devices.
    IPCAllowedUsers = [ "root" "lfour" ];
    IPCAllowedGroups = [ "wheel" ];

    # Minimal policy, with no device names, descriptor hashes or port topology.
    # Unique devices match on their serial alone. Hubs, receivers and internal
    # devices that report an empty or non-unique serial fall back to id
    # (vendor:product). Root hubs are omitted: they cannot be hotplugged and
    # presentControllerPolicy already allows them at boot.
    # To add a device: run `usbguard generate-policy`, then keep just the
    # `serial` attribute (preferred) or `id` when the serial is not unique.
    rules = ''
      # Unique serials
      allow serial "AP5980_20220308"
      allow serial "541505796617"
      allow serial "000000002AC2"
      allow serial "59280214"
      allow serial "320445587106"
      allow serial "7\xc3\xbfq\x06NW467\x0b\x15C"
      allow serial "9706731062015112663"
      allow serial "9000435880929064"
      allow serial "56C3006945"
      allow serial "AL5B9AF420"
      allow serial "121220160204"
      allow serial "WGKBB21707002272"

      # No usable serial: match the model instead
      allow id 0bda:5411
      allow id 0bda:0411
      allow id 05e3:0610
      allow id 05e3:0626
      allow id 1a40:0101
      allow id 3554:fa09
      allow id 1ea7:0064
      allow id 5986:118a
      allow id 0489:e0cd
      allow id 048d:c102
    '';
  };

  environment.systemPackages = [ pkgs.usbguard ];
}

