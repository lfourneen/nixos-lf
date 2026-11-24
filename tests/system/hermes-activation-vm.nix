# Replay the effective Hermes activation with disposable sentinel files.
{
  self ? builtins.getFlake (toString ../..),
}:

let
  pkgs = self.nixosConfigurations.lfour.pkgs;
  inputs = self.inputs;

  fixtureDefaults = pkgs.writeTextDir "hermes_cli/config_defaults.py" ''
    DEFAULT_CONFIG = {"_config_version": 1}
  '';

  fixturePython = pkgs.runCommand "hermes-fixture-python" {
    nativeBuildInputs = [ pkgs.makeWrapper ];
  } ''
    mkdir -p "$out/bin"
    makeWrapper ${pkgs.python3}/bin/python3 "$out/bin/python3" \
      --prefix PYTHONPATH : ${fixtureDefaults}
  '';

  testPython = pkgs.python3.withPackages (p: [ p.mcp p.pyyaml ]);
  proxy = import ../../system/config/network/proxy/env.nix { inherit (pkgs) lib; };
  trustKeys = pkgs.writeText "hermes-trust-keys.json" (builtins.toJSON proxy.trustOverrideNames);

  hostListeners = pkgs.writeText "hermes-host-listeners.py" ''
    import os
    from pathlib import Path
    from select import select
    import socket
    import sys

    def listen(family, address):
        server = socket.socket(family)
        if family == socket.AF_UNIX and not address.startswith("\0"):
            Path(address).parent.mkdir(parents=True, exist_ok=True)
        server.bind(address)
        server.listen()
        return server

    Path("/var/lib/hermes/workspace/time-host-fixture").touch()
    listeners = [
        listen(socket.AF_UNIX, path) for path in (
            "/run/user/1000/bus", "/run/hermes-containers/docker.sock",
            "/run/clash-verge-rev/core/mihomo.sock", "\0hermes-time-fixture",
        )
    ] + [listen(socket.AF_INET, ("127.0.0.1", 9222)), listen(socket.AF_INET6, ("::1", 9222))]
    proxy = listen(socket.AF_INET, ("127.0.0.1", int(sys.argv[1])))
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
        notify.sendto(b"READY=1", os.environ["NOTIFY_SOCKET"])
    while True:
        for server in select(listeners + [proxy], [], [])[0]:
            connection, _ = server.accept()
            with connection:
                if server == proxy:
                    connection.settimeout(2)
                    with connection.makefile("rb") as request:
                        absolute = request.readline().startswith(b"GET http://gost-fixture.invalid/")
                        if absolute:
                            for line in request:
                                if line == b"\r\n":
                                    break
                            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 14\r\n\r\napproved-proxy")
  '';

  probeSkill = pkgs.writeText "probe-hermes-skill.py" ''
    import errno
    import os
    from pathlib import Path
    import sys

    skills = Path(os.environ["HERMES_HOME"]) / "skills"
    skill = skills / "agently-mail"
    deployed = skill / "SKILL.md"
    expected = Path("${../../system/programs/hermes/hermes-skills/agently-mail/SKILL.md}")
    assert os.geteuid() != 0
    assert deployed.read_bytes() == expected.read_bytes()
    assert os.statvfs(deployed).f_flag & os.ST_RDONLY

    def denied(action, errors):
        try:
            action()
        except OSError as exc:
            assert exc.errno in errors, exc
        else:
            raise AssertionError("managed skill path allowed a mutation")

    denied(lambda: deployed.open("ab").close(), {errno.EACCES, errno.EROFS})
    denied(lambda: skill.rename(skills / "renamed-agently"), {errno.EBUSY})
    denied(lambda: skills.rename(skills.with_name("renamed-skills")), {errno.EBUSY})
    (skills / "fixture-writable").touch()
    Path("/var/lib/hermes/workspace", sys.argv[1]).touch()
  '';

  layout = [
    [ "/var/lib/hermes/.hermes" "700" ]
    [ "/var/lib/hermes/home" "700" ]
    [ "/var/lib/hermes/workspace" "2770" ]
    [ "/var/lib/hermes/.local" "700" ]
    [ "/var/lib/hermes/.config" "700" ]
    [ "/var/lib/hermes/.cache" "700" ]
    [ "/var/lib/hermes/.playwright-profiles" "700" ]
    [ "/var/lib/hermes/.hermes/cron" "700" ]
    [ "/var/lib/hermes/.hermes/sessions" "700" ]
    [ "/var/lib/hermes/.hermes/logs" "700" ]
    [ "/var/lib/hermes/.hermes/memories" "700" ]
    [ "/var/lib/hermes/.hermes/plugins" "700" ]
    [ "/var/lib/hermes/.hermes/skills" "700" ]
  ];

  fixture =
    { config, lib, ... }:
    let
      mode = if config.my.proxy.transparentApplications then "transparent" else "compat";
      ca.SSL_CERT_FILE = config.security.pki.caBundle;
      approvedProxy = proxy.gostHttpSlash config.my.machine.ports.gostHttp;
      gatewayEnv = ca // lib.optionalAttrs (mode == "compat") {
        HTTP_PROXY = approvedProxy;
        HTTPS_PROXY = approvedProxy;
        http_proxy = approvedProxy;
        https_proxy = approvedProxy;
        NO_PROXY = proxy.noProxy {
          privateV4 = config.my.machine.privateV4;
          extra = [ "192.168.1.1" "*.local" ];
        };
      };
      expected = pkgs.writeText "hermes-expected-env.json" (builtins.toJSON [
        proxy.trustOverrideNames gatewayEnv ca
      ]);
      injection = lib.genAttrs proxy.trustOverrideNames (_: "fixture");
      injectEnv = {
        PassEnvironment = proxy.trustOverrideNames ++ [ "MANAGER_PROBE" ];
        EnvironmentFile = "/etc/hermes-env-fixture";
      };
    in
    {
      imports = [
        ../../system/config/machine.nix
        inputs.hermes-agent.nixosModules.default
        ../../system/programs/hermes/hermes.nix
        ../../system/programs/hermes/time.nix

        # Host facts that this repository's configuration normally provides.
        {
          options.sops.templates."hermes.env".path = lib.mkOption {
            type = lib.types.str;
            default = "/run/secrets/rendered/hermes.env";
          };
          options.my.proxy.transparentApplications = lib.mkEnableOption "transparent application routing";
          options.my.proxy.fakeIpNet = lib.mkOption {
            type = lib.types.str;
            default = "198.18.0.0/16";
          };
        }
      ];

      config = {
        system.stateVersion = "26.05";
        time.timeZone = "Pacific/Auckland";
        virtualisation.memorySize = 2048;
        services.hermes-agent.package = lib.mkForce (lib.makeOverridable
          (_: (pkgs.writeShellScriptBin "hermes" ''
            exec ${lib.getExe testPython} ${./hermes-startup-probe.py} ${expected} ${mode} "$@"
          '').overrideAttrs (_: {
            passthru.hermesVenv = fixturePython;
          })) { });
        services.hermes-agent.extraPackages = lib.mkForce [ ];
        services.hermes-agent.restart = lib.mkForce "no";
        users.users.lfour = { uid = 1000; isNormalUser = true; extraGroups = [ "hermes" ]; };
        systemd.managerEnvironment = injection // { MANAGER_PROBE = "present"; };
        environment.etc."hermes-env-fixture".text = lib.concatStringsSep "\n" (
          lib.mapAttrsToList (key: value: "${key}=${value}") (injection // { FILE_PROBE = "present"; })
        );
        systemd.services.hermes-fixture-listeners = {
          wantedBy = [ "multi-user.target" ];
          before = [ "hermes-agent.service" ];
          serviceConfig.Type = "notify";
          script = ''
            ${lib.getExe' pkgs.util-linux "setpriv"} --reuid=lfour --regid=users --init-groups \
              ${lib.getExe' pkgs.coreutils "install"} -D -m 0600 ${pkgs.writeText "firefox-fixture-prefs.js" "// fixture"} \
              /home/lfour/.mozilla/firefox/fixture/prefs.js
            exec ${lib.getExe pkgs.python3} ${hostListeners} ${toString config.my.machine.ports.gostHttp}
          '';
        };
        systemd.services.hermes-agent = {
          postStart = "${lib.getExe pkgs.python3} ${probeSkill} agent-skill-ready";
          serviceConfig = injectEnv // {
            EnvironmentFile = lib.mkForce [ config.sops.templates."hermes.env".path injectEnv.EnvironmentFile ];
          };
        };
        systemd.services.hermes-backend = {
          postStart = "${lib.getExe pkgs.python3} ${probeSkill} backend-skill-ready";
          serviceConfig = injectEnv;
        };
        systemd.services."hermes-time@".serviceConfig = injectEnv // {
          ExecStartPre = "${lib.getExe pkgs.python3} ${./hermes-time-probe.py} ${toString config.my.machine.uids.hermesTime} ${trustKeys}";
        };

        # The service unit uses this file as EnvironmentFile. It holds a dummy
        # value for the test; the real secret comes from sops.
        systemd.tmpfiles.rules = [
          "d /run/secrets/rendered 0755 root root -"
          "f /run/secrets/rendered/hermes.env 0600 hermes hermes - DEEPSEEK_API_KEY=test"
        ];
      };
    };
