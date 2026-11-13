{ self, nixpkgs, system }:

let
      pkgs = nixpkgs.legacyPackages.${system};
      compatibilityBrowser = (self.nixosConfigurations.lfour.extendModules {
        modules = [ ({ lib, ... }: { my.proxy.strictBrowser = lib.mkForce false; }) ];
      }).config.home-manager.users.lfour.programs.firefox.finalPackage;
      strictClashPackage = (self.nixosConfigurations.lfour.extendModules {
        modules = [ ({ lib, ... }: {
          my.proxy.subscriptionTrust = lib.mkForce "source";
          my.proxy.controlledSources = lib.mkForce false;
          my.proxy.strictBrowser = lib.mkForce false;
          my.proxy.profileRefreshPolicy = lib.mkForce "strict";
          my.proxy.subscriptionAudit = lib.mkForce "warn";
          my.proxy.subscriptionAuditAllowedHosts = lib.mkForce { };
        }) ];
      }).config.programs.clash-verge.package;

      refreshClashPackage = (self.nixosConfigurations.lfour.extendModules {
        modules = [ ({ lib, ... }: {
          my.proxy.controlledSources = lib.mkForce false;
          my.proxy.strictBrowser = lib.mkForce false;
          my.proxy.subscriptionTrust = lib.mkForce "source";
          my.proxy.profileRefreshPolicy = lib.mkForce "auto";
          my.proxy.subscriptionAudit = lib.mkForce "enforce";
          my.proxy.subscriptionAuditServerCidrs = [ "100.67.0.1/32" ];
          my.proxy.subscriptionAuditAllowedHosts = lib.mkForce {
            "fallback.fixture.example" = [ "203.0.113.11" "203.0.113.10" ];
            "fallback6.fixture.example" = [ "203.0.113.11" "2001:db8:ffff::10" ];
            "fallback-mixed.fixture.example" = [ "203.0.113.10" "100.67.0.1" ];
          };
          my.proxy.subscriptionAuditProtectedRoutes = lib.mkForce {
            "node.fixture.example" = "proxy";
            "tun.fixture.example" = "direct";
          };
        }) ];
      }).config.programs.clash-verge.package;

      strictDnsConfig = (self.nixosConfigurations.lfour.extendModules {
        modules = [ { my.proxy.dnsEncryption = "strict"; } ];
      }).config;

      uniformProxyConfig = (self.nixosConfigurations.lfour.extendModules {
        modules = [ { my.proxy.proxyOnly = true; } ];
      }).config;

      identityConfig = (self.nixosConfigurations.lfour.extendModules {
        modules = [ {
          my.hardening.wired.clonedMacAddress = "random";
          my.hardening.wifi.identities = {
            "11111111-1111-4111-8111-111111111111" = { mode = "stable"; };
            "22222222-2222-4222-8222-222222222222" = { mode = "stable"; };
            "33333333-3333-4333-8333-333333333333" = { mode = "random"; };
            "44444444-4444-4444-8444-444444444444" = { mode = "fixed"; address = "02:11:22:33:44:55"; };
            "55555555-5555-4555-8555-555555555555" = { mode = "permanent"; };
          };
        } ];
      }).config;

      guestConfig = (self.nixosConfigurations.lfour.extendModules {
        modules = [ {
          users.users.guestnet = { isNormalUser = true; uid = 1100; };
          my.hardening = {
            hotspot.enable = true;
            guestNetworks = {
              virbr0.trust = "trusted";
              waydroid0.sourceCidrs = [ "192.168.240.0/24" "fd42:240::/64" ];
            };
            socketGuestUsers = [ "guestnet" ];
            guestPrivateExceptions = [{ interface = "ap0"; cidr = "10.0.0.1/32"; tcpPorts = [ 443 ]; }];
            socketGuestPrivateExceptions = [{ user = "guestnet"; interface = "ens1"; cidr = "10.0.0.1/32"; tcpPorts = [ 443 ]; }];
          };
          my.proxy.transparentGuestInterfaces = [ "virbr0" ];
        } ];
      }).config;

    in
    {
      openidea-tools = self.nixosConfigurations.lfour.pkgs.openidea.tests.repair-tools;
      proxy-regressions = pkgs.runCommand "proxy-regressions" {
        nativeBuildInputs = [ pkgs.python3 pkgs.bash pkgs.jq ];
      } ''
        export NETWORK_SOURCES=${pkgs.lib.fileset.toSource {
          root = ../.;
          fileset = pkgs.lib.fileset.unions [
            ../system/config/network ../system/programs/clash/service/preflight.py
            ../home/config/clash/status.py
          ];
        }}
        export PYTHONDONTWRITEBYTECODE=1
        python3 ${./network/unit/test_netsec.py}
        touch "$out"
      '';

      proxy-upgrade-review = let
        review = builtins.fromJSON (builtins.readFile ../system/config/network/maintenance/sources.json);
        required = pkgs.lib.unique (pkgs.lib.concatMap (entry: entry.checks) (builtins.attrValues review.components));
      in assert pkgs.lib.all (name: builtins.hasAttr name self.checks.${system}) required;
      pkgs.runCommand "proxy-upgrade-review" { } ''
        test -e ${self.nixosConfigurations.lfour.config.my.proxy.upgradeCheck}
        ${pkgs.python3}/bin/python3 ${./network/maintenance/test_upgrades.py} \
          ${../system/config/network/maintenance/upgrades.py} ${../system/config/network/maintenance/sources.json}
        touch "$out"
      '';

      proxy-validation-tools = pkgs.runCommand "proxy-validation-tools" { } ''
        ${pkgs.python3}/bin/python3 ${./network/maintenance/test_network_tools.py} ${../.}
        touch "$out"
      '';

      proxy-rules = self.nixosConfigurations.lfour.config.my.proxy.expectedRules;
      network-identity-config = import ./network/identity/identity-config.nix {
        inherit pkgs identityConfig;
        config = self.nixosConfigurations.lfour.config;
        fixedConfig = (self.nixosConfigurations.lfour.extendModules {
          modules = [ {
            my.hardening.wired.clonedMacAddress = "02:11:22:33:44:55";
            my.hardening.wired.persistDhcpIdentity = true;
          } ];
        }).config;
      };

      network-identity-vm = import ./network/identity/identity-vm.nix { inherit pkgs; };
      proxy-firewall-vm = import ./network/firewall/firewall-vm.nix { inherit pkgs; };
      proxy-strict-rules-vm = import ./network/firewall/strict-rules-vm.nix {
        inherit pkgs;
        config = self.nixosConfigurations.lfour.config;
      };
      proxy-dns-strict-rules = strictDnsConfig.my.proxy.expectedRules;

      proxy-dns-config = let
        dual = (self.nixosConfigurations.lfour.extendModules { modules = [ ({ lib, ... }: {
          my.machine.dotEndpoints = lib.mkForce [
            { address = "192.0.2.53"; authName = "dot.fixture.test"; }
            { address = "2001:db8::53"; authName = "other.fixture.test"; }
          ];
        }) ]; }).config;

        v6 = (self.nixosConfigurations.lfour.extendModules { modules = [ ({ lib, ... }: {
          my.machine.dotEndpoints = lib.mkForce [{ address = "2001:db8::53"; authName = "v6.fixture.test"; }];
        }) ]; }).config;

        variants = [ self.nixosConfigurations.lfour.config strictDnsConfig dual v6 ];
        settings = pkgs.writeText "dns-endpoint-configs.json" (builtins.toJSON (map (c: {
          endpoints = c.my.machine.dotEndpoints;
          port = c.my.machine.ports.dot;
          zones = c.services.unbound.settings.forward-zone;
          script = c.systemd.services.dns-upstream.serviceConfig.ExecStart;
        }) variants));

      in
      pkgs.runCommand "dns-policy-config" { } ''
        ${pkgs.python3}/bin/python3 ${./network/dns/check_dns_endpoints.py} ${settings}
        ${pkgs.lib.concatMapStrings (c: "${pkgs.bash}/bin/bash -n ${c.systemd.services.dns-upstream.serviceConfig.ExecStart}\n") variants}
        test -e ${dual.my.proxy.expectedRules}
        test -e ${v6.my.proxy.expectedRules}
        touch "$out"
      '';

      proxy-egress-config = self.nixosConfigurations.lfour.config.my.hardening.directEgress.configCheck;
      proxy-guest-config = pkgs.runCommand "guest-policy-config" { } ''
        test -e ${self.nixosConfigurations.lfour.config.my.hardening.guestConfigCheck}
        test -e ${guestConfig.my.hardening.guestConfigCheck}
        ${pkgs.bash}/bin/bash -n ${guestConfig.systemd.user.services.docker.serviceConfig.ExecStart}
        touch "$out"
      '';

      proxy-guest-rules = guestConfig.my.proxy.expectedRules;

      tailscale-transport = self.nixosConfigurations.lfour.config.my.hardening.tailscale.transportCheck;

      proxy-tailscale-vm = import ./network/tailscale/tailscale-vm.nix {
        inherit pkgs;
        tailscale = self.nixosConfigurations.lfour.config.services.tailscale.package;
      };

      proxy-egress-vm = import ./network/tailscale/tailscale-vm.nix {
        inherit pkgs;
        tailscale = self.nixosConfigurations.lfour.config.services.tailscale.package;
        restricted = true;
      };

      proxy-egress-rules = (self.nixosConfigurations.lfour.extendModules {
        modules = [ {
          my.hardening.allowPrivateEgress = false;
          my.hardening.directEgress = {
            mode = "restricted";
            unboundDotInProxy = false;
            timeServerCidrs = [ "203.0.113.10/32" "2001:db8::10/128" ];
            exceptions = [
              { interface = "ens1"; cidr = "10.0.0.1/32"; uids = [ 1000 ]; tcpPorts = [ 443 ]; }
              { interface = "wlo1"; cidr = "ff02::fb/128"; uids = [ ]; udpPorts = [ 5353 ]; }
            ];
          };
        } ];
      }).config.my.proxy.expectedRules;

      browser-privacy = import ./home/firefox/firefox.nix {
        inherit pkgs;
        config = self.nixosConfigurations.lfour.config;
        compatConfig = (self.nixosConfigurations.lfour.extendModules {
          modules = [ {
            home-manager.users.${self.nixosConfigurations.lfour.config.my.machine.desktopUser} = {
              my.firefox.videoSiteCompat = true;
            };
          } ];
        }).config;
      };

      browser-sandbox-vm = import ./home/firefox/firefox-sandbox-vm.nix {
        inherit pkgs;
        firefox = compatibilityBrowser;
      };

      browser-ech-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = strictClashPackage;
        ech = {
          http-proxy = compatibilityBrowser;
          transparent = (self.nixosConfigurations.lfour.extendModules {
            modules = [ { home-manager.users.lfour.my.firefox.networkPath = "transparent";
                          home-manager.users.lfour.my.firefox.dailyRequireProxy = false; } ];
          }).config.home-manager.users.lfour.programs.firefox.finalPackage;
        };
      };

      browser-ech-uniform-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = strictClashPackage;
        uniformProxy = true;
        ech = {
          http-proxy = (self.nixosConfigurations.lfour.extendModules {
            modules = [ { my.proxy.proxyOnly = true; home-manager.users.lfour.my.firefox.dailyRequireProxy = false; } ];
          }).config.home-manager.users.lfour.programs.firefox.finalPackage;
          transparent = (self.nixosConfigurations.lfour.extendModules {
            modules = [ {
              my.proxy.proxyOnly = true;
              home-manager.users.lfour.my.firefox.networkPath = "transparent";
              home-manager.users.lfour.my.firefox.dailyRequireProxy = false;
            } ];
          }).config.home-manager.users.lfour.programs.firefox.finalPackage;
        };
      };

      proxy-profile = import ./network/proxy/profile.nix {
        inherit pkgs;
        config = self.nixosConfigurations.lfour.config;
        inherit uniformProxyConfig;

        restrictedConfig = (self.nixosConfigurations.lfour.extendModules {
          modules = [ {
            my.hardening.allowPrivateEgress = false;
            my.hardening.directEgress = {
              mode = "restricted";
              timeServerCidrs = [ "203.0.113.10/32" ];
              timeServerNames = [ "ntp.fixture.example" ];
            };
          } ];
        }).config;

        privateProxyConfig = (self.nixosConfigurations.lfour.extendModules {
          modules = [ { my.hardening.allowPrivateEgress = false; } ];
        }).config;
      };

      proxy-network-vm = import ./network/integration/vm.nix {
        inherit pkgs;
        core = self.nixosConfigurations.lfour.config.programs.clash-verge.package.managedCore;
        clashPackage = strictClashPackage;
      };

      proxy-refresh-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = refreshClashPackage;
        refresh = true;
      };

      proxy-control-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = strictClashPackage;
      };

      proxy-uniform-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = strictClashPackage;
        uniformProxy = true;
      };

      proxy-target-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = strictClashPackage;
        targetPolicy = true;
      };

      proxy-guest-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = strictClashPackage;
        guests = true;
      };

      proxy-dns-strict-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = strictClashPackage;
        strictDns = true;
      };

      proxy-egress-control-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = strictClashPackage;
        restricted = true;
      };

      proxy-control = pkgs.runCommand "proxy-control-regressions" {
        nativeBuildInputs = [ pkgs.python3 ];
      } ''
        export PYTHONDONTWRITEBYTECODE=1
        python3 ${./network/proxy/test_control.py} ${../system/programs/clash/service/clash-verge-stage.py} ${../system/config/network/proxy/health.py}
        touch "$out"
      '';

      proxy-source-config = import ./network/proxy/source-config.nix {
        inherit pkgs;
        config = self.nixosConfigurations.lfour.config;
      };
      proxy-source = import ./network/proxy/source-check.nix { inherit pkgs; };
      proxy-isolation-vm = import ./network/proxy/isolation-vm.nix {
        inherit pkgs;
        core = self.nixosConfigurations.lfour.config.programs.clash-verge.package.managedCore;
      };

      proxy-refresh = pkgs.runCommand "proxy-refresh-regressions" {
        nativeBuildInputs = [ (pkgs.python3.withPackages (python: [ python.pyyaml ])) ];
      } ''
        export PYTHONDONTWRITEBYTECODE=1
        python3 ${./network/proxy/test_audit.py} ${../system/programs/clash/service/subscription_audit.py}
        python3 ${./network/proxy/test_subscription_check.py} ${../system/config/network/diagnostics/subscription_check.py}
        python3 ${./network/unit/test_state_views.py} ${../system/config/network/proxy/state.py}
        python3 ${./home/clash/test_refresh_notify.py} ${../home/config/clash/refresh_notify.py}
        touch "$out"
      '';

      proxy-control-config = let
        c = self.nixosConfigurations.lfour.config;
        small = self.checks.${system}.proxy-control-vm.config.nodes.machine;
        broad = self.checks.${system}.proxy-network-vm.config.nodes.machine;
        uid = c.my.machine.uids.clashCore;
      in assert builtins.isInt uid && uid > 0 && c.users.users.clash-core.uid == uid;
      assert pkgs.lib.count (u: u.uid == uid) (pkgs.lib.attrValues c.users.users) == 1;
      assert small.users.users.clash-core.uid == uid && broad.users.users.clash-core.uid == uid;
      assert small.programs.clash-verge.package == strictClashPackage;
      assert strictClashPackage.managedCore.src == c.programs.clash-verge.package.managedCore.src;
      assert strictClashPackage.managedService == c.programs.clash-verge.package.managedService;
      assert broad.programs.clash-verge.package == strictClashPackage;
      assert small.systemd.services.clash-verge.serviceConfig.CapabilityBoundingSet
        == c.systemd.services.clash-verge.serviceConfig.CapabilityBoundingSet;

      pkgs.runCommand "proxy-control-config" { nativeBuildInputs = [ pkgs.python3 ]; } ''
        python3 - ${c.my.proxy.listenerSettings} ${toString uid} <<'PY'
        import json
        import sys
        settings = json.load(open(sys.argv[1]))
        for name in ('core', 'core-dns', 'core-mixed'):
            for listener in settings['groups'][name]:
                assert listener['uids'] == [int(sys.argv[2])], listener
                assert listener['cgroup'] == '/system.slice/clash-verge.service', listener
        PY
        touch "$out"
      '';

      proxy-core = self.nixosConfigurations.lfour.config.programs.clash-verge.package.managedCore;
      proxy-gost = self.nixosConfigurations.lfour.config.my.proxy.gostPackage;
      proxy-gui = self.nixosConfigurations.lfour.config.programs.clash-verge.package.guiDefaultsCheck;
    }
