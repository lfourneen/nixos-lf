{ pkgs, core }:

let
  python = pkgs.python3.withPackages (p: [ p.pyyaml ]);
  settings = pkgs.writeText "isolation-fixture.json" (builtins.toJSON {
    core = "${core}/bin/mihomo";
    python = "${python}/bin/python3";
    setpriv = "${pkgs.util-linux}/bin/setpriv";
    worker = ../../../system/programs/clash/service/source_worker.py;
    broker = ../../../system/programs/clash/service/source_broker.py;
    audit = ../../../system/programs/clash/service/subscription_audit.py;
    stage = ../../../system/programs/clash/service/clash-verge-stage.py;
    network = ../../../home/programs/firefox/network.py;
    tls = import ../../home/firefox/tls-fixture.nix { inherit pkgs; };
  });

in
pkgs.testers.runNixOSTest {
  name = "controlled-source-and-browser-network";
  nodes.machine = { ... }: {
    users.groups.clash-fetch = {};
    users.users.clash-fetch = { isSystemUser = true; group = "clash-fetch"; };
    users.groups.clash-core = { gid = 982; };
    users.users.clash-core = { isSystemUser = true; uid = 982; group = "clash-core"; };
    environment.systemPackages = [ python pkgs.curl pkgs.iproute2 pkgs.util-linux ];
    networking.extraHosts = "1.1.1.1 privacy-fixture.invalid";
    networking.firewall.enable = false;

    systemd.sockets.fixture-relay = {
      socketConfig.ListenStream = "/run/fixture-relay.sock";
    };

    systemd.services.fixture-relay.serviceConfig = {
      ExecStart = "${python}/bin/python3 ${../../../home/programs/firefox/network.py} host /run/relay-settings.json";
    };

    environment.etc."isolation-fixture.py".source = ./isolation_fixture.py;
    environment.etc."isolation-settings.json".source = settings;
    virtualisation.memorySize = 2048;
  };

  testScript = ''
    start_all()
    machine.wait_for_unit("multi-user.target")
    try:
        machine.succeed("${python}/bin/python3 /etc/isolation-fixture.py /etc/isolation-settings.json > /tmp/isolation.log 2>&1", timeout=180)
    except Exception:
        print(machine.execute("cat /tmp/isolation.log"))
        raise
    finally:
        machine.copy_from_vm("/tmp/isolation.log")
  '';
}
