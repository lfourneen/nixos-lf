"""Finish an old packaged GUI before launching the current one, as its own user.

The GUI's system-proxy switch is a local preference. Its OS writes run through
desktop helpers that never touch the managed core, so the launch boundary
installs command shims and, when the kernel allows it, a private mount namespace
that hides the absolute helper paths baked into the Nix package.
"""
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

GUI_NAMES = {"clash-verge", ".clash-verge-launcher", ".clash-verge-wrapped"}
REAL_VARIABLES = (
    "LF_GUI_REAL_GSETTINGS",
    "LF_GUI_REAL_DCONF",
    "LF_GUI_REAL_KREADCONFIG",
    "LF_GUI_REAL_KWRITECONFIG",
)


def process(pid):
    path = Path("/proc") / str(pid)
    try:
        if path.stat().st_uid != os.getuid():
            return None
        argv0 = os.fsdecode((path / "cmdline").read_bytes().split(b"\0", 1)[0])
        if Path(argv0).name not in GUI_NAMES:
            return None
        # Identify the incarnation as well as the PID before sending a signal.
        start = (path / "stat").read_text().rpartition(")")[2].split()[19]
        return {"pid": int(pid), "argv0": argv0, "start": start}
    except (OSError, IndexError, ValueError):
        return None


def processes():
    return [value for path in Path("/proc").iterdir()
            if path.name.isdigit() and (value := process(path.name)) is not None]


def active(records):
    return [record for record in records if process(record["pid"]) == record]


def stop(records, grace=10):
    def send(records, sig):
        for record in active(records):
            try:
                os.kill(record["pid"], sig)
            except ProcessLookupError:
                pass
    send(records, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while active(records) and time.monotonic() < deadline:
        time.sleep(0.05)
    remaining = active(records)
    if remaining:
        print("Clash Verge: old GUI did not exit; terminating it before launch", file=sys.stderr)
        send(remaining, signal.SIGKILL)
        deadline = time.monotonic() + 2
        while active(remaining) and time.monotonic() < deadline:
            time.sleep(0.05)
    return not active(records)


def prepare(expected, observe=processes, grace=10):
    stale = [record for record in observe() if record["argv0"] != expected]
    if stale and not stop(stale, grace):
        raise RuntimeError("old Clash GUI is still running")
    if any(record["argv0"] != expected for record in observe()):
        raise RuntimeError("another old Clash GUI appeared during launch")


def control_environment(env=None):
    """Install the write-discarding shims without changing the desktop type."""
    env = dict(os.environ if env is None else env)
    shim = env.get("LF_GUI_CONTROL_SHIM")
    path = env.get("LF_GUI_CONTROL_PATH")
    if not shim or not path:
        return env
    env["PATH"] = path + os.pathsep + env.get("PATH", "")
    return env


def control_directory(env):
    runtime = env.get("XDG_RUNTIME_DIR")
    base = runtime if runtime and os.path.isdir(runtime) else None
    try:
        path = tempfile.mkdtemp(prefix="clash-verge-control-", dir=base)
    except OSError:
        # A restricted test or service environment may not expose a writable
        # runtime directory. The masks still work from a private temporary dir.
        path = tempfile.mkdtemp(prefix="clash-verge-control-")
    if os.stat(path).st_uid != os.getuid():
        raise RuntimeError("unexpected control directory owner")
    return path


def control_argv(command, env):
    """Return (argv, private directory) with absolute helper paths masked."""
    shim = env.get("LF_GUI_CONTROL_SHIM")
    if not shim:
        return [command], None

    def intercept_only():
        # No private mount namespace: make the absolute gsettings path a
        # memory-only no-op, and ask the sysproxy crate for the version-5 KDE
        # helpers so they resolve through the PATH shim.
        env["GSETTINGS_BACKEND"] = "memory"
        env["KDE_SESSION_VERSION"] = "5"
        return [command], None

    bwrap = env.get("LF_GUI_BWRAP") or shutil.which("bwrap")
    if not bwrap:
        return intercept_only()
    try:
        masks = json.loads(env.get("LF_GUI_CONTROL_MASKS", "[]"))
    except ValueError:
        return intercept_only()
    if not isinstance(masks, list):
        return intercept_only()
    masks = [mask for mask in masks if isinstance(mask, dict)
             and isinstance(mask.get("target"), str)
             and isinstance(mask.get("real"), str)
             and isinstance(mask.get("variable"), str)]
    if not masks:
        return intercept_only()

    directory = control_directory(env)
    argv = [bwrap, "--dev-bind", "/", "/", "--die-with-parent"]
    originals = {name: env.get(name) for name in REAL_VARIABLES}

    def fallback():
        for name, value in originals.items():
            if value is None:
                env.pop(name, None)
            else:
                env[name] = value
        shutil.rmtree(directory, ignore_errors=True)
        return intercept_only()

    try:
        for index, mask in enumerate(masks):
            target, real, variable = mask["target"], mask["real"], mask["variable"]
            if not os.path.isfile(target) or not os.path.isfile(real):
                return fallback()
            private = os.path.join(directory, str(index))
            fd = os.open(private, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            argv += ["--ro-bind", real, private, "--ro-bind", shim, target]
            env[variable] = private
        probe = subprocess.run([*argv, "--", sys.executable, "-c", "pass"],
                               env=env, capture_output=True, timeout=10)
        if probe.returncode:
            print("Clash Verge: mount isolation unavailable; using command interception",
                  file=sys.stderr)
            return fallback()
        return [*argv, "--", command], directory
    except (OSError, ValueError, subprocess.SubprocessError):
        return fallback()


def launch(expected, command, defaults, delay, args=(), observe=processes):
    directory = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'clash-verge'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(directory / 'launch.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    child = None
    control_dir = None
    try:
        with os.fdopen(fd) as lock:
            info = os.fstat(lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise RuntimeError('invalid GUI launch lock')
            fcntl.flock(lock, fcntl.LOCK_EX)
            prepare(expected, observe=observe)
            if not observe():
                if subprocess.run([defaults, delay], check=False).returncode:
                    print('Clash Verge: GUI defaults could not be normalized', file=sys.stderr)
            environment = control_environment()
            argv, control_dir = control_argv(command, environment)
            child = subprocess.Popen([*argv, *args], env=environment)
            # Keep the lock across the gap between normalization and exec. Once
            # the GUI is visible, subsequent launchers observe it and skip edits.
            deadline = time.monotonic() + 15
            while child.poll() is None and not observe():
                if time.monotonic() >= deadline:
                    raise RuntimeError('GUI process did not become observable')
                time.sleep(0.05)
        return child.wait()
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if control_dir is not None:
            shutil.rmtree(control_dir, ignore_errors=True)


def main():
    if len(sys.argv) >= 6 and sys.argv[1] == '--launch':
        try:
            return launch(*sys.argv[2:6], args=sys.argv[6:])
        except (OSError, RuntimeError, subprocess.SubprocessError):
            print('Clash Verge: GUI launch failed', file=sys.stderr)
            return 1
    if len(sys.argv) != 2:
        raise SystemExit("usage: clash-verge-gui-prepare EXPECTED_ARGV0 | --running | --stop")
    try:
        if sys.argv[1] == "--running":
            return 0 if processes() else 1
        if sys.argv[1] == "--stop":
            return 0 if stop(processes()) else 1
        prepare(sys.argv[1])
    except (OSError, RuntimeError) as error:
        print("Clash Verge: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
