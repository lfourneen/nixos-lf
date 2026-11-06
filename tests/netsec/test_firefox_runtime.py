"""Read back the actual Nix Firefox package in a fresh, offline test profile.

Automation uses the package-private binary solely to avoid requiring a host
proxy-mode supervisor inside the build sandbox. Production policies, AutoConfig
and font configuration are unchanged. Never attach to a personal browser.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

manifest = json.loads(Path(sys.argv[1]).read_text())
output = Path(sys.argv[2])
package = Path(manifest["package"])
settings = json.loads(Path(manifest["settings"]).read_text())
spec = importlib.util.spec_from_file_location("guard", os.environ["FIREFOX_GUARD"])
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

# Check the built policies and desktop entries rather than only Nix input values.
policies = json.loads((package / "lib/firefox/distribution/policies.json").read_text())["policies"]
assert policies == manifest["policies"]
assert policies["Proxy"] == dict(Mode="manual", Locked=True,
    HTTPProxy="127.0.0.1:" + str(manifest["relayPort"]),
    SSLProxy="127.0.0.1:" + str(manifest["relayPort"]),
    UseHTTPProxyForAllProtocols=True, Passthrough="")
assert policies["DNSOverHTTPS"] == {"Enabled": False, "Locked": True}
assert not any((p if isinstance(p, str) else p.get("appId")) in
               {"app.zen_browser.zen", "com.google.Chrome"} for p in manifest["flatpaks"])
for desktop in [manifest["desktop"], *manifest["aliases"]]:
    content = Path(desktop).read_text()
    commands = [line for line in content.splitlines() if line.startswith("Exec=")]
    assert commands and all(line.startswith("Exec=" + str(package) + "/bin/firefox ") for line in commands)
    assert "flatpak run" not in content and "--ProfileManager" not in content
for name in ("text/html", "x-scheme-handler/http", "x-scheme-handler/https"):
    assert manifest["mime"][name] == ["firefox.desktop"]
archive_bytes = (package / "lib/firefox/browser/omni.ja").read_bytes()
# Firefox's optimized ZIP places the central directory first. Append a standard
# directory/end record for Python's ZIP reader; local-file offsets stay intact.
end = bytearray(archive_bytes[archive_bytes.rfind(b"PK\x05\x06"):])
size, offset = struct.unpack_from("<II", end, 12)
struct.pack_into("<I", end, 16, len(archive_bytes))
with zipfile.ZipFile(io.BytesIO(archive_bytes + archive_bytes[offset:offset + size] + end)) as archive:
    schema = json.loads(archive.read("modules/policies/policies-schema.json"))
    # Updating Firefox must not silently drop a top-level policy.
    assert policies.keys() <= schema["properties"].keys(), policies.keys() - schema["properties"].keys()
guard.verify_fonts(settings)

PAGE = r"""<!doctype html><meta charset=utf-8><title>Offline privacy fixture</title>
<script>
const c=document.createElement('canvas');c.width=64;c.height=64;
const ctx=c.getContext('2d');ctx.fillStyle='rgb(17,83,149)';ctx.fillRect(0,0,64,64);
const data=ctx.getImageData(0,0,64,64).data, expected=[17,83,149,255];
window.result=JSON.stringify({changed:data.reduce((n,b,i)=>n+(b!==expected[i%4]),0),
 timezone:Intl.DateTimeFormat().resolvedOptions().timeZone,
 webRTC:typeof RTCPeerConnection,
 webgl:!!document.createElement('canvas').getContext('webgl'),
 webgpu:!!navigator.gpu, language:navigator.language, cpu:navigator.hardwareConcurrency});
