"""Run the built core inside a VM network namespace, without internet nodes."""

import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time


def command(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=10, check=True).stdout


def receive(connection, length):
    value = b""
    while len(value) < length:
        part = connection.recv(length - len(value))
        if not part:
            raise AssertionError("SOCKS response closed early")
        value += part
    return value


def socks_request(length):
    with socket.create_connection(("127.0.0.1", 7897), timeout=3) as connection:
        connection.sendall(b"\x05\x01\x00")
        assert receive(connection, 2) == b"\x05\x00"
        connection.sendall(b"\x05\x01\x00\x03" + bytes([length]) + b"a" * length
                           + b"\x12\x34GET / HTTP/1.0\r\n\r\n")
        # Mihomo acknowledges SOCKS before routing. Consume the full IPv4
        # handshake, then wait for the REJECT rule to close the application
        # stream. The caller proves liveness with a fresh connection as well;
        # an EOF caused by a core panic alone must not count as success.
        assert receive(connection, 10)[:4] == b"\x05\x00\x00\x01"
        try:
            assert connection.recv(1) == b"", "REJECT returned application data"
        except ConnectionResetError:
            pass


def main(core, family):
    ipv6 = family == "dual"
    command("ip", "link", "set", "lo", "up")
    command("ip", "link", "add", "uplink", "type", "dummy")
    command("ip", "addr", "add", "192.0.2.2/24", "dev", "uplink")
    command("ip", "link", "set", "uplink", "up")
    command("ip", "route", "add", "default", "via", "192.0.2.1")
    if ipv6:
        command("ip", "-6", "addr", "add", "2001:db8:abcd::2/64", "dev", "uplink", "nodad")
        command("ip", "-6", "route", "add", "default", "via", "2001:db8:abcd::1")
    profile = {
        "mixed-port": 7897, "allow-lan": False, "ipv6": ipv6,
        "routing-mark": 6666, "mode": "rule", "rules": ["MATCH,REJECT"],
        "tun": {
            "enable": True, "device": "Mihomo", "stack": "mixed", "mtu": 1500,
            "auto-route": True, "auto-redirect": True, "strict-route": True,
            "auto-detect-interface": True, "iproute2-table-index": 2022,
            "dns-mode": "hijack", "dns-hijack": ["any:53", "tcp://any:53"],
            # Include IPv4 and IPv6 ranges ending at the maximum address.
            "route-exclude-address": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                                      "100.64.0.0/10", "fc00::/7", "fe80::/10", "224.0.0.0/4",
                                      "255.255.255.255/32", "ff00::/8"],
            "inet6-address": ["fdfe:dcba:9876::1/126"] if ipv6 else [],
        },
        "dns": {"enable": True, "listen": "127.0.0.1:1053", "ipv6": False,
                "enhanced-mode": "fake-ip", "fake-ip-range": "198.18.0.1/16",
                "fake-ip-filter": [], "nameserver": ["tls://1.1.1.1"]},
    }
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        (path / "config.json").write_text(json.dumps(profile))
        environment = dict(os.environ, SKIP_SYSTEM_IPV6_CHECK="1")
        # Avoid resolvectl altering the outer VM's resolved state. This test
        # exercises sing-tun's kernel routing/DNS path; resolver policy has its
        # own tests in the outer namespace.
        environment["PATH"] = os.environ["NETSEC_CORE_PATH"]
        with (path / "core.log").open("w+") as log:
            process = subprocess.Popen([core, "-d", directory, "-f", str(path / "config.json")],
                                       env=environment, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 15
                while True:
                    assert process.poll() is None, "core exited during startup"
                    if subprocess.run(["nft", "list", "table", "inet", "mihomo"],
                                      capture_output=True, timeout=2).returncode == 0:
                        break
                    assert time.monotonic() < deadline, "auto-redirect table absent"
                    time.sleep(0.1)
                command("ip", "link", "show", "dev", "Mihomo")
                command("ip", "route", "show", "table", "2022")
                destinations = ["203.0.113.53"] + (["2001:db8:ffff::53"] if ipv6 else [])
                for destination in destinations:
                    for protocol in ("+notcp", "+tcp"):
                        answer = command("dig", "+short", "+time=2", "+tries=1", protocol,
                                         "@" + destination, "captured.example", "A").strip()
                        assert ipaddress.ip_address(answer) in ipaddress.ip_network("198.18.0.0/16"), answer
                for length in (1, 252, 253, 254, 255):
                    socks_request(length)
                    assert process.poll() is None, "SOCKS request terminated core"
                socks_request(1)
                print(f"{family}: auto-redirect, max-address exclusions, UDP/TCP DNS and SOCKS boundaries passed")
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read(), file=sys.stderr)
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    main(*sys.argv[1:])
