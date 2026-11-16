{ self, pkgs }:

let
  alternate = (self.nixosConfigurations.lfour.extendModules {
    modules = [ ({ lib, ... }: {
      my.machine = {
        desktopUser = lib.mkForce "fixture-user";
        wired.name = lib.mkForce "fixture-eth";
        wireless.name = lib.mkForce "fixture-wifi";
      };

      users.users.fixture-user = {
        isNormalUser = true;
        uid = 1100;
        home = "/home/fixture-user";
      };

      home-manager.users = lib.mkForce {
        fixture-user = { imports = [ ../../home ]; home.stateVersion = "26.05"; };
      };
    }) ];
  }).config;

  rejected = (self.nixosConfigurations.lfour.extendModules {
    modules = [ {
      systemd.services.unreviewed-fixture.serviceConfig = {
        User = "nobody";
        ExecStart = "${pkgs.coreutils}/bin/true";
      };
    } ];
  }).config;

  accepted = (self.nixosConfigurations.lfour.extendModules {
    modules = [ {
      systemd.services.reviewed-fixture.serviceConfig = {
        User = "nobody";
        Slice = "untrusted.slice";
        ExecStart = "${pkgs.coreutils}/bin/true";
      };
    } ];
  }).config;

  failed = c: builtins.filter (a: !a.assertion) c.assertions;

in
assert pkgs.lib.hasInfix "fixture-eth" alternate.my.proxy.proxyModeRules;
assert pkgs.lib.hasInfix "fixture-wifi" alternate.my.proxy.blockedModeRules;
assert !pkgs.lib.hasInfix "ens1" alternate.my.proxy.proxyModeRules;
assert alternate.home-manager.users.fixture-user.home.homeDirectory == "/home/fixture-user";
assert alternate.home-manager.users.fixture-user.home.sessionVariables.DOCKER_HOST == "unix:///run/user/1100/docker.sock";
assert builtins.any (a: pkgs.lib.hasInfix "unreviewed-fixture" a.message) (failed rejected);
assert failed accepted == [ ];
pkgs.runCommand "network-policy-config" { } ''
  touch "$out"
''
