"""Exercise the production namespace/portal policy with a real Wayland Firefox."""
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


manifest = json.loads(Path("/etc/firefox-sandbox-test.json").read_text())
settings = json.loads(Path(manifest["settings"]).read_text())
sandbox = module("sandbox", settings["sandboxModule"])
guard = module("guard", manifest["guard"])
binary = manifest["package"] + "/bin/.firefox-unchecked"
profile = Path(settings["profile"])
profile.mkdir(parents=True, exist_ok=True)

if sys.argv[1:] and sys.argv[1] == "run":
    raise SystemExit(sandbox.run(settings, binary, sys.argv[2:]))

driver = module("driver", manifest["driver"])
tls = module("tls", manifest["tlsModule"]).start(manifest["tlsDirectory"])
private = Path("/home/lfour/private")
private.mkdir(exist_ok=True)
(private / "selected.txt").write_text("chosen through the portal")
(private / "secret.txt").write_text("must remain private")
host_socket = socket.socket(socket.AF_UNIX)
host_socket.bind("\0firefox-host-secret")
host_socket.listen()
with socket.socket(socket.AF_UNIX) as unconfined:
    unconfined.connect("\0firefox-host-secret")
os.environ.update(MOZ_DISABLE_CONTENT_SANDBOX="1", MOZ_REMOTE_ALLOW_SYSTEM_ACCESS="1",
                  SSH_AUTH_SOCK="/run/user/1000/agent.sock", FONTCONFIG_FILE="/personal/fonts.conf")

# Run a parent-process payload through the actual sandbox, not a Gecko content
# sandbox. It must have no host files, raw desktop bus or abstract sockets.
probe = r'''
import ctypes, errno, json, os, pathlib, socket, subprocess
m = MANIFEST
home = pathlib.Path('/home/lfour')
assert not (home / 'private/secret.txt').exists()
assert not (home / '.ssh').exists()
assert not pathlib.Path('/run/dbus/system_bus_socket').exists()
assert not pathlib.Path('/dev/input').exists()
assert not pathlib.Path('/dev/video0').exists()
assert not pathlib.Path('/dev/nvidia-modeset').exists()
assert not pathlib.Path('/sys/bus/pci').exists()
assert not pathlib.Path('/run/user/1000/firefox-managed').exists()
assert not pathlib.Path('/run/user/1000/doc/by-app').exists()
bundle = pathlib.Path('/etc/ssl/certs/ca-certificates.crt')
assert bundle.is_file() and not bundle.is_symlink()
assert 'BEGIN CERTIFICATE' in bundle.read_text()
assert not pathlib.Path('/etc/static').exists()
assert 'LD_PRELOAD' not in os.environ and 'SSH_AUTH_SOCK' not in os.environ
assert 'MOZ_DISABLE_CONTENT_SANDBOX' not in os.environ
assert 'MOZ_REMOTE_ALLOW_SYSTEM_ACCESS' not in os.environ
assert len(list(pathlib.Path('/proc').glob('[0-9]*'))) < 10
# This x86_64 test attempts KEYCTL_GET_KEYRING_ID on the inherited session
# keyring. A same-UID parent can normally obtain/create it; the sandbox cannot.
libc = ctypes.CDLL(None, use_errno=True)
assert libc.syscall(250, 0, -3, 1) == -1 and ctypes.get_errno() == errno.EPERM
s = socket.socket(socket.AF_UNIX)
try:
    s.connect('\0firefox-host-secret')
except PermissionError:
    pass
else:
    raise AssertionError('host abstract Unix socket was reachable')
result = subprocess.run([m['gdbus'], 'call', '--session', '--dest', 'org.freedesktop.systemd1',
    '--object-path', '/org/freedesktop/systemd1', '--method', 'org.freedesktop.systemd1.Manager.ListUnits'],
    capture_output=True, text=True)
assert result.returncode, result.stdout
assert 'AccessDenied' in result.stderr or 'ServiceUnknown' in result.stderr, result.stderr
assert subprocess.run([m['pactl'], 'info'], capture_output=True).returncode == 0
(home / 'Downloads/allowed.txt').write_text('download')
(home / 'Downloads/secret-link').symlink_to(home / 'private/secret.txt')
assert not (home / 'Downloads/secret-link').exists()
(home / '.cache/allowed.txt').write_text('cache')
print('Parent filesystem, devices, bus, abstract sockets and audio verified', flush=True)
'''.replace("MANIFEST", repr(manifest))
(profile / "probe.py").write_text(probe)
subprocess.run([manifest["gdbus"], "call", "--session", "--dest", "org.freedesktop.systemd1",
    "--object-path", "/org/freedesktop/systemd1", "--method", "org.freedesktop.systemd1.Manager.ListUnits"],
    check=True, capture_output=True)
assert sandbox.run(settings, binary, [manifest["python"], "-I", str(profile / "probe.py")]) == 0
assert Path(settings["downloads"], "allowed.txt").read_text() == "download"
assert Path(settings["cache"], "allowed.txt").read_text() == "cache"

