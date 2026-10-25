"""VM-only integration: actual nft events cannot feed back into lease renewal."""

from pathlib import Path
import subprocess
import sys
import tempfile
import time


def nft(value):
    subprocess.run(["nft", "-f", "-"], input=value, text=True, capture_output=True, check=True)


with tempfile.TemporaryDirectory() as temporary:
    directory = Path(temporary)
    log = directory / "wakes"
    callback = directory / "systemctl"
    callback.write_text("""#!/run/current-system/sw/bin/python3
from pathlib import Path
import subprocess
with Path(%r).open('a') as stream:
    stream.write('wake\\n')
subprocess.run(['nft', '-f', '-'], input='flush set inet lf_filter dns_split4\\nadd element inet lf_filter dns_split4 { 192.0.2.53 timeout 120s }\\n', text=True, check=True)
""" % str(log))
    callback.chmod(0o700)
    monitor = subprocess.Popen(["nft", "monitor", "ruleset"], stdout=subprocess.PIPE)
    consumer = subprocess.Popen([sys.executable, sys.argv[1], str(callback)], stdin=monitor.stdout)
    monitor.stdout.close()
    try:
        time.sleep(0.3)
        nft("add element inet lf_filter dns_split4 { 192.0.2.53 timeout 120s }\n")
        time.sleep(1.3)
        assert not log.exists(), "lease grant woke supervision"
        nft("flush set inet lf_filter dns_split4\nadd element inet lf_filter dns_split4 { 192.0.2.53 timeout 120s }\n")
        time.sleep(1.3)
        assert not log.exists(), "lease renewal woke supervision"
        nft("add chain inet lf_filter event_fixture\n")
        deadline = time.monotonic() + 3
        while not log.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert log.exists(), "policy definition change did not wake supervision"
        time.sleep(1.3)
        assert log.read_text().splitlines() == ["wake"], "callback renewal fed back into another wake"
    finally:
        consumer.terminate()
        consumer.wait(timeout=3)
        monitor.terminate()
        monitor.wait(timeout=3)
        nft("destroy chain inet lf_filter event_fixture\nflush set inet lf_filter dns_split4\n")
