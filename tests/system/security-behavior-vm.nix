{ pkgs, ports }:

let
  reserved = import ../../system/config/network/lib/reserved-ports.nix { inherit (pkgs) lib; inherit ports; };

  probe = pkgs.writeText "bind-probe.py" ''
    import errno, socket
    for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        for protocol in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            for port in ${builtins.toJSON (map pkgs.lib.toInt reserved)}:
                with socket.socket(family, protocol) as listener:
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                    try:
                        listener.bind((address, port))
                    except OSError as error:
                        assert error.errno in (errno.EPERM, errno.EACCES), error
                    else:
                        raise AssertionError((family, protocol, port))
            with socket.socket(family, protocol) as listener:
                listener.bind((address, 18099))
  '';

  crash = pkgs.runCommandCC "synthetic-crash" { } ''
    cat > crash.c <<'EOF'
    #include <signal.h>
    #include <stdlib.h>
    #include <string.h>
    int main(void) {
      volatile char *marker = malloc(4096);
      strcpy((char *)marker, "SYNTHETIC_PRIVATE_MEMORY_7a4c91");
      raise(SIGABRT);
      return marker[0];
    }
    EOF
    $CC -O0 crash.c -o "$out"
  '';

in
pkgs.testers.runNixOSTest {
  name = "security-behavior";

  nodes.machine = {
    imports = [ ../../system/programs/systemd/coredump.nix ];

    systemd.slices.hermes.sliceConfig.SocketBindDeny = reserved;
    systemd.services.bind-probe = {
      serviceConfig = {
        Type = "oneshot";
        User = "nobody";
        Slice = "hermes.slice";
        ExecStart = "${pkgs.python3}/bin/python3 ${probe}";
      };
    };
  };

  testScript = ''
    start_all()
    machine.wait_for_unit("multi-user.target")
    machine.succeed("systemctl start bind-probe.service")
    machine.succeed("systemctl restart bind-probe.service")
    machine.fail("systemd-run --wait --unit=synthetic-crash --property=LimitCORE=infinity ${crash}")
    machine.wait_until_succeeds("journalctl -b -t systemd-coredump --no-pager | grep synthetic-crash")
    machine.succeed("test -z \"$(find /var/lib/systemd/coredump -type f -print -quit)\"")
    machine.fail("journalctl -b --no-pager -o cat | grep -F SYNTHETIC_PRIVATE_MEMORY_7a4c91")
  '';
}
