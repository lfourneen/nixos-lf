{
  description = "NixOS Configuration";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

    nixpkgs-unstable.url = "github:NixOS/nixpkgs/nixos-unstable";

    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    impermanence = {
      url = "github:nix-community/impermanence";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    home-manager = {
      url = "github:nix-community/home-manager/release-26.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    sops-nix = {
      url = "github:Mic92/sops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    mcp-nixos = {
      url = "github:utensils/mcp-nixos";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    noctalia.url = "github:noctalia-dev/noctalia/cachix";

    nix-flatpak.url = "github:gmodena/nix-flatpak";

    hermes-agent.url = "github:NousResearch/hermes-agent/v0.23.0";

    llm-agents.url = "github:numtide/llm-agents.nix";
  };

  outputs = { self, nixpkgs, home-manager, ... }@inputs:

  let
    system = "x86_64-linux";

  in
  {
    checks.${system} = let
      pkgs = nixpkgs.legacyPackages.${system};
      strictDnsConfig = (self.nixosConfigurations.lfour.extendModules {
        modules = [ { my.proxy.dnsEncryption = "strict"; } ];
      }).config;
    in {
      openidea-tools = self.nixosConfigurations.lfour.pkgs.openidea.tests.repair-tools;
      proxy-regressions = pkgs.runCommand "proxy-regressions" {
        nativeBuildInputs = [ pkgs.python3 pkgs.bash pkgs.jq ];
      } ''
        export NETSEC_MODULES=${./system/programs/systemd}
        export PYTHONDONTWRITEBYTECODE=1
        python3 ${./tests/netsec/test_netsec.py}
        touch "$out"
      '';
      proxy-rules = self.nixosConfigurations.lfour.config.my.proxy.expectedRules;
      proxy-dns-strict-rules = strictDnsConfig.my.proxy.expectedRules;
      proxy-dns-config = pkgs.runCommand "dns-policy-config" { } ''
        ${pkgs.bash}/bin/bash -n ${self.nixosConfigurations.lfour.config.systemd.services.dns-upstream.serviceConfig.ExecStart}
        ${pkgs.bash}/bin/bash -n ${strictDnsConfig.systemd.services.dns-upstream.serviceConfig.ExecStart}
        touch "$out"
      '';
      proxy-egress-config = self.nixosConfigurations.lfour.config.my.hardening.directEgress.configCheck;
      tailscale-transport = self.nixosConfigurations.lfour.config.my.hardening.tailscale.transportCheck;
      proxy-tailscale-vm = import ./tests/netsec/tailscale-vm.nix {
        inherit pkgs;
        tailscale = self.nixosConfigurations.lfour.config.services.tailscale.package;
      };
      proxy-egress-vm = import ./tests/netsec/tailscale-vm.nix {
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
      browser-privacy = import ./tests/netsec/firefox.nix {
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
      browser-ech-vm = import ./tests/netsec/control-vm.nix {
        inherit pkgs;
        clashPackage = self.nixosConfigurations.lfour.config.programs.clash-verge.package;
        ech = {
          http-proxy = self.nixosConfigurations.lfour.config.home-manager.users.lfour.programs.firefox.finalPackage;
          transparent = (self.nixosConfigurations.lfour.extendModules {
            modules = [ { home-manager.users.lfour.my.firefox.networkPath = "transparent"; } ];
          }).config.home-manager.users.lfour.programs.firefox.finalPackage;
        };
      };
      proxy-profile = import ./tests/netsec/profile.nix {
        inherit pkgs;
        config = self.nixosConfigurations.lfour.config;
        uniformProxyConfig = (self.nixosConfigurations.lfour.extendModules {
          modules = [ { my.proxy.proxyOnly = true; } ];
        }).config;
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
      proxy-network-vm = import ./tests/netsec/vm.nix {
        inherit pkgs;
        core = self.nixosConfigurations.lfour.config.programs.clash-verge.package.managedCore;
        clashPackage = self.nixosConfigurations.lfour.config.programs.clash-verge.package;
      };
      proxy-control-vm = import ./tests/netsec/control-vm.nix {
        inherit pkgs;
        clashPackage = self.nixosConfigurations.lfour.config.programs.clash-verge.package;
      };
      proxy-dns-strict-vm = import ./tests/netsec/control-vm.nix {
        inherit pkgs;
        clashPackage = self.nixosConfigurations.lfour.config.programs.clash-verge.package;
        strictDns = true;
      };
      proxy-egress-control-vm = import ./tests/netsec/control-vm.nix {
        inherit pkgs;
        clashPackage = self.nixosConfigurations.lfour.config.programs.clash-verge.package;
        restricted = true;
      };
      proxy-control = pkgs.runCommand "proxy-control-regressions" {
        nativeBuildInputs = [ pkgs.python3 ];
      } ''
        export PYTHONDONTWRITEBYTECODE=1
        python3 ${./tests/netsec/test_control.py} ${./system/programs/clash/clash-verge-stage.py}
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
      assert small.programs.clash-verge.package == c.programs.clash-verge.package;
      assert broad.programs.clash-verge.package == c.programs.clash-verge.package;
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
    };

    nixosConfigurations.lfour = nixpkgs.lib.nixosSystem {
      inherit system;

      specialArgs = { inherit inputs; };

      modules = [
        # System config
        ./system

        # Proper nixpkgs configuration module
        {
          nixpkgs = {
            overlays = [
              # Custom overlays
              (import ./overlays)

              # MCP NixOS overlay
              (final: prev: {
                mcp-nixos = inputs.mcp-nixos.packages.${prev.stdenv.hostPlatform.system}.default;
              })

              # LLM agents overlay
              (final: prev: {
                dsh = inputs.llm-agents.packages.${prev.stdenv.hostPlatform.system}.dsh;
                #oh-my-opencode = inputs.llm-agents.packages.${prev.stdenv.hostPlatform.system}.oh-my-opencode;
              })

              # Shared nixpkgs-unstable as pkgs.unstable.*
              (final: prev: {
                unstable = import inputs.nixpkgs-unstable {
                  system = prev.stdenv.hostPlatform.system;
                  config = prev.config // {
                    allowUnfree = true;
                  };
                };
              })
            ];
            config.allowUnfree = true;
          };
        }

        # Home Manager Integration
        home-manager.nixosModules.home-manager
        {
          home-manager = {
            useGlobalPkgs = true;
            useUserPackages = true;
            backupFileExtension = "backup";

            extraSpecialArgs = {
              inherit inputs;

            };
            users.lfour = {
              imports = [ ./home ];
            };
          };
        }

        # File System
        inputs.disko.nixosModules.default
        inputs.impermanence.nixosModules.impermanence

        # Sops(secrets managemnet)
        inputs.sops-nix.nixosModules.sops

        # Flatpak
        inputs.nix-flatpak.nixosModules.nix-flatpak

        # Hermes Agent
        inputs.hermes-agent.nixosModules.default
      ];
    };
  };
}
