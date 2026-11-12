"""Real-core regression: a selectable DIRECT leaf must fail before egress."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def main(core):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        policy = {"lf-private-targets": {"prefixes": ["10.0.0.0/8"],
                                        "allowed-domains": [], "proxy-only": True}}
        (root / "policy.json").write_text(json.dumps(policy))
        profile = {"mixed-port": 17897, "external-controller": "127.0.0.1:19090",
                   "routing-mark": 6666, "mode": "rule", "log-level": "info",
                   "proxies": [{"name": "Upstream", "type": "socks5", "server": "100.67.0.1", "port": 18080}],
                   "proxy-groups": [{"name": "Choice", "type": "select", "proxies": ["DIRECT", "Upstream"]}],
                   "rules": ["MATCH,Choice"]}
        (root / "profile.json").write_text(json.dumps(profile))
        environment = dict(os.environ, LF_MANAGED_PROXY_POLICY=str(root / "policy.json"))
        with (root / "core.log").open("w+") as log:
            process = subprocess.Popen([core, "-d", directory, "-f", str(root / "profile.json")],
                                       env=environment, stdout=log, stderr=subprocess.STDOUT)
            def api(method, path, value=None):
                connection = http.client.HTTPConnection("127.0.0.1", 19090, timeout=2)
                try:
                    connection.request(method, path, None if value is None else json.dumps(value),
                                       {"Content-Type": "application/json"})
                    response = connection.getresponse(); response.read()
                    assert response.status < 300, response.status
                finally:
                    connection.close()
            def request():
                return subprocess.run(["curl", "--noproxy", "", "-fsS", "--max-time", "3", "-x",
                                       "http://127.0.0.1:17897", "-w", "\n%{http_code}",
                                       "http://203.0.113.10:8080/"],
                                      capture_output=True, text=True, timeout=4)
            try:
                deadline = time.monotonic() + 10
                while True:
                    assert process.poll() is None, "strict core exited"
                    try:
                        api("GET", "/configs"); break
                    except OSError:
                        assert time.monotonic() < deadline, "strict core startup timeout"
                        time.sleep(0.05)
                assert request().returncode != 0, "nested DIRECT leaked application data"
                api("PUT", "/proxies/Choice", {"name": "Upstream"})
                result = request()
                assert result.returncode == 0 and result.stdout.endswith("\n200"), (
                    result.returncode, result.stdout[-100:], result.stderr)
                api("PUT", "/proxies/Choice", {"name": "DIRECT"})
                assert request().returncode != 0, "selector switch restored DIRECT"
                api("PATCH", "/configs", {"mode": "global"})
                api("PUT", "/proxies/GLOBAL", {"name": "DIRECT"})
                assert request().returncode != 0, "GLOBAL DIRECT leaked application data"
                log.flush(); log.seek(0)
                assert "refuses application DIRECT egress" in log.read()
                print("strict real core: nested/changed/GLOBAL DIRECT refused; proxy leaf succeeded")
            except BaseException:
                log.flush(); log.seek(0); print(log.read(), file=sys.stderr)
                raise
            finally:
                process.terminate()
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=3)


if __name__ == "__main__":
    main(sys.argv[1])
