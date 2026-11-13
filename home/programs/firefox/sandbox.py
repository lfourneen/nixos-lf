"""Outer confinement for the native package; no privileged service or fallback.

The small proxy has a separate mount namespace containing portal identity and
the host bus. Only its filtered socket enters the browser namespace. Portal
metadata follows xdg-desktop-portal's Flatpak identity protocol; the application
and runtime remain native Nix store packages.
"""
from contextlib import contextmanager
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import time


APP_ID = "org.nixos.ManagedFirefox"
CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"


def remote_name(settings):
    # Match nsDBusRemoteServer's encoding of the explicit full profile path.
    # Do not permit OpenURL into other, potentially unconfined Firefox profiles.
    encoded = base64.b64encode(os.fsencode(settings["profile"])).decode("ascii")
    return ("org.mozilla.firefox." + re.sub(r"[^A-Za-z0-9_]", "_", encoded))[:255]


def process_start(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None


def check_generation(settings, binary):
    active = False
    directory = Path(settings["runtime"]) / "firefox-managed"
    if directory.exists():
        for path in directory.glob("*/generation.json"):
            state = json.loads(path.read_text())
            if state.get("profile", settings["profile"]) != settings["profile"]:
                continue
            if process_start(state["pid"]) != state["start"]:
                continue
            if state["binary"] != str(binary):
                raise ValueError("close the existing Firefox process to load the current Nix package")
            active = True
    if not active:
        # A lock left after namespace teardown can contain a recycled host PID.
        # Ask the kernel whether the profile is actually owned. A live browser
        # without our host supervisor is never a valid remoting destination.
        try:
            with (Path(settings["profile"]) / ".parentlock").open("r+") as lock:
                fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except FileNotFoundError:
            pass
        except BlockingIOError:
            raise ValueError("close the existing Firefox process before entering the outer sandbox") from None
    return active


def prerequisites(settings):
    if settings.get("requireProxy"):
        path = Path(settings['proxySocket'])
        try:
            info = path.lstat()
        except FileNotFoundError:
            raise ValueError("strict proxy socket is unavailable; check clash-strict.socket and Mode B") from None
        parent = path.parent.lstat()
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or
                not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022):
            raise ValueError("strict proxy endpoint has invalid ownership or permissions")
    runtime = Path(settings["runtime"])
    info = runtime.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("the desktop runtime directory must be private and owned by this user")
    wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
    # Do not permit an environment variable to add arbitrary host mounts.
    if not wayland or "/" in wayland or wayland in {".", ".."}:
        raise ValueError("WAYLAND_DISPLAY must name a socket in the desktop runtime directory")
    for path in (runtime / "bus", runtime / wayland):
        if not stat.S_ISSOCK(path.stat().st_mode):
            raise ValueError("required desktop socket is unavailable: " + str(path))
    if not Path(CA_BUNDLE).is_file():
        raise ValueError("the system CA bundle is unavailable")
    subprocess.run([settings["scope"], "--check"], check=True, timeout=5)
    return wayland


def base(settings):
    return [settings["bwrap"], "--unshare-user", "--unshare-pid", "--unshare-ipc",
            "--unshare-uts", "--hostname", "firefox", "--cap-drop", "ALL",
            "--new-session", "--die-with-parent", "--clearenv",
            "--ro-bind", "/nix/store", "/nix/store", "--proc", "/proc",
            "--dev", "/dev", "--tmpfs", "/tmp",
            "--dir", settings["runtime"], "--setenv", "XDG_RUNTIME_DIR", settings["runtime"]]


def desktop_environment(settings, wayland):
    # No inherited loader, shell, debugging, agent, system-bus or font paths.
    env = {"HOME": settings["home"], "USER": settings["user"], "LOGNAME": settings["user"],
           "PATH": settings["path"], "LANG": "C.UTF-8", "TMPDIR": "/tmp",
           "XDG_CONFIG_HOME": settings["home"] + "/.config",
           "XDG_CACHE_HOME": settings["home"] + "/.cache",
           "XDG_DATA_HOME": settings["home"] + "/.local/share",
           "XDG_RUNTIME_DIR": settings["runtime"], "WAYLAND_DISPLAY": wayland,
           "MOZ_ENABLE_WAYLAND": "1", "MOZ_DBUS_REMOTE": "1", "GDK_BACKEND": "wayland",
           "GTK_USE_PORTAL": "1", "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + settings["runtime"] + "/bus",
           "FONTCONFIG_FILE": settings["fonts"], "FONTCONFIG_PATH": "/nonexistent",
           "PULSE_SERVER": "unix:" + settings["runtime"] + "/pulse/native"}
    for name in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "XDG_SESSION_TYPE",
                 "XDG_ACTIVATION_TOKEN", "DESKTOP_STARTUP_ID"):
        if name in os.environ:
            env[name] = os.environ[name]
    return env


