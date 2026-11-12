"""Fresh-profile Firefox automation; never connect to a user's browser."""
import json
import os
from pathlib import Path
import shlex
import socket
import sys
import time


def isolated_launcher(package, settings, directory):
    directory = Path(directory)
    app_data = directory / "app-data"
    app_data.mkdir()
    wrapper = (Path(package) / "bin/.firefox-unchecked").read_text()
    original = "export MOZ_APP_DATA='" + str(Path(settings["profile"]).parent) + "'"
    assert original in wrapper
    launcher = directory / "firefox"
    launcher.write_text(wrapper.replace(original, "export MOZ_APP_DATA=" + shlex.quote(str(app_data))))
    launcher.chmod(0o700)
    return launcher


def confined_launcher(package, settings, directory):
    """VM-only automation through the production outer policy, with fresh data.

    The public entry point rejects automation. This private fixture opts into
    Marionette *inside* confinement, without adding environment or CLI escapes
    to the deployed launcher.
    """
    directory = Path(directory)
    settings = {**settings, "profile": str(directory / "profile"), "cache": str(directory / "cache")}
    binary = str(Path(package) / "bin/.firefox-unchecked")
    env = str(Path(settings["path"].split(os.pathsep)[0]) / "env")
    launcher = directory / "confined-firefox"
    launcher.write_text(f"#!{sys.executable}\n" + """
import importlib.util, sys
settings = SETTINGS
spec = importlib.util.spec_from_file_location('sandbox', settings['sandboxModule'])
sandbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sandbox)
raise SystemExit(sandbox.run(settings, BINARY,
    [ENV, 'MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1', BINARY, *sys.argv[1:]]))
""".replace("SETTINGS", repr(settings)).replace("BINARY", repr(binary)).replace("ENV", repr(env)))
    launcher.chmod(0o700)
    return launcher


class Marionette:
    def __init__(self, browser, port):
        deadline = time.monotonic() + 40
        while True:
            try:
                self.socket = socket.create_connection(("127.0.0.1", port), timeout=2)
                break
            except OSError:
                assert browser.poll() is None, "Firefox exited before automation startup"
                if time.monotonic() > deadline:
                    raise TimeoutError("Marionette startup timed out")
                time.sleep(.2)
        self.socket.settimeout(30)
        self.identifier = 0
        self.read()

    def read(self):
        size = b""
        while True:
            char = self.socket.recv(1)
            if not char:
                raise EOFError("automation socket closed")
            if char == b":":
                break
            size += char
        data = b""
        while len(data) < int(size):
            part = self.socket.recv(int(size) - len(data))
            if not part:
                raise EOFError("incomplete automation response")
            data += part
        return json.loads(data)

    def call(self, name, parameters=None):
        self.identifier += 1
        payload = json.dumps([0, self.identifier, name, parameters or {}]).encode()
        self.socket.sendall(str(len(payload)).encode() + b":" + payload)
        response = self.read()
        assert response[:2] == [1, self.identifier], response
        assert not response[2], response[2]
        return response[3]

    def execute(self, source):
        return self.call("WebDriver:ExecuteScript", {"script": source, "args": [], "newSandbox": True})["value"]
