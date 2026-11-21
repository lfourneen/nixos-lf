{ pkgs, firefox, identity, portal, routingPolicies, desktopIdentity }:

let
  desktop = desktopIdentity;
  runtime = "/run/user/${toString desktop.uid}";
  python = pkgs.python3.withPackages (p: [ p.pygobject3 ]);
  tls = import ./tls-fixture.nix { inherit pkgs; };

  manifest = pkgs.writeText "firefox-sandbox-test.json" (builtins.toJSON {
    package = firefox;
    identitySettings = identity.netsec.settings;
    identityPackage = identity.netsec.package;
    inherit routingPolicies;
    uid = desktop.uid;
    inherit (firefox.netsec) settings;
    driver = ./firefox_driver.py;
    guard = ../../../home/programs/firefox/guard.py;
    python = "${python}/bin/python3";
    gdbus = "${pkgs.glib.bin}/bin/gdbus";
    pactl = "${pkgs.pulseaudio}/bin/pactl";
    env = "${pkgs.coreutils}/bin/env";
    tlsDirectory = tls;
    tlsModule = ./firefox_tls_fixture.py;
  });

in
pkgs.testers.runNixOSTest {
  name = "firefox-outer-sandbox";
  nodes.machine = { ... }: {
    imports = [ ./firefox-desktop.nix ];
    _module.args.desktopTestUser = desktop.name;
    users.users.${desktop.name} = { isNormalUser = true; inherit (desktop) uid home; linger = true; };
    # Exercise NixOS's real /etc/static symlinks and NSS/p11-kit system trust.
    # The browser profile stays empty; do not import this CA with certutil.
    security.pki.certificateFiles = [ "${tls}/ca.pem" ];

    environment.systemPackages = [ python pkgs.glib pkgs.util-linux ];
    environment.etc."firefox-sandbox-test.json".source = manifest;
    environment.etc."firefox-sandbox-test.py".source = ./test_firefox_sandbox.py;
    systemd.services.firefox-mode-fixture.serviceConfig.ExecStart =
      "${python}/bin/python3 /etc/firefox-sandbox-test.py serve";
    virtualisation.memorySize = 3072;
    virtualisation.cores = 2;
  };

  testScript = ''
    start_all()
    machine.wait_for_unit("user@${toString desktop.uid}.service")
    try:
        machine.wait_until_succeeds("test -S ${runtime}/wayland-0 && test -S ${runtime}/pulse/native", timeout=40)
        machine.succeed("systemctl start firefox-mode-fixture")
        machine.wait_until_succeeds("test -S ${runtime}/clash-verge-rev/verge-mihomo.sock && test -S /run/clash-strict/proxy.sock")
        machine.succeed("runuser -u ${desktop.name} -- env HOME=${pkgs.lib.escapeShellArg desktop.home} XDG_RUNTIME_DIR=${runtime} WAYLAND_DISPLAY=wayland-0 DBUS_SESSION_BUS_ADDRESS=unix:path=${runtime}/bus ${python}/bin/python3 /etc/firefox-sandbox-test.py > /tmp/firefox-sandbox.log 2>&1", timeout=180)
    except Exception:
        print(machine.execute("tail -n 70 /tmp/firefox-sandbox.log; journalctl _UID=${toString desktop.uid} --no-pager -n 50"))
        raise
    finally:
        machine.copy_from_vm("/tmp/firefox-sandbox.log")
    print(machine.succeed("tail -n 1 /tmp/firefox-sandbox.log"))

    def desktop(command):
        return "${pkgs.util-linux}/bin/runuser -u ${desktop.name} -- ${pkgs.coreutils}/bin/env HOME=${pkgs.lib.escapeShellArg desktop.home} XDG_RUNTIME_DIR=${runtime} WAYLAND_DISPLAY=wayland-0 DBUS_SESSION_BUS_ADDRESS=unix:path=${runtime}/bus " + command

    def publish(state):
        machine.succeed("${python}/bin/python3 /etc/firefox-sandbox-test.py publish " + state)

    with subtest("production Firefox launchers bind to the ready B routing policy"):
        daily = "${firefox}/bin/firefox"
        identity = "${identity}/bin/firefox-identity"
        portal = "${portal}/bin/firefox-portal http://127.0.0.1/hello"
        publish("auto")
        machine.succeed(desktop(daily + " --check"))
        machine.fail(desktop(identity + " --check"))
        machine.fail(desktop(portal))
        for name, entry in [("daily", daily), ("identity", identity)]:
            publish("rules")
            machine.succeed(desktop(entry + " --check"))
            machine.succeed("systemd-run --no-block --collect --unit=fixture-browser " + desktop(entry + " about:blank"))
            try:
                generation = "${runtime}/firefox-managed/*/generation.json"
                machine.wait_until_succeeds("test $(find ${runtime}/firefox-managed -name generation.json | wc -l) = 1", timeout=30)
                remote = machine.succeed("${python}/bin/python3 /etc/firefox-sandbox-test.py remote-name " + name).strip()
                bus = desktop("${pkgs.glib.bin}/bin/gdbus call --session --dest org.freedesktop.DBus "
                    "--object-path /org/freedesktop/DBus --method org.freedesktop.DBus.")
                try:
                    machine.wait_until_succeeds(bus + "NameHasOwner " + remote + " | grep -q true", timeout=30)
                except Exception:
                    print(machine.execute(bus + "ListNames"))
                    raise
                for mode in ["rules", "proxy-only"]:
                    publish(mode)
                    machine.succeed(desktop(entry + " --check"))
                    machine.succeed(desktop(entry + " --new-window about:robots"), timeout=35)
                    machine.succeed("systemctl is-active fixture-browser && test $(ls " + generation + " | wc -l) = 1")
                publish("proxy-only wrong")
                assert "managed-policy differs" in machine.fail(desktop(entry + " --check") + " 2>&1")
                publish("proxy-only stale")
                assert "observation stale" in machine.fail(desktop(entry + " --check") + " 2>&1")
                publish("blocked")
                machine.fail(desktop(entry + " --check"))
                machine.fail(desktop(entry + " --new-window about:robots"))
            finally:
                machine.succeed("systemctl stop fixture-browser")
                machine.wait_until_succeeds("test $(find ${runtime}/firefox-managed -name generation.json | wc -l) = 0")

    with subtest("portal browser exits and clears its data when explicit direct intent is revoked"):
        publish("rules")
        machine.fail(desktop(portal))
        publish("blocked")
        machine.fail(desktop(portal))
        publish("direct")
        machine.succeed("systemd-run --no-block --collect --unit=fixture-portal " + desktop(portal))
        try:
            machine.wait_until_succeeds("test -f /run/firefox-portal-visited", timeout=60)
        except Exception:
            print(machine.execute("journalctl -u fixture-portal -u firefox-mode-fixture --no-pager -n 45"))
            raise
        machine.succeed("test $(find ${runtime} -maxdepth 1 -name 'firefox-portal-*' | wc -l) = 1")
        machine.wait_until_succeeds("grep -Fx 'Portal download fixture' ${runtime}/firefox-portal-*/downloads/portal-fixture.txt", timeout=30)
        machine.succeed("rm /run/proxy-mode/intent")
        machine.wait_until_succeeds("test $(find ${runtime} -maxdepth 1 -name 'firefox-portal-*' | wc -l) = 0", timeout=15)
        machine.succeed("test ! -e ${pkgs.lib.escapeShellArg "${desktop.home}/Downloads/portal-fixture.txt"}")
        machine.fail(desktop(portal))
        machine.succeed(desktop(daily + " --check"))
  '';
}
