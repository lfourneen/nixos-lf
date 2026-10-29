"""VM-only persistent guest flow used to prove policy revocation."""

from pathlib import Path
import socket
import socketserver
import sys
import time


if sys.argv[1] == "server":
    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            while data := self.request.recv(4096):
                self.request.sendall(data)
    with socketserver.ThreadingTCPServer(("0.0.0.0", 18081), Echo) as server:
        server.serve_forever()
else:
    target, label = sys.argv[2:]
    with socket.create_connection((target, 18081), timeout=3) as stream:
        stream.sendall(b"before")
        assert stream.recv(4096) == b"before"
        Path("/tmp/" + label + ".ready").touch()
        deadline = time.monotonic() + 30
        while not Path("/tmp/" + label + ".next").exists():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        stream.settimeout(2)
        try:
            stream.sendall(b"after")
            data = stream.recv(4096)
        except OSError:
            data = b""
        assert not data, "revoked established flow remained usable"
        Path("/tmp/" + label + ".result").write_text("blocked\n")
