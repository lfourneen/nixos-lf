{ config, lib, pkgs, ... }:

let
  h = config.my.hardening;
  settings = pkgs.writeText "network-identities.json" (builtins.toJSON {
    nmcli = "${config.networking.networkmanager.package}/bin/nmcli";
    interface = config.my.machine.wireless.name;
    stateDirectory = "/var/lib/network-identity";
    profiles = h.wifi.identities;
    wifiDefault = h.wifi.clonedMacAddress;
    wired = h.wired.clonedMacAddress;
  });
  check = pkgs.runCommand "network-identity-config" { } ''
    ${pkgs.python3}/bin/python3 ${../programs/systemd/netsec/link_identity.py} ${settings} check-config
    touch "$out"
  '';
  tool = pkgs.writeShellScriptBin "network-identity" ''
    exec ${pkgs.python3}/bin/python3 ${../programs/systemd/netsec/link_identity.py} ${settings} "$@"
  '';
in
{
  my.hardening.linkIdentitySettings = settings;
  my.hardening.linkIdentityCheck = check;
  system.checks = [ check ];
  environment.systemPackages = [ tool ];
  systemd.tmpfiles.rules = [ "d /var/lib/network-identity 0700 root root -" ];

  # The upstream manager daemon binds [::]:546 even with an allowinterfaces
  # list. Its native single-interface mode instead binds the wired link-local
  # address, leaving NM's independent Wi-Fi DHCPv6 client able to bind.
  # Extend the upstream command so its generated config and hardening survive.
  systemd.services.dhcpcd = { ... }: {
    options.serviceConfig = lib.mkOption {
      apply = service: service // lib.optionalAttrs
        (service ? ExecStart && builtins.isString service.ExecStart
          && lib.hasPrefix "@${pkgs.dhcpcd}/sbin/dhcpcd dhcpcd " service.ExecStart) {
        ExecStart = "${service.ExecStart} ${lib.escapeShellArg config.my.machine.wired.name}";
        ExecReload = "${service.ExecReload} ${lib.escapeShellArg config.my.machine.wired.name}";
        PIDFile = "/run/dhcpcd/${config.my.machine.wired.name}.pid";
      };
    };
  };

  # The permanent match is independent of the address assigned by dhcpcd or
  # this .link file. The interface-name unmanaged selector remains authoritative.
  systemd.network.links."10-wired-${config.my.machine.wired.name}".linkConfig = {
    # NixOS raises the interface before starting dhcpcd. Randomize the initial
    # address in udev as well; dhcpcd owns later carrier-loss randomization.
    MACAddressPolicy = if h.wired.clonedMacAddress == "random" then "random" else "none";
  } // lib.optionalAttrs (!(builtins.elem h.wired.clonedMacAddress [ "permanent" "random" ])) {
    MACAddress = h.wired.clonedMacAddress;
  };

  # Keep the native client's address, client-id, DUID and lease lifecycle
  # together. Append anonymous last so inherited hostname/options are masked.
  networking.dhcpcd.extraConfig = lib.mkIf (h.wired.clonedMacAddress != "permanent") (lib.mkOrder 2000 ''
    interface ${config.my.machine.wired.name}
    ${lib.optionalString (h.wired.clonedMacAddress == "random") "randomise_hwaddr"}
    slaac hwaddr
    anonymous
  '');
}
