"""Finish an old packaged GUI before launching the current one, as its own user."""
import os
from pathlib import Path
import signal
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


def main():
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
