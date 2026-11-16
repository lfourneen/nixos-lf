{ pkgs, lib, ... }:

{
  # The kernel authorizes internal USB ports while external devices await policy.
  boot.kernelParams = [ "usbcore.authorized_default=2" ];

  services.usbguard = {
    enable = true;
    dbus.enable = true;

    presentDevicePolicy = "apply-policy";
    presentControllerPolicy = "allow";
    insertedDevicePolicy = "apply-policy";
    implicitPolicyTarget = "block";

    # Raw IPC is administrative; desktop D-Bus writes require authentication.
    IPCAllowedUsers = [ "root" ];
    IPCAllowedGroups = [ "usbguard-admin" ];

    ruleFile = "/persist/usbguard/rules.conf";
  };

  system.activationScripts.usbguard-private-policy = {
    deps = [ ];
    text = ''
      ${pkgs.python3}/bin/python3 ${./policy.py} ${pkgs.systemd}/bin/systemctl
    '';
  };

  system.activationScripts.etc.deps = [ "usbguard-private-policy" ];
  systemd.services.usbguard.preStart = lib.mkForce ''
    test -s /persist/usbguard/rules.conf
  '';

  security.polkit.extraConfig = lib.mkBefore ''
    polkit.addRule(function(action, subject) {
      if (action.id.indexOf("org.usbguard.") !== 0 &&
          action.id.indexOf("org.usbguard1.") !== 0) return;
      if (subject.user === "root") return polkit.Result.YES;
      if (!subject.active || !subject.local) return polkit.Result.NO;
      if (action.id === "org.usbguard.Devices1.listDevices" ||
          action.id === "org.usbguard.Policy1.listRules" ||
          action.id === "org.usbguard1.getParameter") return polkit.Result.YES;
      return polkit.Result.AUTH_ADMIN;
    });
  '';

  environment.systemPackages = [ pkgs.usbguard ];
}
