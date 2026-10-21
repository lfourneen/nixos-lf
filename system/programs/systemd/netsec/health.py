"""Bounded, cancellable Mihomo health probe. Never changes the core configuration."""

import hashlib
import ipaddress
import json
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, urlsplit


class ProbeFailure(Exception):
    def __init__(self, layer, detail):
        self.layer, self.detail = layer, detail
        super().__init__(detail)


def config_view(config):
    """Only invariant fields; never persist subscriptions, credentials or nodes."""
    view = {key: config.get(key) for key in (
        "mode", "routing-mark", "mixed-port", "port", "socks-port", "redir-port",
        "tproxy-port", "allow-lan", "ipv6", "log-level")}
    tun = config.get("tun") or {}
    view["tun"] = {key: tun.get(key) for key in (
        "enable", "device", "stack", "auto-route", "auto-redirect", "strict-route",
        "mtu", "iproute2-table-index")}
    view["tun"]["stack"] = str(tun.get("stack", "")).lower()
    for key in ("route-exclude-address", "inet6-address", "dns-hijack",
                "route-address-set", "route-exclude-address-set"):
        view["tun"][key] = sorted(tun.get(key) or [])
    return view


def check_config(config, settings):
    view = config_view(config)
    if view["mode"] == "direct":
        raise ProbeFailure("direct-mode", "core mode is direct")
    if view["mode"] not in {"rule", "global"}:
        raise ProbeFailure("config", "unknown core mode")
    for key, expected in settings["expected"].items():
        if key == "tun":
            for field, value in expected.items():
                if view["tun"].get(field) != value:
                    raise ProbeFailure("config", f"tun.{field} differs from managed configuration")
        elif view.get(key) != expected:
            raise ProbeFailure("config", f"{key} differs from managed configuration")
    return hashlib.sha256(json.dumps(view, sort_keys=True).encode()).hexdigest()


