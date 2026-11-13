"""Local HTTPS origins and a closed CONNECT relay for the sandbox trust test."""
from socketserver import BaseRequestHandler, ThreadingTCPServer
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import select
import socket
import ssl
import threading


def start(directory):
    directory = Path(directory)

    class Origin(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"Firefox system trust fixture"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    origins = {}
    for name in ("trusted", "untrusted", "expired"):
        origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(directory / (name + ".pem"), directory / (name + ".key"))
        origin.socket = context.wrap_socket(origin.socket, server_side=True)
        origins[name + ".sandbox.fixture.test"] = origin
        threading.Thread(target=origin.serve_forever, daemon=True).start()
    origins["wrong.sandbox.fixture.test"] = origins["trusted.sandbox.fixture.test"]

    plaintext = ThreadingHTTPServer(('127.0.0.1', 0), Origin)
    origins['plaintext.sandbox.fixture.test'] = plaintext
    threading.Thread(target=plaintext.serve_forever, daemon=True).start()
    class Truncate(BaseRequestHandler):
        def handle(self):
            self.request.recv(4096)
    truncated = ThreadingTCPServer(('127.0.0.1', 0), Truncate)
    origins['truncated.sandbox.fixture.test'] = truncated
    threading.Thread(target=truncated.serve_forever, daemon=True).start()

    class Relay(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            host, _, port = self.path.rpartition(":")
            if host not in origins or port != "443":
                self.send_error(502)
                return
            with socket.create_connection(origins[host].server_address, timeout=5) as peer:
                self.send_response(200)
                self.end_headers()
                sockets = [self.connection, peer]
                while True:
                    ready, _, _ = select.select(sockets, [], [], 15)
                    if not ready:
                        return
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        (peer if source is self.connection else self.connection).sendall(data)

        def do_GET(self):
            self.send_error(502)

        def log_message(self, *args):
            pass

    relay = ThreadingHTTPServer(("127.0.0.1", 33332), Relay)
    threading.Thread(target=relay.serve_forever, daemon=True).start()
    return relay, origins
