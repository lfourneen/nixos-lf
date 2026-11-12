"""VM-only transparent TCP endpoint and established-flow test client."""

from pathlib import Path
import socket
import sys
import threading
import time


def serve(connection):
    connection.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, 6666)
    connection.settimeout(30)
    try:
        with connection:
            while True:
                request = b""
                while b"\r\n\r\n" not in request:
                    data = connection.recv(4096)
                    if not data:
                        return
                    request += data
                connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
    except (OSError, TimeoutError):
        return


def server():
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.setsockopt(socket.SOL_IP, socket.IP_TRANSPARENT, 1)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, 6666)
    listener.bind(("0.0.0.0", 7896))
    listener.listen(16)
    while True:
        connection, _ = listener.accept()
        threading.Thread(target=serve, args=(connection,), daemon=True).start()


def client():
    with socket.create_connection(("203.0.113.10", 8080), timeout=3) as connection:
        request = b"GET / HTTP/1.1\r\nHost: fixture\r\n\r\n"
        connection.sendall(request)
        response = b""
        while not response.endswith(b"OK"):
            response += connection.recv(4096)
        Path("/tmp/guest-ready").write_text("ready")
        until = time.monotonic() + 30
        while not Path("/tmp/guest-next").exists():
            if time.monotonic() > until:
                raise TimeoutError("VM driver did not request the second packet")
            time.sleep(0.05)
        connection.settimeout(1)
        connection.sendall(request)
        try:
            data = connection.recv(4096)
            Path("/tmp/guest-result").write_text("unexpected-data" if data else "closed")
        except TimeoutError:
            Path("/tmp/guest-result").write_text("blocked")


if __name__ == "__main__":
    server() if sys.argv[1] == "server" else client()
