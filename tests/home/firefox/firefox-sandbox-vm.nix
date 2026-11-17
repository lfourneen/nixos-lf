{ pkgs, firefox, desktopIdentity }:

let
  desktop = desktopIdentity;
  runtime = "/run/user/${toString desktop.uid}";
  python = pkgs.python3.withPackages (p: [ p.pygobject3 ]);
  tls = import ./tls-fixture.nix { inherit pkgs; };

  manifest = pkgs.writeText "firefox-sandbox-test.json" (builtins.toJSON {
    package = firefox;
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
    virtualisation.memorySize = 3072;
    virtualisation.cores = 2;
  };

  testScript = ''
    start_all()
    machine.wait_for_unit("user@${toString desktop.uid}.service")
    try:
        machine.wait_until_succeeds("test -S ${runtime}/wayland-0 && test -S ${runtime}/pulse/native", timeout=40)
        machine.succeed("runuser -u ${desktop.name} -- env HOME=${pkgs.lib.escapeShellArg desktop.home} XDG_RUNTIME_DIR=${runtime} WAYLAND_DISPLAY=wayland-0 DBUS_SESSION_BUS_ADDRESS=unix:path=${runtime}/bus ${python}/bin/python3 /etc/firefox-sandbox-test.py > /tmp/firefox-sandbox.log 2>&1", timeout=180)
    except Exception:
        print(machine.execute("tail -n 70 /tmp/firefox-sandbox.log; journalctl _UID=${toString desktop.uid} --no-pager -n 50"))
        raise
    finally:
        machine.copy_from_vm("/tmp/firefox-sandbox.log")
    print(machine.succeed("tail -n 1 /tmp/firefox-sandbox.log"))
  '';
}
