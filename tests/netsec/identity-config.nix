{ pkgs, config, identityConfig, fixedConfig }:

let
  directories = c: map (entry: { inherit (entry) directory mode; })
    c.environment.persistence."/persist".directories;

  manifest = pkgs.writeText "identity-config-tests.json" (builtins.toJSON {
    settings = identityConfig.my.hardening.linkIdentitySettings;
    base = {
      wifi = config.my.hardening.wifi.clonedMacAddress;
      wired = config.my.hardening.wired.clonedMacAddress;
      persist = config.my.hardening.wired.persistDhcpIdentity;
      link = config.systemd.network.links."10-wired-${config.my.machine.wired.name}";
      directories = directories config;
      dhcpService = config.systemd.services.dhcpcd.serviceConfig;
    };

    random = identityConfig.networking.dhcpcd.extraConfig;

    fixed = {
      link = fixedConfig.systemd.network.links."10-wired-${fixedConfig.my.machine.wired.name}";
      dhcp = fixedConfig.networking.dhcpcd.extraConfig;
      directories = directories fixedConfig;
    };
  });

in
pkgs.runCommand "network-identity-checks" { } ''
  test -e ${config.my.hardening.linkIdentityCheck}
  test -e ${identityConfig.my.hardening.linkIdentityCheck}
  test -e ${fixedConfig.my.hardening.linkIdentityCheck}
  ${pkgs.python3}/bin/python3 ${./test_link_identity.py} ${../../system/programs/systemd/netsec/link_identity.py}
  ${pkgs.python3}/bin/python3 ${./check_identity_config.py} \
    ${manifest} ${../../system/programs/systemd/netsec/link_identity.py} \
    ${config.networking.networkmanager.package}/bin/nmcli
  touch "$out"
''