def graphics_devices(nvidia, devices=Path("/dev"), sysfs=Path("/sys")):
    def character_device(path):
        try:
            return stat.S_ISCHR(path.stat().st_mode)
        except OSError:
            return False

    render = {p for p in (devices / "dri").glob("renderD*")
              if re.fullmatch(r"renderD[0-9]+", p.name) and character_device(p)}
    result = set(render)
    found_nvidia = False
    if nvidia:
        for path in render:
            number = path.stat().st_rdev
            device = sysfs / "dev/char" / f"{os.major(number)}:{os.minor(number)}" / "device"
            try:
                if (device / "vendor").read_text().strip().lower() != "0x10de":
                    continue
            except OSError:
                continue
            found_nvidia = True
            # NVIDIA's userspace requires its corresponding primary node even
            # for rendering. Do not grant unrelated GPUs' primary nodes.
            result.update(devices / "dri" / p.name for p in (device / "drm").glob("card*")
                          if re.fullmatch(r"card[0-9]+", p.name))
        if found_nvidia:
            result.update(p for p in devices.glob("nvidia*")
                          if re.fullmatch(r"nvidia(?:[0-9]+|ctl|-uvm)", p.name))
    return sorted(p for p in result if character_device(p))


@contextmanager
def session(settings):
    wayland = prerequisites(settings)
    runtime = Path(settings["runtime"])
    root = runtime / "firefox-managed"
    root.mkdir(mode=0o700, exist_ok=True)
    instance = Path(tempfile.mkdtemp(prefix="instance-", dir=root))
    identifier = instance.name
    portal = runtime / ".flatpak" / identifier
    portal.mkdir(mode=0o700, parents=True)
    metadata = instance / "info"
    metadata.write_text(f"[Application]\nname={settings.get('appId', APP_ID)}\n"
                        f"[Instance]\ninstance-id={identifier}\n"
                        "[Context]\nshared=" + ("" if settings.get("requireProxy") or settings.get("portalOnly") else "network;") + "\n")
    # Activate the real document portal before binding its per-application view.
    # Never mount the global doc tree, which includes grants for other apps.
    try:
        subprocess.run([settings["gdbus"], "call", "--session", "--dest",
                        "org.freedesktop.portal.Documents", "--object-path",
                        "/org/freedesktop/portal/documents", "--method",
                        "org.freedesktop.portal.Documents.GetMountPoint"],
                       env={**os.environ, "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(runtime / "bus")},
                       check=True, capture_output=True, timeout=20)
        documents = runtime / "doc/by-app" / settings.get("appId", APP_ID)
        if not documents.is_dir():
            raise ValueError("the per-application document portal is unavailable")
        for name in ("profile", "cache", "downloads"):
            path = Path(settings[name])
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            if path.is_symlink() or path.resolve() != path:
                raise ValueError("browser writable directories must be real paths: " + str(path))
        Path(settings["profile"]).chmod(0o700)
        proxy_dir = instance / "proxy"
        proxy_dir.mkdir(mode=0o700)
        control, child_control = socket.socketpair()
        proxy_command = base(settings) + [
            "--ro-bind", str(metadata), "/.flatpak-info",
            "--bind", str(proxy_dir), "/run/proxy",
            "--ro-bind", str(runtime / "bus"), "/run/host-bus",
            "--", settings["dbusProxy"], "--fd=" + str(child_control.fileno()),
            "unix:path=/run/host-bus", "/run/proxy/bus", "--filter",
            "--own=" + remote_name(settings),
            "--call=" + remote_name(settings) + "=org.mozilla.firefox.OpenURL",
            "--call=org.freedesktop.portal.Desktop=org.freedesktop.DBus.Properties.Get",
            "--call=org.freedesktop.portal.Desktop=org.freedesktop.DBus.Properties.GetAll",
            "--call=org.freedesktop.portal.Desktop=org.freedesktop.portal.FileChooser.*",
            "--call=org.freedesktop.portal.Desktop=org.freedesktop.portal.Settings.Read",
            "--call=org.freedesktop.portal.Desktop=org.freedesktop.portal.Settings.ReadAll",
            "--call=org.freedesktop.portal.Desktop=org.freedesktop.portal.OpenURI.SchemeSupported",
            "--call=org.freedesktop.portal.Desktop=org.freedesktop.portal.Request.Close",
            "--call=org.freedesktop.portal.Desktop=org.freedesktop.portal.Session.Close",
            "--broadcast=org.freedesktop.portal.Desktop=org.freedesktop.portal.Request.Response",
            "--broadcast=org.freedesktop.portal.Desktop=org.freedesktop.portal.Settings.SettingChanged"]
        proxy = subprocess.Popen(proxy_command, pass_fds=(child_control.fileno(),),
                                 stdin=subprocess.DEVNULL)
        child_control.close()
        portal_proxy = None
        try:
            if not select.select([control], [], [], 10)[0] or not control.recv(1):
                raise ValueError("filtered desktop bus failed to start")
            command = base(settings) + ["--ro-bind", str(metadata), "/.flatpak-info",
                "--dir", settings["home"], "--chdir", settings["home"],
                "--bind", settings["profile"], settings["profile"],
                "--bind", settings["cache"], settings["home"] + "/.cache",
                "--bind", settings["downloads"], settings["downloads"],
                "--bind", str(documents), str(runtime / "doc"),
                "--bind", str(proxy_dir / "bus"), str(runtime / "bus"),
                "--ro-bind", str(runtime / wayland), str(runtime / wayland),
                # NixOS certificate files link through /etc/static. Binding
                # the directory leaves those links dangling in this root.
                # Pin the actual file without exposing the rest of /etc/static.
                "--ro-bind", str(Path(CA_BUNDLE).resolve(strict=True)), CA_BUNDLE]
            for path in ("/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf",
                         "/etc/localtime", "/etc/machine-id",
                         "/run/opengl-driver"):
                command += ["--ro-bind-try", path, path]
            if settings.get('portalOnly'):
                path = instance / 'portal.sock'
                portal_proxy = subprocess.Popen([settings['python'], '-I', settings['networkModule'], 'portal',
                    '--socket', str(path), '--address', settings['portalAddress']],
                    env={'PATH': settings['path']}, stdin=subprocess.DEVNULL)
                deadline = time.monotonic() + 5
                while not path.exists():
                    if portal_proxy.poll() is not None or time.monotonic() > deadline:
                        raise ValueError('portal proxy failed to start')
                    time.sleep(0.02)
                command += ['--unshare-net', '--ro-bind', str(path), '/run/strict-proxy.sock']
            elif settings.get('requireProxy'):
                command += ['--unshare-net', '--ro-bind', settings['proxySocket'], '/run/strict-proxy.sock']
            # Retain Gecko's own sandbox. NVIDIA access is separately selected
            # from the NixOS driver configuration; other devices stay hidden.
            for path in graphics_devices(settings.get("nvidiaAcceleration", False)):
                command += ["--dev-bind", str(path), str(path)]
            egl = Path("/etc/egl/egl_external_platform.d")
            if egl.is_dir():
                command += ["--ro-bind", str(egl.resolve(strict=True)), str(egl)]
            pulse = runtime / "pulse/native"
            if pulse.exists():
                command += ["--ro-bind", str(pulse), str(pulse)]
            for name, value in desktop_environment(settings, wayland).items():
                command += ["--setenv", name, value]
            yield command, instance, portal, proxy
        finally:
            control.close()
            if portal_proxy is not None:
                portal_proxy.terminate()
                portal_proxy.wait(timeout=5)
            proxy.terminate()
            try:
                proxy.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait()
    finally:
        shutil.rmtree(portal)
        shutil.rmtree(instance)


def run(settings, binary, command):
    root = Path(settings["runtime"]) / "firefox-managed"
    root.mkdir(mode=0o700, exist_ok=True)

    def terminate(_signum, _frame):
        raise SystemExit(128 + signal.SIGTERM)

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        with (root / "launch.lock").open("a") as launch_lock:
            fcntl.flock(launch_lock, fcntl.LOCK_EX)
            check_generation(settings, binary)
            return run_locked(settings, binary, command, launch_lock)
    finally:
        signal.signal(signal.SIGTERM, previous)


def run_locked(settings, binary, command, launch_lock):
    with session(settings) as (prefix, instance, portal, proxy):
        # The metadata names a host PID in the browser's namespaces. Publish it
        # before the browser can ask the portal to translate any selected file.
        read_info, write_info = os.pipe()
        read_gate, write_gate = os.pipe()
        browser = None
        try:
            if settings.get('requireProxy') or settings.get('portalOnly'):
                command = [settings['python'], '-I', settings['networkModule'], 'browser',
                           '--socket', '/run/strict-proxy.sock', '--port', str(settings['proxyPort']), '--', *command]
            started = time.monotonic()
            browser = subprocess.Popen(prefix + ["--info-fd", str(write_info),
                "--block-fd", str(read_gate), "--", settings["scope"], *command],
                pass_fds=(write_info, read_gate), stdin=subprocess.DEVNULL)
            os.close(write_info)
            write_info = None
            os.close(read_gate)
            read_gate = None
            if not select.select([read_info], [], [], 15)[0]:
                raise ValueError("browser mount namespace setup timed out")
            with os.fdopen(read_info) as stream:
                read_info = None
                info = json.load(stream)
            (portal / "bwrapinfo.json").write_text(json.dumps(info))
            state = {"pid": os.getpid(), "start": process_start(os.getpid()), "binary": str(binary), "profile":settings["profile"]}
            pending = instance / "generation.tmp"
            pending.write_text(json.dumps(state))
            pending.replace(instance / "generation.json")
            fcntl.flock(launch_lock, fcntl.LOCK_UN)
            os.write(write_gate, b"1")
            os.close(write_gate)
            write_gate = None
            while True:
                try:
                    return browser.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    if settings.get('lifetime') and time.monotonic() - started >= settings['lifetime']:
                        raise ValueError('temporary browser session expired')
                    if proxy.poll() is not None:
                        raise ValueError("filtered desktop bus exited")
        finally:
            for fd in (read_info, write_info, read_gate, write_gate):
                if fd is not None:
                    os.close(fd)
            if browser is not None and browser.poll() is None:
                browser.terminate()
                try:
                    browser.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    browser.kill()
                    browser.wait()