in
pkgs.testers.runNixOSTest {
  name = "hermes-activation";

  node.specialArgs = { inherit inputs; };
  nodes.machine = fixture;
  nodes.transparent.imports = [ fixture { my.proxy.transparentApplications = true; } ];

  testScript = ''
    layout = ${builtins.toJSON layout}

    start_all()
    machine.wait_for_unit("multi-user.target")

    def check_layout():
        for path, mode in layout:
            actual = machine.succeed(f"stat -c '%a %U %G' {path}").strip()
            assert actual == f"{mode} hermes hermes", (path, actual)
        marker = machine.succeed("cat /var/lib/hermes/.state-version").strip()
        assert marker == "4", marker
        actual = machine.succeed(
            "stat -c '%a %U %G' /var/lib/hermes/.state-version"
        ).strip()
        assert actual == "644 root root", actual
        actual = machine.succeed(
            "stat -c '%a %U %G' /var/lib/hermes/.gitconfig"
        ).strip()
        assert actual == "640 root hermes", actual

    def backups():
        return machine.succeed("ls -1 /var/lib/hermes/.backup").strip().split()

    def snapshot():
        return machine.succeed(
            "find /root/sentinel -exec stat -c '%a %u %g %n' {} +; "
            "find /root/sentinel -type f -exec sha256sum {} +"
        )

    with subtest("activation creates the verified layout"):
        check_layout()
        assert machine.succeed("stat -c '%U %G %a' /var/lib/hermes").strip() == "root hermes 750"
        machine.fail("su -s /bin/sh hermes -c 'mv /var/lib/hermes/workspace /var/lib/hermes/replaced'")
        machine.fail("su -s /bin/sh lfour -c 'touch /var/lib/hermes/.hermes/injected'")
        machine.succeed("su -s /bin/sh lfour -c 'touch /var/lib/hermes/workspace/shared'")
        entries = backups()
        assert len(entries) == 1, entries
        manifest = "/var/lib/hermes/.backup/" + entries[0] + "/manifest.txt"
        actual = machine.succeed(f"stat -c '%a %U %G' {manifest}").strip()
        assert actual == "600 root root", actual
        previous_paths = [
            line.split("\t", 1)[0] for line in machine.succeed(f"cat {manifest}").splitlines()
        ]
        assert "/var/lib/hermes" in previous_paths, previous_paths

    with subtest("a second activation is idempotent"):
        machine.succeed("/run/current-system/activate")
        check_layout()
        assert len(backups()) == 1, backups()

    with subtest("real time MCP and both launch environments"):
        for vm in (machine, transparent):
            ready = "/var/lib/hermes/workspace"
            vm.wait_until_succeeds(f"test -f {ready}/time-mcp-ready -a -f {ready}/backend-env-ready", timeout=30)
            vm.succeed("${pkgs.python3}/bin/python3 ${./hermes-time-probe.py} host")
            assert vm.succeed("stat -c '%a %U %G' /run/hermes-time/mcp.sock").strip() == "660 root hermes-time-clients"
            vm.fail("su -s /bin/sh lfour -c '${pkgs.socat}/bin/socat -u /dev/null UNIX-CONNECT:/run/hermes-time/mcp.sock'")
            vm.succeed("""
                set -eu
                pid=$(systemctl show 'hermes-time@*.service' -p MainPID --value)
                test \"$(stat -c %u /proc/$pid)\" = 1201
                test \"$(stat -Lc %d:%i /proc/$pid/cwd)\" = \"$(stat -Lc %d:%i /proc/$pid/root/work)\"
                test \"$(readlink /proc/$pid/fd/0)\" = \"$(readlink /proc/$pid/fd/1)\"
                test $(ls /proc/$pid/fd | wc -l) -ge 3
                if readlink /proc/$pid/fd/* | grep -Eq '^/(home|var|run)/'; then exit 1; fi
                ${pkgs.python3}/bin/python3 ${./hermes-time-probe.py} process-env \"$pid\" ${trustKeys}
                bridge=$(pgrep -P \"$(systemctl show hermes-agent -p MainPID --value)\" -x socat)
                test -z \"$(tr '\\0' '\\n' </proc/$bridge/environ)\"
            """)

    with subtest("managed skill and its parent are pinned inside both units"):
        ready = "/var/lib/hermes/workspace"
        machine.wait_until_succeeds(f"test -f {ready}/agent-skill-ready", timeout=10)
        machine.wait_until_succeeds(f"test -f {ready}/backend-skill-ready", timeout=10)
        host_skill = "/var/lib/hermes/.hermes/skills/agently-mail/SKILL.md"
        machine.succeed(f"su -s /bin/sh hermes -c 'echo inert-copy > {host_skill}'")
        machine.succeed(f"rm {ready}/agent-skill-ready {ready}/backend-skill-ready")
        machine.succeed("systemctl restart hermes-agent.service hermes-backend.service")
        machine.succeed(f"test -f {ready}/agent-skill-ready -a -f {ready}/backend-skill-ready")
        assert machine.succeed(f"cat {host_skill}").strip() == "inert-copy"
        audit = machine.succeed("su -s /bin/sh hermes -c '/run/current-system/sw/bin/hermes-skill-audit'")
        assert '"status": "content-mismatch"' in audit, audit
        assert "inert-copy" not in audit, audit
        machine.succeed("/run/current-system/activate")

    with subtest("legacy root-owned document parents permit unprivileged rendering"):
        parent = "/var/lib/hermes/.hermes/skills/agently-mail"
        target = parent + "/SKILL.md"
        machine.succeed(f"chown root:root {parent} {target}; chmod 0555 {parent}; chmod 0444 {target}")
        machine.fail(f"su -s /bin/sh hermes -c 'install -m 0640 /etc/hostname {target}'")
        machine.succeed("echo 3 > /var/lib/hermes/.state-version")
        machine.succeed("/run/current-system/activate")
        assert machine.succeed(f"stat -c '%U %a' {parent}").strip() == "hermes 700"
        assert machine.succeed(f"stat -c '%U %a' {target}").strip() == "hermes 640"
        assert len(backups()) == 2, backups()
        machine.succeed("/run/current-system/activate")
        assert len(backups()) == 2, backups()
        check_layout()

    with subtest("a directory symlink fails activation"):
        machine.succeed("mkdir -p /root/sentinel")
        machine.succeed("cp -a /var/lib/hermes/workspace /root/sentinel/workspace")
        machine.succeed("echo sentinel > /root/sentinel/workspace/data")
        machine.succeed("chmod 2750 /root/sentinel/workspace")
        machine.succeed("chmod 0640 /root/sentinel/workspace/data")
        before = snapshot()
        machine.succeed("mv /var/lib/hermes/workspace /var/lib/hermes/workspace.saved")
        machine.succeed("ln -s /root/sentinel/workspace /var/lib/hermes/workspace")
        machine.fail("/run/current-system/activate")
        assert snapshot() == before, (before, snapshot())
        machine.succeed("rm /var/lib/hermes/workspace")
        machine.succeed("mv /var/lib/hermes/workspace.saved /var/lib/hermes/workspace")
        machine.succeed("/run/current-system/activate")
        check_layout()

    with subtest("a file symlink fails activation"):
        machine.succeed("cp -a /var/lib/hermes/.gitconfig /root/sentinel/gitconfig")
        before = snapshot()
        machine.succeed("rm /var/lib/hermes/.gitconfig")
        machine.succeed("ln -s /root/sentinel/gitconfig /var/lib/hermes/.gitconfig")
        machine.fail("/run/current-system/activate")
        assert snapshot() == before, (before, snapshot())
        machine.succeed("rm /var/lib/hermes/.gitconfig")
        machine.succeed("cp -a /root/sentinel/gitconfig /var/lib/hermes/.gitconfig")
        machine.succeed("chown root:hermes /var/lib/hermes/.gitconfig")
        machine.succeed("/run/current-system/activate")
        check_layout()
  '';
}
