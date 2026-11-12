{ pkgs, firefox }:

let
  python = pkgs.python3.withPackages (p: [ p.pygobject3 ]);
  tls = pkgs.runCommand "firefox-system-trust-fixture" {
    nativeBuildInputs = [ pkgs.openssl ];
  } ''
    mkdir -p "$out"
    cd "$out"
    openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.pem \
      -days 3650 -subj '/CN=Firefox sandbox system trust fixture' \
      -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign,cRLSign'
    openssl req -new -newkey rsa:2048 -nodes -keyout trusted.key -out trusted.csr \
      -subj '/CN=trusted.sandbox.fixture.test'
    cat > server.ext <<EOF
    basicConstraints=critical,CA:FALSE
    keyUsage=critical,digitalSignature,keyEncipherment
    extendedKeyUsage=serverAuth
    subjectAltName=DNS:trusted.sandbox.fixture.test
    EOF
    openssl x509 -req -in trusted.csr -CA ca.pem -CAkey ca.key -CAcreateserial \
      -out trusted.pem -days 3650 -extfile server.ext
    openssl req -x509 -newkey rsa:2048 -nodes -keyout untrusted.key -out untrusted.pem \
      -days 3650 -subj '/CN=untrusted.sandbox.fixture.test' \
      -addext 'subjectAltName=DNS:untrusted.sandbox.fixture.test'
    rm ca.key ca.srl trusted.csr server.ext
  '';

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
    users.users.lfour = { isNormalUser = true; uid = 1000; linger = true; };
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
    machine.wait_for_unit("user@1000.service")
    try:
        machine.wait_until_succeeds("test -S /run/user/1000/wayland-0 && test -S /run/user/1000/pulse/native", timeout=40)
        machine.succeed("runuser -u lfour -- env XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus ${python}/bin/python3 /etc/firefox-sandbox-test.py > /tmp/firefox-sandbox.log 2>&1", timeout=180)
    except Exception:
        print(machine.execute("tail -n 70 /tmp/firefox-sandbox.log; journalctl _UID=1000 --no-pager -n 50"))
        raise
    finally:
        machine.copy_from_vm("/tmp/firefox-sandbox.log")
    print(machine.succeed("tail -n 1 /tmp/firefox-sandbox.log"))
  '';
}
