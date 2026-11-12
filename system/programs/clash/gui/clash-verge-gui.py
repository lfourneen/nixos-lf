"""Finish an old packaged GUI before launching the current one, as its own user."""
import fcntl
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

GUI_NAMES = {"clash-verge", ".clash-verge-launcher", ".clash-verge-wrapped"}


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


def launch(expected, command, defaults, delay, args=(), observe=processes):
    directory = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'clash-verge'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(directory / 'launch.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    child = None
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
            child = subprocess.Popen([command, *args])
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
