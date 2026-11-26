"""Measure one UDP echo as an ordinary desktop socket in authorized B."""

from contextlib import ExitStack
import fcntl
import hashlib
import ipaddress
import json
from pathlib import Path
import secrets
import select
import socket
import subprocess
import sys
import tempfile
import time


def endpoint(settings):
    address = ipaddress.ip_address(settings['address'])
    port = settings['port']
    if (not address.is_global or address.is_multicast or getattr(address, 'scope_id', None)
            or getattr(address, 'ipv4_mapped', None) or type(port) is not int or not 1 <= port <= 65535):
        raise ValueError('expected a public literal UDP echo endpoint')
    return address, port


def echo(address, port, sent=lambda: None):
    nonce = secrets.token_bytes(24)
    family = socket.AF_INET if address.version == 4 else socket.AF_INET6
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as stream:
            stream.settimeout(2.5)
            stream.connect((str(address), port))
            stream.send(nonce)
            sent()
            return 'verified' if stream.recv(len(nonce) + 1) == nonce else 'no-reply'
    except (TimeoutError, ConnectionRefusedError):
        return 'no-reply'
    except OSError:
        return 'error'


def publish(settings, settings_path, root=Path('/run')):
    import state

    expected = hashlib.sha256(Path(settings_path).read_bytes()).hexdigest()

    def current():
        boot = state.boot_identity(root)
        info = state.readiness(root, boot_id=boot)
        if not info['ready']:
            return None
        binding = state.udp_probe_binding(root, boot, info['policy_version'], info,
                                          state.read(root / 'proxy-mode/source.json'))
        return binding if binding and binding['settings'] == expected else None

    directory = root / 'proxy-mode'
    with ExitStack() as cleanup:
        with (directory / 'lock').open('rb') as lock:
            deadline = time.monotonic() + 1.5
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        return
                    time.sleep(.05)
            binding = current()
            if binding is None:
                return
            # Keep the routing generation stable only until the datagram is sent.
            argv = [settings['setpriv'], f"--reuid={settings['uid']}", f"--regid={settings['gid']}",
                    '--clear-groups', '--no-new-privs', '--bounding-set=-all',
                    '--inh-caps=-all', '--ambient-caps=-all',
                    sys.executable, __file__, settings_path, '--echo']
            child = cleanup.enter_context(subprocess.Popen(argv, stdout=subprocess.PIPE, bufsize=0))
            if not select.select([child.stdout], [], [], 1)[0]:
                child.kill()
                child.wait()
                return
            first = child.stdout.readline()
        try:
            tail, _ = child.communicate(timeout=3)
            value = (tail if first == b'sent\n' else first).decode('ascii').strip()
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
            value = 'error'
    if value not in state.UDP_PROBE_RESULTS:
        value = 'error'
    if current() != binding:
        return
    record = {'binding': binding, 'result': value, 'monotonic_seconds': time.clock_gettime(time.CLOCK_BOOTTIME)}
    with tempfile.NamedTemporaryFile(mode='w', dir=directory, prefix='.udp-health-', delete=False) as stream:
        name = Path(stream.name)
        try:
            json.dump(record, stream)
            stream.flush()
            name.chmod(0o644)
            name.replace(directory / 'udp-health.json')
        finally:
            name.unlink(missing_ok=True)


if __name__ == '__main__':
    configuration = json.loads(Path(sys.argv[1]).read_text())
    address, port = endpoint(configuration)
    if '--echo' in sys.argv[2:]:
        print(echo(address, port, lambda: print('sent', flush=True)))
    elif '--validate' not in sys.argv[2:]:
        try:
            publish(configuration, sys.argv[1])
        except FileNotFoundError:
            pass
