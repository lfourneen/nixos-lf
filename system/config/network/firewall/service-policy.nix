{ config, lib, ... }:

let
  ports = import ../lib/reserved-ports.nix { inherit lib; ports = config.my.machine.ports; };

  infrastructure = [
    "clash-core-localhost" "clash-core-loopback" "clash-dns-application"
    "clash-dns-infrastructure" "clash-strict" "dhcpcd" "gost-relay"
    "proxy-real-dns" "clash-real-dns"
    "socket-guest-dns" "unbound" "nscd" "wpa_supplicant" "clamav-freshclam"
  ];

  protected = [
    "clash-gui-forwarder" "mpd" "hermes-user-profile-seed"
    "home-manager-${config.my.machine.desktopUser}"
  ];

  covered = service: let
    slice = service.serviceConfig.Slice or "system.slice";
    deny = service.serviceConfig.SocketBindDeny or [ ];
    inherited = config.systemd.slices.${lib.removeSuffix ".slice" slice}.sliceConfig.SocketBindDeny or [ ];
  in lib.all (p: builtins.elem p (deny ++ inherited)) ports;

in
{
  systemd.slices.untrusted.sliceConfig.SocketBindDeny = ports;
  systemd.services = lib.genAttrs protected (_: {
    serviceConfig.Slice = "untrusted.slice";
  });

  assertions = lib.mapAttrsToList (name: service: {
    assertion = (service.serviceConfig.User or "root") == "root"
      || builtins.elem name infrastructure || covered service;
    message = "Review network bind policy for system service ${name}: use untrusted.slice or explicitly review its infrastructure listeners.";
  }) config.systemd.services;
}
