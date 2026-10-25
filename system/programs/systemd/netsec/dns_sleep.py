"""Revoke DNS leases before sleep and recover failed hooks without regranting."""

import contextlib
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time


REVOKE = "".join("flush set inet lf_filter " + name + "\n"
                 for name in ("dns_plaintext4", "dns_split4", "dns_split6"))


@contextlib.contextmanager
def policy_lock(root, timeout=5):
    with (root / "proxy-mode/lock").open("a") as lock:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("DNS policy lock busy") from None
                time.sleep(0.05)
        yield


def transition(settings, action, root=Path("/run"), run=subprocess.run):
    root = Path(root)
    runtime = root / "dns-policy"
    marker = runtime / "sleeping"
    with policy_lock(root):
        if action == "resume":
            state = run([settings["systemctl"], "show", "-p", "ActiveState", "--value", "sleep.target"],
                        text=True, capture_output=True, timeout=3, check=True).stdout.strip()
            if state not in {"inactive", "failed"}:
                raise RuntimeError("sleep target is still active or transitioning")
        elif action == "before":
            marker.touch()
        else:
            raise ValueError("unknown sleep action")
        # Keep the marker on any failure. The independent resume service retries
        # after sleep.target is inactive, including a failed ExecStart.
        run([settings["nft"], "-f", "-"], input=REVOKE, text=True,
            capture_output=True, timeout=5, check=True)
        if action == "resume":
            marker.unlink(missing_ok=True)
        temporary = runtime / "state.json.tmp"
        temporary.write_text(json.dumps({"status": "suspended" if action == "before" else "revoked",
                                         "monotonic_seconds": time.clock_gettime(time.CLOCK_BOOTTIME)}) + "\n")
        temporary.replace(runtime / "state.json")


if __name__ == "__main__":
    try:
        with open(sys.argv[1]) as stream:
            transition(json.load(stream), sys.argv[2])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print("dns-policy-sleep: " + type(error).__name__, file=sys.stderr)
        sys.exit(1)
