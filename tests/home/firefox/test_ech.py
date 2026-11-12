"""Real NSS handshakes against the VM's local ECH origin and cover-name trap."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


driver = module("driver", "/etc/netsec-firefox-driver.py")
manifest = json.loads(Path("/etc/netsec-ech.json").read_text())
events = Path("/run/netsec-ech/events.jsonl")


def records():
    return [json.loads(line) for line in events.read_text().splitlines()]


@contextmanager
def browser(kind):
    item = manifest[kind]
    settings = json.loads(Path(item["settings"]).read_text())
    guard = module("guard", item["guard"])
    with tempfile.TemporaryDirectory(prefix="firefox-ech-") as work:
        profile = Path(work) / "profile"
        profile.mkdir()
        subprocess.run(["certutil", "-N", "--empty-password", "-d", "sql:" + str(profile)], check=True)
        subprocess.run(["certutil", "-A", "-n", "VM ECH origin", "-t", "C,,", "-d", "sql:" + str(profile),
                        "-i", "/run/netsec-ech/ca.pem"], check=True)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        prefs = {"marionette.port": port, "marionette.prefs.recommended": False,
                 "remote.prefs.recommended": False, "browser.startup.homepage": "about:blank",
                 # Gecko's automation-only gate would otherwise skip libc DNS.
                 "network.dns.native_https_query_in_automation": True}
        (profile / "user.js").write_text("\n".join(
            "user_pref(" + json.dumps(k) + ", " + json.dumps(v) + ");" for k, v in prefs.items()))
        launcher = driver.confined_launcher(item["package"], settings, work)
        env = guard.environment(settings)
        env["MOZ_REMOTE_ALLOW_SYSTEM_ACCESS"] = "1"
        log_path = Path("/tmp/ech-firefox-" + kind + ".log")
        with log_path.open("w") as log:
            process = subprocess.Popen([str(launcher), "--headless", "--no-remote", "--profile", str(profile),
                                        "--marionette", "--remote-allow-system-access", "about:blank"],
                                       env=env, stdout=log, stderr=log)
            client = None
            try:
                client = driver.Marionette(process, port)
                client.call("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {"acceptInsecureCerts": False}}})
                client.call("WebDriver:SetTimeouts", {"pageLoad": 15000})
                client.call("Marionette:SetContext", {"value": "chrome"})
                actual = client.execute("""
return {proxy: Services.prefs.getIntPref('network.proxy.type'),
        dns: Services.prefs.getIntPref('network.trr.mode'),
        native: Services.prefs.getBoolPref('network.dns.native_https_query'),
        fallback: Services.prefs.getBoolPref('network.dns.echconfig.fallback_to_origin_when_all_failed'),
        locked: Services.prefs.prefIsLocked('network.dns.echconfig.enabled')};
""")
                assert actual == {"proxy": 0 if kind == "transparent" else 1, "dns": 5, "native": True,
                                  "fallback": False, "locked": True}, actual
                client.call("Marionette:SetContext", {"value": "content"})
                yield client
            except Exception:
                log.flush()
                print(log_path.read_text()[-5000:], flush=True)
                raise
            finally:
                if client:
                    client.socket.close()
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


results = {}

if sys.argv[1:] == ["portal"]:
    with browser("transparent") as client:
        client.call("WebDriver:Navigate", {"url": "http://100.67.0.1:8080/"})
        assert client.execute("return document.body.innerText.length;") > 0
    print("Transparent Firefox reached the literal HTTP portal in A")
    raise SystemExit(0)


def navigate(client, label, accepted):
    host = label + ".ech.fixture.example"
    print("Navigating", label, "expect ECH", accepted, flush=True)
    client.call("WebDriver:Navigate", {"url": "https://" + host + "/"})
    body = client.execute("return document.body.innerText;")
    assert body.startswith("{"), (host, body)
    value = json.loads(body)
    assert value["accepted"] is accepted and value["server_name"] == host and not value["trap"], value
    if accepted:
        public = "retry-public" if label == "retry" else "public"
        assert value["outer"] == public + ".ech.fixture.example", value
    else:
        assert value["outer"] == host, ("expected GREASE, not accepted ECH", value)
    results[label] = value


with browser("http-proxy") as client:
    navigate(client, "connect", False)
with browser("transparent") as client:
    for label, accepted in (("accepted", True), ("plain", False), ("retry", True), ("reject", False)):
        navigate(client, label, accepted)
    # NSS allows an authenticated ECH rejection with no retry keys to retry
    # ordinary TLS. This differs from an untrusted rejection or bad origin cert.
    for label in ("untrusted", "badcert"):
        host = label + ".ech.fixture.example"
        try:
            client.call("WebDriver:Navigate", {"url": "https://" + host + "/"})
        except AssertionError as error:
            assert isinstance(error.args[0], dict) and error.args[0].get("error") == "insecure certificate", error
            results[label] = {"error": str(error)}
        else:
            uri = client.execute("return document.documentURI;")
            assert uri.startswith(("about:certerror", "about:neterror")), (host, uri)
            results[label] = {"error_page": uri}
        assert not any(event["kind"] == "http" and event["host"].split(":")[0] == host for event in records())
assert not any(event.get("trap") for event in records()), "cover SNI changed the dial target"
assert any(event["kind"] == "tls" and event.get("outer") == "retry-public.ech.fixture.example"
           and not event["accepted"] for event in records()), "no authenticated retry was exercised"
assert any(event["kind"] == "tls" and event.get("outer") == "reject-public.ech.fixture.example"
           and not event["accepted"] for event in records()), "no authenticated rejection was exercised"
dns = [json.loads(line) for line in Path("/run/netsec-ech/dns.log").read_text().splitlines()]
assert not any(query == {"name": "connect.ech.fixture.example", "type": 65} for query in dns), "CONNECT unexpectedly discovered HTTPS RR"
Path("/tmp/ech-browser-results.json").write_text(json.dumps(results, indent=2) + "\n")
print(json.dumps(results, indent=2), flush=True)