</script>"""
requests = []


class Proxy(BaseHTTPRequestHandler):
    def do_GET(self):
        requests.append(self.path)
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_CONNECT(self):
        # No forwarding: background extension/update traffic cannot leave the fixture.
        self.send_error(502)

    def log_message(self, *args):
        pass


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


server = ThreadingHTTPServer(("127.0.0.1", manifest["relayPort"]), Proxy)
threading.Thread(target=server.serve_forever, daemon=True).start()
record = dict(package=str(package), headless=True, echHandshakeTested=False)
with tempfile.TemporaryDirectory() as work:
    profile = Path(work) / "profile"
    profile.mkdir(mode=0o700)
    # Home Manager pins MOZ_APP_DATA to the real home. Relocate only that
    # directory in a temporary copy of the library wrapper; the executed Nix
    # runtime and all policies/AutoConfig remain the production artifacts.
    app_data = Path(work) / "app-data"
    app_data.mkdir()
    launcher = Path(work) / "firefox"
    wrapper = (package / "bin/.firefox-unchecked").read_text()
    # makeWrapper always quotes its --set value, even without shell metacharacters.
    original = "export MOZ_APP_DATA='" + str(Path(settings["profile"]).parent) + "'"
    assert original in wrapper
    launcher.write_text(wrapper.replace(original, "export MOZ_APP_DATA=" + shlex.quote(str(app_data))))
    launcher.chmod(0o700)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # Hostile user.js values must not defeat package locks, even in a new profile.
    user = {
        "marionette.port": port, "marionette.prefs.recommended": False,
        "remote.prefs.recommended": False, "browser.startup.homepage": "about:blank",
        "privacy.resistFingerprinting": False, "network.trr.mode": 3,
        "network.proxy.type": 0, "network.proxy.http_port": 9,
        "media.peerconnection.enabled": True, "webgl.disabled": False,
    }
    (profile / "user.js").write_text("\n".join(
        "user_pref(" + json.dumps(k) + ", " + json.dumps(v) + ");" for k, v in user.items()))
    client = None
    with (output / "firefox.log").open("w") as log:
        browser = subprocess.Popen([
            str(launcher), "--headless", "--no-remote", "--profile", str(profile),
            "--marionette", "--remote-allow-system-access", "about:blank"],
            env={**guard.environment(settings), "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1"},
            stdout=log, stderr=log)
        try:
            client = Marionette(browser, port)
            client.call("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {"acceptInsecureCerts": False}}})
            client.call("Marionette:SetContext", {"value": "chrome"})
            expected = dict(manifest["prefs"], **{
                "network.proxy.type": 1, "network.proxy.http": "127.0.0.1",
                "network.proxy.http_port": manifest["relayPort"],
                "network.proxy.ssl": "127.0.0.1", "network.proxy.ssl_port": manifest["relayPort"],
            })
            record["preferences"] = client.execute("""
const prefs={};
for(const name of """ + json.dumps(list(expected)) + """) {
 const t=Services.prefs.getPrefType(name);
 const value=t===128?Services.prefs.getBoolPref(name):t===64?Services.prefs.getIntPref(name):
             t===32?Services.prefs.getStringPref(name):null;
 prefs[name]={value,locked:Services.prefs.prefIsLocked(name)};
}
return prefs;
""")
            for name, value in expected.items():
                assert record["preferences"][name] == {"value": value, "locked": True}, name
            guard.verify_running_generation(profile, package / "bin/.firefox-unchecked")
            record["activePolicies"] = client.execute("return Object.keys(Services.policies.getActivePolicies());")
            assert set(policies) <= set(record["activePolicies"]), set(policies) - set(record["activePolicies"])
            client.call("Marionette:SetContext", {"value": "content"})
            client.call("WebDriver:Navigate", {"url": "http://privacy-fixture.invalid/"})
            record["web"] = client.execute("return JSON.parse(window.result);")
            assert record["web"]["changed"] > 0, record["web"]
            assert record["web"]["timezone"] in {"UTC", "Atlantic/Reykjavik"}
            assert record["web"]["webRTC"] == "undefined"
            assert not record["web"]["webgl"] and not record["web"]["webgpu"]
            assert "http://privacy-fixture.invalid/" in requests, requests
            # A literal HTTP portal and loopback address must also use the relay.
            for url in ("http://192.168.1.1/portal", "http://127.0.0.1/relay-check"):
                client.call("WebDriver:Navigate", {"url": url})
                assert url in requests, requests
            # Closing the relay must cause an error, never a direct connection
            # even to a live loopback HTTP destination.
            server.shutdown()
            server.server_close()
            direct_hits = []
            class Direct(Proxy):
                def do_GET(self):
                    direct_hits.append(self.path)
                    super().do_GET()
            direct = ThreadingHTTPServer(("127.0.0.1", 0), Direct)
            threading.Thread(target=direct.serve_forever, daemon=True).start()
            try:
                try:
                    client.call("WebDriver:Navigate", {"url": "http://127.0.0.1:"
                                + str(direct.server_port) + "/must-use-relay"})
                    raise RuntimeError("navigation succeeded without a relay")
                except AssertionError as error:
                    assert "proxyConnectFailure" in str(error), str(error)
                assert not direct_hits, direct_hits
                record["failClosed"] = True
            finally:
                direct.shutdown()
                direct.server_close()
            record["ok"] = True
        finally:
            if client:
                client.socket.close()
            if browser.poll() is None:
                browser.terminate()
                try:
                    browser.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    browser.kill()
                    browser.wait(timeout=3)
            server.server_close()
            (output / "runtime.json").write_text(json.dumps(record, indent=2) + "\n")
            if not record.get("ok"):
                log.flush()
                print((output / "firefox.log").read_text()[-6000:], flush=True)
print("Native Firefox policy locks, privacy APIs, fonts and relay path passed")
