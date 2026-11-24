{ self, nixpkgs, system }:

let
      pkgs = nixpkgs.legacyPackages.${system};
      desktopUser = self.nixosConfigurations.lfour.config.my.machine.desktopUser;
      guiFixtureConfig = (self.nixosConfigurations.lfour.extendModules {
        modules = [ ({ lib, ... }: {
          my.machine.desktopUser = lib.mkForce "gui-fixture";
          my.proxy.delayTestUrl = lib.mkForce "https://source.fixture.example:17443/speed?policy=root";
          my.proxy.nodeProbeUrls = lib.mkForce [ "http://node.fixture.example:8080/" ];
          my.proxy.tunProbeName = lib.mkForce "tun.fixture.example";
          my.proxy.tunProbeNames = lib.mkForce [ "tun.fixture.example" "cp.cloudflare.com" ];
          users.users.gui-fixture = {
            isNormalUser = true;
            uid = 1107;
            home = "/srv/gui-fixture";
          };
          home-manager.users = lib.mkForce {
            gui-fixture = { imports = [ ../home ]; home.stateVersion = "26.05"; };
          };
        }) ];
      }).config;

      # One source tree for tests that resolve paths relative to their own
      # location. Secrets and the blocklist snapshot are already build inputs
      # of the flake.
      repoSource = pkgs.lib.fileset.toSource {
        root = ../.;
        fileset = pkgs.lib.fileset.unions [
          ../flake.nix ../flake.lock ../.sops.yaml
          ../home ../maintenance ../local-data ../overlays ../scripts ../system ../tests
        ];
      };

      compatibilityBrowser = (self.nixosConfigurations.lfour.extendModules {
        modules = [ ({ lib, ... }: {
          my.proxy.strictBrowser = lib.mkForce false;
          my.proxy.transparentApplications = lib.mkForce false;
        }) ];
      }).config.home-manager.users.${desktopUser}.programs.firefox.finalPackage;
      strictClashPackage = (self.nixosConfigurations.lfour.extendModules {
        modules = [ ({ lib, ... }: {
          my.proxy.subscriptionTrust = lib.mkForce "source";
          my.proxy.controlledSources = lib.mkForce false;
          my.proxy.runtimeRouting = lib.mkForce false;
          my.proxy.strictBrowser = lib.mkForce false;
          my.proxy.profileRefreshPolicy = lib.mkForce "strict";
          my.proxy.subscriptionAudit = lib.mkForce "warn";
          my.proxy.subscriptionAuditAllowedHosts = lib.mkForce { };
          my.proxy.localDnssec = lib.mkForce false;
        }) ];
      }).config.programs.clash-verge.package;

      refreshClashPackage = (self.nixosConfigurations.lfour.extendModules {
        modules = [ ({ lib, ... }: {
          my.proxy.controlledSources = lib.mkForce false;
          my.proxy.runtimeRouting = lib.mkForce false;
          my.proxy.strictBrowser = lib.mkForce false;
          my.proxy.subscriptionTrust = lib.mkForce "source";
          my.proxy.profileRefreshPolicy = lib.mkForce "auto";
          my.proxy.localDnssec = lib.mkForce false;
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

      managedClashPackageFor = runtimeRouting: (self.nixosConfigurations.lfour.extendModules {
        modules = [ ({ lib, ... }: {
          my.proxy.runtimeRouting = lib.mkForce runtimeRouting;
          my.proxy.nodeProbeUrls = lib.mkForce [ "http://node.fixture.example:8080/" ];
          my.proxy.tunProbeName = lib.mkForce "tun.fixture.example";
          my.proxy.tunProbeNames = lib.mkForce [ "tun.fixture.example" "cp.cloudflare.com" ];
        }) ];
      }).config.programs.clash-verge.package;

      strictDnsConfig = (self.nixosConfigurations.lfour.extendModules {
        modules = [ { my.proxy.dnsEncryption = "strict"; } ];
      }).config;

      uniformProxyConfig = (self.nixosConfigurations.lfour.extendModules {
        modules = [ ({ lib, ... }: { my.proxy.proxyOnly = true; my.proxy.runtimeRouting = lib.mkForce false; }) ];
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
      network-policy-config = import ./network/policy-config.nix { inherit self pkgs; };
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

        httpOnly = (self.nixosConfigurations.lfour.extendModules { modules = [ ({ lib, ... }: {
          my.proxy.runtimeRouting = lib.mkForce false;
          my.proxy.transparentApplications = lib.mkForce false;
          my.proxy.localDnssec = lib.mkForce false;
          my.proxy.tunMode = lib.mkForce false;
          home-manager.users.${desktopUser}.my.firefox.networkPath = lib.mkForce "http-proxy";
        }) ]; }).config;

        variants = [ self.nixosConfigurations.lfour.config strictDnsConfig dual v6 httpOnly ];
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
        ${pkgs.lib.concatMapStrings (c: "${pkgs.bash}/bin/bash -n ${c.systemd.services.nftables-verify.serviceConfig.ExecStart}\n") variants}
        ${pkgs.bash}/bin/bash -n ${../system/config/network/firewall/client_dns.sh}
        test -e ${dual.my.proxy.expectedRules}
        test -e ${v6.my.proxy.expectedRules}
        touch "$out"
      '';

      proxy-egress-config = self.nixosConfigurations.lfour.config.my.hardening.directEgress.configCheck;
      proxy-guest-config = let
        c = self.nixosConfigurations.lfour.config;
        account = c.users.users.hermes-containers;
        service = c.systemd.services.hermes-containers;
      in assert account.uid == c.my.machine.uids.hermesContainers;
      assert builtins.elem "hermes-containers" c.my.hardening.socketGuestUsers;
      assert account.extraGroups == [ "hermes-container-clients" ];
      assert pkgs.lib.sort builtins.lessThan c.users.groups.hermes-container-clients.members == [ "hermes" "hermes-containers" ];
      assert service.serviceConfig.User == "hermes-containers";
      assert builtins.elem "HTTP_PROXY" service.serviceConfig.UnsetEnvironment;
      assert builtins.elem "hermes-container-retire.service" service.requires;
      assert c.systemd.services.hermes-agent.environment.DOCKER_HOST == "unix:///run/hermes-containers/docker.sock";
      assert c.systemd.services.hermes-backend.environment.DOCKER_HOST == c.systemd.services.hermes-agent.environment.DOCKER_HOST;
      pkgs.runCommand "guest-policy-config" { } ''
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
        config = (self.nixosConfigurations.lfour.extendModules {
          modules = [ ({ lib, ... }: {
            my.proxy.transparentApplications = lib.mkForce false;
            home-manager.users.${desktopUser}.my.firefox.videoSiteCompat = false;
          }) ];
        }).config;
        compatConfig = (self.nixosConfigurations.lfour.extendModules {
          modules = [ ({ lib, ... }: {
            my.proxy.transparentApplications = lib.mkForce false;
            home-manager.users.${self.nixosConfigurations.lfour.config.my.machine.desktopUser} = {
              my.firefox.videoSiteCompat = true;
            };
          }) ];
        }).config;
        windowsConfig = (self.nixosConfigurations.lfour.extendModules {
          modules = [ ({ lib, ... }: {
            my.proxy.transparentApplications = lib.mkForce false;
            home-manager.users.${desktopUser}.my.firefox = {
              videoSiteCompat = false;
              windowsUserAgentDomains = [ "windows-ua.invalid" ];
            };
          }) ];
        }).config;
      };

      browser-sandbox-vm = let
        fixtureConfig = (self.nixosConfigurations.lfour.extendModules {
          modules = [ ({ lib, ... }: {
            my.proxy.transparentApplications = lib.mkForce false;
            my.proxy.listenerCheck = lib.mkForce "${pkgs.coreutils}/bin/true";
          }) ];
        }).config;
        home = fixtureConfig.home-manager.users.${desktopUser};
        launcher = name: pkgs.lib.findFirst (pkg: pkgs.lib.getName pkg == "firefox-${name}")
          (throw "Missing firefox-${name} launcher") home.home.packages;
      in import ./home/firefox/firefox-sandbox-vm.nix {
        inherit pkgs;
        firefox = home.programs.firefox.finalPackage;
        identity = launcher "identity";
        portal = launcher "portal";
        inherit (fixtureConfig.my.proxy) routingPolicies;
        desktopIdentity = self.nixosConfigurations.lfour.config.programs.clash-verge.package.desktopIdentity;
      };

      browser-ech-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = strictClashPackage;
        ech = {
          http-proxy = compatibilityBrowser;
          transparent = (self.nixosConfigurations.lfour.extendModules {
            modules = [ {
              home-manager.users.${desktopUser}.my.firefox = {
                networkPath = "transparent";
                dailyRequireProxy = false;
              };
            } ];
          }).config.home-manager.users.${desktopUser}.programs.firefox.finalPackage;
        };
      };

      browser-ech-uniform-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = strictClashPackage;
        uniformProxy = true;
        ech = {
          http-proxy = (self.nixosConfigurations.lfour.extendModules {
            modules = [ ({ lib, ... }: {
              my.proxy.proxyOnly = true;
              my.proxy.transparentApplications = lib.mkForce false;
              home-manager.users.${desktopUser}.my.firefox.dailyRequireProxy = false;
            }) ];
          }).config.home-manager.users.${desktopUser}.programs.firefox.finalPackage;
          transparent = (self.nixosConfigurations.lfour.extendModules {
            modules = [ {
              my.proxy.proxyOnly = true;
              home-manager.users.${desktopUser}.my.firefox = {
                networkPath = "transparent";
                dailyRequireProxy = false;
              };
            } ];
          }).config.home-manager.users.${desktopUser}.programs.firefox.finalPackage;
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

      proxy-arp-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = strictClashPackage;
        arpProtection = true;
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

      proxy-hermes-containers-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = strictClashPackage;
        guests = true;
        hermesContainers = true;
      };

      proxy-core-netns-vm = import ./network/proxy/core-netns-vm.nix {
        inherit pkgs;
        core = self.nixosConfigurations.lfour.config.programs.clash-verge.package.managedCore;
      };

      proxy-managed-netns-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = managedClashPackageFor false;
        managedSources = true;
      };

      proxy-routing = pkgs.runCommand "proxy-routing-transactions" {
        nativeBuildInputs = [ (pkgs.python3.withPackages (p: [ p.pyyaml ])) ];
      } ''
        export PYTHONDONTWRITEBYTECODE=1
        python3 ${./network/proxy/test_routing.py} ${repoSource}
        touch "$out"
      '';

      proxy-routing-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = managedClashPackageFor true;
        managedSources = true;
        assurance = true;
        routing = true;
      };

      proxy-routing-recovery-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = managedClashPackageFor true;
        managedSources = true;
        assurance = true;
        routing = true;
        recoveryOnly = true;
      };

      proxy-single-egress-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = managedClashPackageFor true;
        managedSources = true;
        assurance = true;
        routing = true;
        egressBrowser = self.nixosConfigurations.lfour.config.home-manager.users.${desktopUser}.programs.firefox.finalPackage;
        evidenceIdentity = {
          source = builtins.hashString "sha256" (toString repoSource);
          lock = builtins.hashFile "sha256" ../flake.lock;
        };
      };

      proxy-ip-tunnel-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = managedClashPackageFor true;
        managedSources = true;
        assurance = true;
        routing = true;
        ipTunnelRelay = self.inputs.nixpkgs-unstable.legacyPackages.${system}.mihomo;
      };

      proxy-gui-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = guiFixtureConfig.programs.clash-verge.package;
        managedSources = true;
        assurance = true;
        routing = true;
        guiControls = true;
        guiPkgs = self.inputs.nixpkgs-unstable.legacyPackages.${system};
      };

      proxy-dns-strict-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = strictClashPackage;
        strictDns = true;
      };

      proxy-assurance-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = strictClashPackage;
        assurance = true;
      };

      proxy-egress-control-vm = import ./network/proxy/control-vm.nix {
        inherit pkgs;
        clashPackage = strictClashPackage;
        restricted = true;
      };

      proxy-control = pkgs.runCommand "proxy-control-regressions" {
        nativeBuildInputs = [ pkgs.python3 pkgs.yq-go ];
      } ''
        export PYTHONDONTWRITEBYTECODE=1
        export LF_TEST_YQ=${pkgs.yq-go}/bin/yq
        python3 ${./network/proxy/test_control.py} ${../system/programs/clash/service/clash-verge-stage.py} \
          ${../system/config/network/proxy/health.py} ${../system/programs/clash/service/preflight.py}
        python3 ${./network/proxy/test_authorization.py} ${../system/config/network/proxy/authorization.py}
        python3 ${./network/proxy/test_desktop.py} ${./network/proxy/desktop.py}
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
        clash = c.programs.clash-verge.package;
        installInputs = builtins.getContext clash.installPhase;
        fixupInputs = builtins.getContext clash.postFixup;
        small = self.checks.${system}.proxy-control-vm.config.nodes.machine;
        broad = self.checks.${system}.proxy-network-vm.config.nodes.machine;
        uid = c.my.machine.uids.clashCore;
      in assert builtins.isInt uid && uid > 0 && c.users.users.clash-core.uid == uid;
      assert pkgs.lib.count (u: u.uid == uid) (pkgs.lib.attrValues c.users.users) == 1;
      assert small.users.users.clash-core.uid == uid && broad.users.users.clash-core.uid == uid;
      assert small.programs.clash-verge.package == strictClashPackage;
      assert strictClashPackage.managedCore.src == clash.managedCore.src;
      assert strictClashPackage.managedService == clash.managedService;
      assert builtins.hasAttr (builtins.unsafeDiscardStringContext clash.managedService.drvPath) fixupInputs;
      assert !(builtins.hasAttr (builtins.unsafeDiscardStringContext clash.service.drvPath) installInputs);
      assert broad.programs.clash-verge.package == strictClashPackage;
      assert small.systemd.services.clash-verge.serviceConfig.CapabilityBoundingSet
        == c.systemd.services.clash-verge.serviceConfig.CapabilityBoundingSet;
      assert c.systemd.services.clash-verge.serviceConfig.NetworkNamespacePath == "/run/netns/lf-clash-core";
      assert small.systemd.services.clash-verge.serviceConfig.NetworkNamespacePath
        == c.systemd.services.clash-verge.serviceConfig.NetworkNamespacePath;
      assert c.systemd.services.clash-network-observer.serviceConfig.CapabilityBoundingSet == "";
      assert c.systemd.services.clash-core-loopback.serviceConfig.User != "clash-core";
      assert c.systemd.services.clash-core-loopback.serviceConfig.CapabilityBoundingSet == "";
      assert c.systemd.services.clash-core-localhost.serviceConfig.NetworkNamespacePath
        == c.systemd.services.clash-verge.serviceConfig.NetworkNamespacePath;

      pkgs.runCommand "proxy-control-config" { nativeBuildInputs = [ pkgs.python3 ]; } ''
        python3 - ${c.my.proxy.listenerSettings} ${toString uid} <<'PY'
        import json
        import sys
        settings = json.load(open(sys.argv[1]))
        for name in ('core', 'core-dns', 'core-mixed'):
            for listener in settings['groups'][name]:
                if listener['cgroup'] in ('/system.slice/clash-dns-application.service', '/system.slice/clash-dns-infrastructure.service'):
                    assert listener['uids'] == [${toString c.users.users.unbound.uid}], listener
                    assert listener['namespace'] is True, listener
                    continue
                assert listener['uids'] == [int(sys.argv[2])], listener
                assert listener['cgroup'] == '/system.slice/clash-verge.service', listener
                assert listener['namespace'] is True, listener
        PY
        touch "$out"
      '';

      security-policy-config = let
        host = self.nixosConfigurations.lfour;
        c = host.config;
        ports = c.my.machine.ports;
        activation = c.system.activationScripts."hermes-agent-setup".text;
        policyAssertion = cfg: message:
          (pkgs.lib.findFirst (check: check.message == message) { assertion = false; } cfg.assertions).assertion;
        noSsh = "The desktop host does not provide OpenSSH.";
        noSshIngress = "The desktop host does not declare TCP/22 ingress.";
        withSsh = (host.extendModules { modules = [ { services.openssh.enable = true; } ]; }).config;
        withTailnetSsh = (host.extendModules {
          modules = [ { my.hardening.tailnetTcpPorts = [ "21-23" ]; } ];
        }).config;
        withGuestSsh = (host.extendModules {
          modules = [ { my.hardening.guestNetworks.${c.my.machine.vmBridge}.hostTcpPorts = [ 22 ]; } ];
        }).config;
      in
      assert policyAssertion c noSsh && policyAssertion c noSshIngress;
      assert !(policyAssertion withSsh noSsh);
      assert !(policyAssertion withTailnetSsh noSshIngress);
      assert !(policyAssertion withGuestSsh noSshIngress);
      assert c.services.usbguard.presentDevicePolicy == "apply-policy";
      assert c.services.usbguard.IPCAllowedUsers == [ "root" ];
      assert c.services.usbguard.IPCAllowedGroups == [ "usbguard-admin" ];
      assert builtins.hasAttr "usbguard-admin" c.users.groups;
      assert builtins.hasAttr "adbusers" c.users.groups;
      assert !(builtins.elem "input" c.users.users.lfour.extraGroups);
      assert !(builtins.elem "tty" c.users.users.lfour.extraGroups);
      assert c.systemd.coredump.settings.Coredump.Storage == "none";
      assert c.systemd.coredump.settings.Coredump.ProcessSizeMax == "0";
      assert !(builtins.hasAttr "coredump-clean" c.systemd.services);
      assert c.virtualisation.libvirtd.qemu.runAsRoot == false;
      assert c.virtualisation.virtualbox.host.enable && c.virtualisation.virtualbox.host.enableHardening;
      assert c.virtualisation.virtualbox.host.addNetworkInterface && !c.virtualisation.virtualbox.host.enableKvm;
      assert c.my.hardening.guestNetworks.vboxnet0.trust == "restricted";
      assert builtins.elem "vboxnet0" c.my.proxy.guestInterfaces;
      assert builtins.elem "vboxnet0" c.my.proxy.transparentGuestInterfaces;
      assert c.virtualisation.containerd.enable == true;
      assert c.systemd.services.hermes-agent.serviceConfig.Slice == "hermes.slice";
      assert c.systemd.services.hermes-backend.serviceConfig.Slice == "hermes.slice";
      assert c.systemd.services.hermes-containers.serviceConfig.Slice == "hermes.slice";
      assert c.systemd.services."hermes-time@".serviceConfig.Slice == "untrusted.slice";
      assert c.systemd.services."hermes-time@".serviceConfig.RestrictAddressFamilies == [ "AF_UNIX" ];
      assert c.users.groups.hermes-time-clients.members == [ c.services.hermes-agent.user ];
      assert !(builtins.elem "hermes-time" c.my.hardening.socketGuestUsers);
      assert c.systemd.slices.hermes.sliceConfig.SocketBindDeny
        == c.systemd.slices.user.sliceConfig.SocketBindDeny;
      assert c.my.secrets.systemAgeKey.enable == true;
      assert c.sops.age.keyFile == "/persist/secrets/age/system.key";
      assert builtins.hasAttr "system-secrets-key" c.system.activationScripts;
      assert builtins.elem (toString ports.coreDnsTransport)
        c.systemd.slices.hermes.sliceConfig.SocketBindDeny;
      assert builtins.elem (toString ports.coreDnsBootstrapTransport)
        c.systemd.slices.hermes.sliceConfig.SocketBindDeny;
      assert pkgs.lib.hasInfix "hermes-state.py" activation;
      assert pkgs.lib.hasInfix "--managed-file" activation;
      assert pkgs.lib.hasInfix "skills/agently-mail/SKILL.md" activation;
      assert !pkgs.lib.hasInfix "find /var/lib/hermes/home" activation;
      assert pkgs.lib.hasInfix "/home/config/agent-skills/" (builtins.readFile ../scripts/push-to-dir.sh);
      assert builtins.pathExists ../local-data/dns-blocklist.manifest.json;
      pkgs.runCommand "security-policy-config" { } ''
        touch "$out"
      '';

      deployment-tests = pkgs.runCommand "deployment-tests" {
        nativeBuildInputs = with pkgs; [
          python3 bash rsync coreutils findutils util-linux git
        ];
      } ''
        export PYTHONDONTWRITEBYTECODE=1 DEPLOY_SKIP_EVAL=1
        python3 ${repoSource}/tests/deployment/test_deployment.py ${repoSource}/scripts/push-to-dir.sh
        python3 ${repoSource}/tests/deployment/test_deployment_safety.py ${repoSource}/scripts/push-to-dir.sh
        python3 ${repoSource}/tests/deployment/test_sync_to_git.py ${repoSource}/scripts/sync-to-git.sh
        python3 ${repoSource}/tests/deployment/test_verify_network.py ${repoSource}/scripts/verify-network.py
        touch "$out"
      '';

      maintenance-ledger = pkgs.runCommand "maintenance-ledger" {
        nativeBuildInputs = [ pkgs.python3 ];
      } ''
        export PYTHONDONTWRITEBYTECODE=1
        python3 ${repoSource}/scripts/check-maintenance.py --json
        touch "$out"
      '';

      blocklist-tests = pkgs.runCommand "blocklist-tests" {
        nativeBuildInputs = [ pkgs.python3 pkgs.xz ];
      } ''
        export PYTHONDONTWRITEBYTECODE=1
        python3 ${repoSource}/tests/network/maintenance/test_blocklist.py
        touch "$out"
      '';

      device-policy = pkgs.runCommand "device-policy" {
        nativeBuildInputs = [ pkgs.python3 ];
      } ''
        python3 ${repoSource}/tests/system/test_device_policy.py
        python3 ${repoSource}/tests/system/test_usbguard_policy.py
        touch "$out"
      '';

      hermes-activation-vm = import ./system/hermes-activation-vm.nix { inherit self; };

      skill-path-audit = pkgs.runCommand "skill-path-audit-tests" {
        nativeBuildInputs = [ (pkgs.python3.withPackages (ps: [ ps.pyyaml ])) ];
      } ''
        python3 ${repoSource}/tests/system/test_skill_path_audit.py
        touch "$out"
      '';

      security-behavior-vm = import ./system/security-behavior-vm.nix {
        inherit pkgs;
        ports = self.nixosConfigurations.lfour.config.my.machine.ports;
      };

      libvirt-qemu-migration = pkgs.runCommand "libvirt-qemu-migration" {
        nativeBuildInputs = [ pkgs.python3 pkgs.bash pkgs.coreutils pkgs.util-linux pkgs.glibc.getent ];
      } ''
        python3 ${repoSource}/tests/system/test_libvirt_migration.py
        touch "$out"
      '';

      system-age-key = pkgs.runCommand "system-age-key" {
        nativeBuildInputs = [ pkgs.python3 pkgs.age ];
      } ''
        python3 ${repoSource}/tests/system/test_system_age_key.py
        touch "$out"
      '';

      proxy-core = self.nixosConfigurations.lfour.config.programs.clash-verge.package.managedCore;
      proxy-gost = self.nixosConfigurations.lfour.config.my.proxy.gostPackage;
      proxy-gui = self.nixosConfigurations.lfour.config.programs.clash-verge.package.guiDefaultsCheck;
    }