def effective_policy(text, proxies):
    """Resolve the exact logged policy and its selected leaf, including odd names."""
    names = sorted(proxies, key=len, reverse=True)
    name = next((p for p in names if text == p or text.startswith(p + "[")), None)
    if name is None:
        raise ProbeFailure("obs", "logged policy is absent from the API graph")
    group, seen = name, set()
    for _ in range(32):
        if name in seen or name not in proxies:
            raise ProbeFailure("obs", "cyclic or incomplete policy graph")
        seen.add(name)
        item = proxies[name]
        if name in {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE"} \
                or str(item.get("type", "")).lower() in {"direct", "reject", "pass", "compatible"}:
            raise ProbeFailure("route", "probe selected a non-proxy policy")
        selected = item.get("now")
        if not selected:
            if item.get("all"):
                raise ProbeFailure("obs", "group does not expose a selected leaf")
            return group, name
        name = selected
    raise ProbeFailure("obs", "policy nesting exceeds 32 levels")


def route_line(contents, host, port):
    pattern = re.compile(r"127\.0\.0\.1:" + re.escape(str(port)) + r" --> "
                         + re.escape(host) + r"(?::[0-9]+)?(?:\s|$)")
    result = None
    for line in contents.splitlines():
        try:
            payload = json.loads(line).get("payload", "")
        except (ValueError, AttributeError):
            continue
        if isinstance(payload, str) and pattern.search(payload) and "using " in payload:
            result = payload.rsplit("using ", 1)[1].strip()
    return result


class Probe:
    def __init__(self, settings, clock=time.monotonic):
        self.settings, self.clock = settings, clock
        self.start = clock()
        self.deadline = self.start + settings["budget_seconds"]
        self.timings = {}

    def remaining(self, maximum):
        remaining = min(maximum, self.deadline - self.clock())
        if remaining <= 0:
            raise ProbeFailure("obs", "total probe deadline exhausted")
        return remaining

    def command(self, argv, maximum=2.5):
        duration = self.remaining(maximum)
        try:
            result = subprocess.run(argv, capture_output=True, text=True,
                                    timeout=duration, check=False)
            return result.stdout if result.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def api(self, path, maximum=1.0):
        duration = self.remaining(maximum)
        value = self.command([self.settings["curl"], "--noproxy", "*", "-sS",
                              "--max-time", str(duration), "--unix-socket",
                              self.settings["socket"], "http://localhost" + path], duration)
        try:
            return json.loads(value)
        except ValueError:
            raise ProbeFailure("obs", "controller reply unavailable or invalid") from None

    def timed(self, name, callback):
        start = self.clock()
        try:
            return callback()
        finally:
            self.timings[name] = round((self.clock() - start) * 1000)

    def tun_test(self):
        if not self.settings["require_tun"]:
            return
        dig, name = self.settings["dig"], self.settings["tun_probe_name"]
        answer = self.command([dig, "+time=1", "+tries=1", "+short", "@127.0.0.1",
                               "-p", str(self.settings["dns_port"]), name, "A"], 1.5)
        network = ipaddress.ip_network(self.settings["fake_ip_net"], strict=False)
        fake_ip = None
        for line in answer.splitlines():
            try:
                address = ipaddress.ip_address(line.strip())
            except ValueError:
                continue
            if address in network:
                fake_ip = str(address)
                break
        if not fake_ip:
            raise ProbeFailure("tun", "managed DNS did not return a fake IP")
        if not self.command([dig, "+time=1", "+tries=1", "+short", "@" + fake_ip,
                             "-p", "53", name, "A"], 1.5).strip():
            raise ProbeFailure("tun", "TUN DNS self-test did not answer")

    def target(self, url, log_path):
        host = urlsplit(url).hostname
        if not host:
            raise ProbeFailure("config", "invalid health target URL")
        # Fresh source ports disambiguate the log from other local applications.
        output = self.command([self.settings["curl"], "-sS", "-o", "/dev/null", "-w",
                               "%{http_code} %{local_port}", "--max-time", "2.5",
                               "--noproxy", "", "--local-port", "15010-15090", "-x",
                               f"http://127.0.0.1:{self.settings['mixed_port']}", url])
        fields = output.split()
        if len(fields) != 2 or fields[0] not in {"200", "204"} or not fields[1].isdigit():
            raise ProbeFailure("obs", "mixed HTTP request failed")
        until = self.clock() + self.remaining(0.6)
        policy = None
        while self.clock() < until:
            policy = route_line(log_path.read_text(errors="replace"), host, fields[1])
            if policy:
                break
            time.sleep(0.05)
        if not policy:
            raise ProbeFailure("obs", "no exact-source-port route log for this target")
        proxies = self.api("/proxies").get("proxies", {})
        group, member = effective_policy(policy, proxies)
        # Test the exact leaf, never a group's arbitrary healthy fallback member.
        delay = self.api("/proxies/" + quote(member, safe="") + "/delay?url="
                         + quote(url, safe="") + "&timeout=2500", 2.7)
        if not isinstance(delay.get("delay"), (int, float)) or delay["delay"] < 0:
            raise ProbeFailure("node", "effective selected node delay test failed")
        current = self.api("/proxies").get("proxies", {})
        if effective_policy(policy, current) != (group, member):
            raise ProbeFailure("obs", "selection changed during the node test")

    def run(self, fast=False):
        fingerprint = self.timed("config", lambda: check_config(self.api("/configs"), self.settings))
        if fast:
            return fingerprint
        with tempfile.TemporaryDirectory(prefix="health-", dir=self.settings["runtime"]) as temporary:
            log_path = Path(temporary) / "logs"
            with log_path.open("w") as stream:
                reader = subprocess.Popen([self.settings["curl"], "--noproxy", "*", "-NsS",
                                           "--max-time", str(self.remaining(30)), "--unix-socket",
                                           self.settings["socket"], "http://localhost/logs?level=info"],
                                          stdout=stream, stderr=subprocess.DEVNULL)
            try:
                time.sleep(min(0.15, self.remaining(0.15)))
                failures = []
                for number, url in enumerate(self.settings["urls"]):
                    try:
                        self.timed(f"target_{number}", lambda: self.target(url, log_path))
                        break
                    except ProbeFailure as error:
                        if error.layer in {"route", "config"}:
                            raise
                        failures.append(error)
                else:
                    raise next((e for e in failures if e.layer == "node"), failures[-1])
                self.timed("tun", self.tun_test)
                final = check_config(self.api("/configs"), self.settings)
                if final != fingerprint:
                    raise ProbeFailure("obs", "configuration changed during the probe")
                return final
            finally:
                reader.terminate()
                try:
                    reader.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    reader.kill()
                    reader.wait()


def main():
    def cancelled(_signal, _frame):
        raise ProbeFailure("interrupted", "probe cancelled by supervisor")
    signal.signal(signal.SIGTERM, cancelled)
    with open(sys.argv[1]) as stream:
        settings = json.load(stream)
    probe = Probe(settings)
    result = {"ok": False, "reason": "obs"}
    try:
        result.update(ok=True, reason="", fingerprint=probe.run(fast="--fast" in sys.argv[2:]))
    except ProbeFailure as error:
        result.update(reason=error.layer, detail=error.detail)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        result.update(detail=f"probe error: {type(error).__name__}")
    result.update(duration_ms=round((time.monotonic() - probe.start) * 1000), layers=probe.timings)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
