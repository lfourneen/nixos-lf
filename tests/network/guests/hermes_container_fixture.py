"""Actual offline Docker image and restricted backend; isolated VM only."""
import json
import os
from pathlib import Path
import pwd
import shlex
import stat
import subprocess
import sys

ENDPOINT = 'unix:///run/hermes-containers/docker.sock'


def run(*args, okay=True, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=80, **kwargs)
    if okay:
        assert result.returncode == 0, (args, result.stdout, result.stderr)
    return result


def docker(*args, okay=True, user='hermes'):
    return run('runuser', '-u', user, '--', 'docker', '--host', ENDPOINT, *args, okay=okay)


def setup():
    # A runtime-only override permits a genuine legacy daemon in the VM. It
    # is removed after testing the production stop-before-start barrier.
    run('systemctl', 'start', 'user@988.service')
    dropin = Path('/run/user/988/systemd/user/docker.service.d')
    dropin.mkdir(parents=True)
    (dropin/'legacy.conf').write_text('[Unit]\nConditionUser=\nConditionUser=!root\n')
    userctl = ['systemctl', '--user', '--machine=hermes@.host']
    run(*userctl, 'daemon-reload')
    run(*userctl, 'start', 'docker.service')
    run(*userctl, 'is-active', 'docker.service')
    marker = Path('/var/lib/hermes/.local/share/retained-container-data')
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('synthetic old state')
    run('systemctl', 'start', 'hermes-containers.service')
    assert run(*userctl, 'is-active', 'docker.service', okay=False).returncode
    (dropin/'legacy.conf').unlink()
    run(*userctl, 'daemon-reload')
    run(*userctl, 'start', 'docker.service')
    assert run(*userctl, 'is-active', 'docker.service', okay=False).returncode
    assert marker.read_text() == 'synthetic old state'
    owner = pwd.getpwnam('hermes-containers').pw_uid
    value = os.stat('/run/hermes-containers/docker.sock')
    assert value.st_uid == owner and stat.S_IMODE(value.st_mode) == 0o660, value
    assert docker('info', okay=False, user='lfour').returncode
    info = json.loads(docker('info', '--format', '{{json .}}').stdout)
    assert 'name=rootless' in info['SecurityOptions'], info['SecurityOptions']
    assert info['DockerRootDir'] == '/var/lib/hermes-containers/docker'
    docker('load', '-i', '/etc/hermes-network-image.tar')
    print('Actual legacy daemon retired; old data retained; dedicated rootless daemon and socket ACL passed')


def probes(mode):
    assert docker('version', '--format', '{{.Server.Version}}').stdout.strip()
    if mode == 'A':
        # The other role's private grant must be live before checking that the
        # container backend cannot use that same destination and port.
        code = 'import socket; s=socket.create_connection(("100.67.0.1",18081),2); s.sendall(b"echo"); assert s.recv(64)==b"echo"'
        run('runuser', '-u', 'guestnet', '--', 'python3', '-c', code)
    # Hermes itself keeps the host proxy path; only container transport has
    # the dedicated UID. Group membership grants no network exception in C.
    if mode in ('B', 'C'):
        result = run('runuser', '-u', 'hermes', '--', 'curl', '--noproxy', '', '--proxy',
                     'http://127.0.0.1:33332', '--max-time', '4', '-fsS',
                     'http://node.fixture.example:8080/', okay=False)
        assert (result.returncode == 0) == (mode == 'B'), (mode, result.stderr)
    def container(command, allowed):
        result = docker('run', '--rm', '--pull=never', '--dns=8.8.8.8', 'network-fixture:local',
                        '/bin/bash', '-c', command, okay=False)
        assert result.returncode != 125, ('container failed to start', mode, result.stderr)
        assert (result.returncode == 0) == allowed, (mode, command, result.stdout, result.stderr)

    container("curl --noproxy '*' --max-time 3 -fsS http://203.0.113.10:8080/ >/dev/null", mode != 'C')
    for address, allowed in [('203.0.113.10', mode != 'C'), ('100.67.0.1', False),
                             ('2001:db8:dead::1', False), ('fd42:feed::1', False)]:
        family = 'AF_INET6' if ':' in address else 'AF_INET'
        code = (f'import socket; s=socket.socket(socket.{family},socket.SOCK_DGRAM); '
                f's.settimeout(2); s.sendto(b"echo",({address!r},18082)); assert s.recv(64)==b"echo"')
        container('python3 -c '+shlex.quote(code), allowed)
    # guestnet has a private grant in this VM; Hermes must not borrow it.
    for address in ['100.67.0.1', '100.67.0.3', '169.254.169.254', '198.18.0.9', '10.0.2.2']:
        container(f"curl --noproxy '*' --max-time 2 -fsS http://{address}:18081/ >/dev/null", False)
    # Explicit host proxy settings still face the original UID/target guard.
    for port in (33332, 7897):
        container(f"curl --noproxy '' --proxy http://10.0.2.2:{port} --max-time 2 -fsS http://203.0.113.10:8080/ >/dev/null", False)
    if mode != 'C':
        container("python3 -c 'import socket; assert socket.gethostbyname(\"public.guest.fixture.example\") == \"203.0.113.10\"'", True)
    print('Real Hermes containers:', mode, 'public policy, private/proxy/metadata refusal and real-address DNS passed')


if __name__ == '__main__':
    setup() if sys.argv[1] == 'setup' else probes(sys.argv[1])
