"""VM-only: exercise the generated NM wake hook against real systemd jobs."""

from pathlib import Path
import subprocess
import sys
import tempfile
import time


def run(*args, timeout=10):
    return subprocess.run(args, check=True, text=True, capture_output=True, timeout=timeout)


def pending(unit):
    return run("systemctl", "show", unit, "--property=Job", "--value").stdout.strip() not in {"", "0"}


def main(hook):
    # The dispatcher must finish before network-online, while its wake target
    # waits for network-online. Blocking systemctl inside the callback deadlocks
    # these jobs even though the declarative unit ordering is acyclic.
    online = subprocess.run(["systemctl", "is-active", "--quiet", "network-online.target"]).returncode == 0
    root = Path("/run/systemd/system")
    dispatch = root / "netsec-dispatch.service"
    target_dropin = root / "network-online.target.d/90-netsec-lifecycle.conf"
    wake_dropin = root / "proxy-net-wake.service.d/90-netsec-lifecycle.conf"
    paths = (dispatch, target_dropin, wake_dropin)
    assert all(not path.exists() for path in paths)
    with tempfile.TemporaryDirectory(prefix="netsec-lifecycle-", dir="/run") as temporary:
        directory = Path(temporary)
        marker = directory / "wake-finished"
        mutant = directory / "blocking-hook"
        source = Path(hook).read_text()
        assert source.count("--no-block") == 1, "update the blocking-callback mutation"
        mutant.write_text(source.replace("--no-block", ""))
        mutant.chmod(0o700)

        def stop():
            run("systemctl", "stop", "proxy-net-wake.service", "netsec-dispatch.service", "network-online.target")

        def prepare(script, event):
            stop()
            marker.unlink(missing_ok=True)
            dispatch.write_text(
                "[Unit]\nBefore=network-online.target\n"
                "[Service]\nType=oneshot\nRemainAfterExit=yes\nTimeoutStartSec=20\n"
                f"ExecStart={script} wlo1 {event}\n")
            run("systemctl", "daemon-reload")

        try:
            for path in (target_dropin, wake_dropin):
                path.parent.mkdir(parents=True, exist_ok=True)
            target_dropin.write_text("[Unit]\nRequires=netsec-dispatch.service\n")
            wake_dropin.write_text(
                "[Unit]\nAfter=network-online.target\n"
                f"[Service]\nExecStartPost=/run/current-system/sw/bin/touch {marker}\n")
            # Materialize the unit before stop() first references it.
            dispatch.write_text("[Service]\nType=oneshot\nExecStart=/run/current-system/sw/bin/true\n")
            run("systemctl", "daemon-reload")
            prepare(mutant, "connectivity-change")
            try:
                run("systemctl", "start", "network-online.target", timeout=3)
            except subprocess.TimeoutExpired:
                pass
            else:
                raise AssertionError("blocking callback did not reproduce the wait-online deadlock")
            assert pending("network-online.target") and pending("proxy-net-wake.service")
            assert run("systemctl", "show", "netsec-dispatch.service", "--property=ActiveState", "--value").stdout.strip() == "activating"
            assert not marker.exists(), "wake ran before the dispatcher completed"

            for event in ("connectivity-change", "up", "down", "dhcp4-change", "dhcp6-change"):
                prepare(hook, event)
                run("systemctl", "start", "network-online.target", timeout=5)
                run("systemctl", "is-active", "--quiet", "network-online.target")
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                assert marker.exists(), (event, "queued production wake did not finish")
                assert run("systemctl", "show", "netsec-dispatch.service", "--property=Result", "--value").stdout.strip() == "success"
            print("Blocking callback deadlocked; all five production NM events completed and woke supervision")
        finally:
            stop()
            run("systemctl", "reset-failed", "netsec-dispatch.service", "proxy-net-wake.service")
            for path in paths:
                path.unlink(missing_ok=True)
            run("systemctl", "daemon-reload")
            if online:
                run("systemctl", "start", "network-online.target")


if __name__ == "__main__":
    main(sys.argv[1])