with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
(profile / "user.js").write_text('\n'.join(
    "user_pref(" + json.dumps(k) + ", " + json.dumps(v) + ");" for k, v in {
        "marionette.port": port, "marionette.prefs.recommended": False,
        "remote.prefs.recommended": False, "browser.startup.homepage": "about:blank",
    }.items()))
command = [manifest["python"], __file__, "run", manifest["env"],
           "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1", binary, "--profile", str(profile),
           "--marionette", "--remote-allow-system-access", "about:blank"]
process = subprocess.Popen(command)
client = None
try:
    client = driver.Marionette(process, port)
    client.call("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {"acceptInsecureCerts": False}}})
    client.call("WebDriver:SetTimeouts", {"pageLoad": 15000})
    client.call("WebDriver:Navigate", {"url": "https://trusted.sandbox.fixture.test/"})
    assert client.execute("return document.body.innerText;") == "Firefox system trust fixture"
    for host in ("untrusted.sandbox.fixture.test", "wrong.sandbox.fixture.test"):
        try:
            client.call("WebDriver:Navigate", {"url": "https://" + host + "/"})
        except AssertionError as error:
            assert error.args[0]["error"] == "insecure certificate", error
        else:
            raise AssertionError("invalid certificate was accepted: " + host)
    client.call("WebDriver:Navigate", {"url": "about:blank"})
    client.call("Marionette:SetContext", {"value": "chrome"})
    actual = client.call("WebDriver:ExecuteAsyncScript", {"script": """
const done = arguments[arguments.length - 1];
IOUtils.exists('/home/lfour/private/secret.txt').then(privateFile => done({private: privateFile,
        picker: Services.prefs.getIntPref('widget.use-xdg-desktop-portal.file-picker'),
        sandbox: Services.prefs.getIntPref('security.sandbox.content.level')}));
""", "args": [], "newSandbox": True})["value"]
    assert actual["private"] is False and actual["picker"] == 1 and actual["sandbox"] > 0, actual
    result = client.call("WebDriver:ExecuteAsyncScript", {"script": """
const done = arguments[arguments.length - 1];
const picker = Cc['@mozilla.org/filepicker;1'].createInstance(Ci.nsIFilePicker);
picker.init(Services.wm.getMostRecentWindow('navigator:browser').browsingContext,
            'Choose a file', Ci.nsIFilePicker.modeOpen);
picker.open(result => done({result, path: picker.file && picker.file.path}));
""", "args": [], "newSandbox": True})["value"]
    assert result["result"] == 0 and result["path"].startswith("/run/user/1000/doc/"), result
    request = json.loads(Path("/home/lfour/portal-request.json").read_text())
    assert request[1] == sandbox.APP_ID, request
    contents = client.call("WebDriver:ExecuteAsyncScript", {"script":
        "const done = arguments[arguments.length - 1]; IOUtils.readUTF8(" +
        json.dumps(result["path"]) + ").then(done);", "args": [], "newSandbox": True})["value"]
    assert contents == "chosen through the portal"
    assert sandbox.check_generation(settings, binary)
    try:
        sandbox.check_generation(settings, "/other-generation/bin/.firefox-unchecked")
    except ValueError as error:
        assert "close the existing Firefox" in str(error)
    else:
        raise AssertionError("old package reuse was accepted")
    # A second confined process must reach the existing window through the
    # filtered bus despite distinct PID/mount/Landlock namespaces.
    opened = subprocess.run([manifest["python"], __file__, "run", binary, "--profile", str(profile),
                             "--new-tab", "about:robots"], timeout=25)
    assert opened.returncode == 0
    deadline = time.monotonic() + 10
    while not client.execute("return [...Services.wm.getMostRecentWindow('navigator:browser').gBrowser.browsers].some(b => b.currentURI.spec == 'about:robots');"):
        assert time.monotonic() < deadline, "URL did not reach the existing browser"
        time.sleep(.2)
    client.call("Marionette:Quit", {"flags": ["eForceQuit"]})
    assert process.wait(timeout=15) == 0
finally:
    if client:
        client.socket.close()
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=10)
assert not sandbox.check_generation(settings, binary)
# The namespace PID in a stale Firefox lock must not prevent a clean restart.
restarted = subprocess.Popen(command)
try:
    client = driver.Marionette(restarted, port)
    client.call("WebDriver:NewSession")
    client.call("Marionette:Quit", {"flags": ["eForceQuit"]})
    assert restarted.wait(timeout=15) == 0
finally:
    if restarted.poll() is None:
        restarted.terminate()
        restarted.wait(timeout=10)

original_session = sandbox.session


@contextmanager
def broken_proxy(settings):
    with original_session(settings) as value:
        value[3].terminate()
        value[3].wait(timeout=5)
        yield value


sandbox.session = broken_proxy
try:
    sandbox.run(settings, binary, [manifest["python"], "-c", "import time; time.sleep(30)"])
except ValueError as error:
    assert "filtered desktop bus exited" in str(error), error
else:
    raise AssertionError("the browser survived a failed bus proxy")
assert not list(Path(settings["runtime"], "firefox-managed").glob("*/generation.json"))
print("Real Wayland Firefox: portal grant, URL reuse, generation rejection and restart passed", flush=True)
