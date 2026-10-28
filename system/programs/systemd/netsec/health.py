"""Bounded, cancellable Mihomo health probe. Never changes the core configuration."""

import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit


class ProbeFailure(Exception):
    def __init__(self, layer, detail):
        self.layer, self.detail = layer, detail
        super().__init__(detail)


def config_view(config):
    """Only invariant fields; never persist subscriptions, credentials or nodes."""
    view = {key: config.get(key) for key in (
        "mode", "routing-mark", "mixed-port", "port", "socks-port", "redir-port",
        "tproxy-port", "allow-lan", "ipv6", "log-level", "managed-policy")}
    tun = config.get("tun") or {}
    view["tun"] = {key: tun.get(key) for key in (
        "enable", "device", "stack", "auto-route", "auto-redirect", "strict-route",
        "dns-mode", "mtu", "iproute2-table-index", "iproute2-rule-index",
        "auto-redirect-input-mark", "auto-redirect-output-mark",
        "auto-redirect-iproute2-fallback-rule-index", "file-descriptor")}
    view["tun"]["stack"] = str(tun.get("stack", "")).lower()
    for key in ("route-exclude-address", "inet6-address", "dns-hijack",
                "route-address-set", "route-exclude-address-set", "route-address",
                "include-interface", "exclude-interface", "include-uid", "exclude-uid",
                "include-uid-range", "exclude-uid-range"):
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
    """Validate the connection's recorded leaf, never substitute a later `now`."""
    def group(item):
        return "all" in item or "now" in item

    candidates = []
    for outer, item in proxies.items():
        if not isinstance(item, dict):
            raise ProbeFailure("obs", "invalid policy graph")
        if text == outer and not group(item):
            candidates.append((outer, outer))
        prefix = outer + "["
        if group(item) and text.startswith(prefix) and text.endswith("]"):
            leaf = text[len(prefix):-1]
            if leaf in proxies and not group(proxies[leaf]):
                candidates.append((outer, leaf))
    # Match the full rendering against known names, including brackets inside
    # either name. Ambiguous renderings cannot establish which leaf was used.
    if len(candidates) != 1:
        raise ProbeFailure("obs", "connection policy is absent or ambiguous")
    outer, leaf = candidates[0]
    forbidden = {"direct", "reject", "reject-drop", "pass", "compatible"}
    if leaf.lower() in forbidden or str(proxies[leaf].get("type", "")).lower() in forbidden:
        raise ProbeFailure("route", "probe selected a non-proxy policy")

    pending = [(outer, frozenset())]
    visits = 0
    while pending:
        name, seen = pending.pop()
        visits += 1
        if visits > 4096 or len(seen) >= 32 or name in seen or name not in proxies:
            raise ProbeFailure("obs", "cyclic, incomplete or oversized policy graph")
        if name == leaf:
            return outer, leaf
        item = proxies[name]
        selected = item.get("now")
        if selected:
            children = [selected]
        elif str(item.get("type", "")).lower() == "loadbalance":
            # LoadBalance chooses per connection and intentionally has no now.
            children = item.get("all") or []
        else:
            children = []
        pending.extend((child, seen | {name}) for child in children)
    raise ProbeFailure("obs", "recorded leaf no longer matches the current selection")


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
        self.last_error = ""
        try:
            result = subprocess.run(argv, capture_output=True, text=True,
                                    timeout=duration, check=False)
            if result.returncode == 0:
                return result.stdout
            self.last_error = (result.stderr or result.stdout or "").strip()[:160]
            return ""
        except (OSError, subprocess.TimeoutExpired) as error:
            self.last_error = f"{type(error).__name__}: {error}"[:160]
            return ""

    def api(self, path, maximum=1.0):
        duration = self.remaining(maximum)
        connection = http.client.HTTPConnection("localhost", timeout=duration)
        timer = None
        try:
            connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.sock.settimeout(duration)
            connection.sock.connect(self.settings["socket"])
            transport = connection.sock
            def expire():
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            timer = threading.Timer(self.remaining(duration), expire)
            timer.daemon = True
            timer.start()
            connection.request("GET", path)
            response = connection.getresponse()
            value = response.read(8 * 1024 * 1024 + 1)
            if len(value) > 8 * 1024 * 1024:
                raise ValueError("controller response too large")
            return json.loads(value)
        except (OSError, ValueError, http.client.HTTPException):
            raise ProbeFailure("obs", "controller reply unavailable or invalid") from None
        finally:
            if timer is not None:
                timer.cancel()
            connection.close()

    def timed(self, name, callback):
        start = self.clock()
        try:
            return callback()
        finally:
            self.timings[name] = round((self.clock() - start) * 1000)

    def tun_test(self, log_path=None):
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
        # A root fake-IP TCP flow is excluded from gost's REDIRECT. It exercises
        # the core's effective TUN/auto-redirect data path as well as DNS hijack.
        output = self.command([self.settings["curl"], "--noproxy", "*", "--head", "-sS",
                               "-o", "/dev/null", "--max-time", "2.5", "--max-filesize", "1048576",
                               "--local-port", "15110-15190", "--resolve", name + ":80:" + fake_ip,
                               "-w", "%{http_code} %{local_port}", "http://" + name + "/"], 2.7)
        fields = output.split()
        if len(fields) != 2 or fields[0] not in {"200", "204", "301", "302", "307", "308"} \
                or not fields[1].isdigit():
            detail = getattr(self, "last_error", "") or output.strip()[:120] or "empty reply"
            raise ProbeFailure("obs", f"fake-IP TCP probe failed for {fake_ip}: {detail}")
        if log_path is None:
            raise ProbeFailure("obs", "TUN data probe has no route observation")
        pattern = re.compile(r":" + re.escape(fields[1]) + r" --> " + re.escape(name) + r":80(?:\s|$)")
        until = self.clock() + self.remaining(0.6)
        while self.clock() < until:
            for line in log_path.read_text(errors="replace").splitlines():
                try:
                    payload = json.loads(line).get("payload", "")
                except (ValueError, AttributeError):
                    continue
                if isinstance(payload, str) and pattern.search(payload) and "using " in payload:
                    return
            time.sleep(0.05)
        raise ProbeFailure("obs", f"fake-IP TCP probe for {fake_ip}:{fields[1]} has no exact-source route log")

    def target(self, url, log_path):
        host = urlsplit(url).hostname
        if not host:
            raise ProbeFailure("config", "invalid health target URL")
        # Fresh source ports disambiguate the log from other local applications.
        timeout = self.remaining(self.settings["request_timeout_seconds"])
        output = self.command([self.settings["curl"], "-sS", "-o", "/dev/null", "-w",
                               "%{http_code} %{local_port}", "--max-time", str(timeout),
                               "--noproxy", "", "--local-port", "15010-15090", "-x",
                               f"http://127.0.0.1:{self.settings['mixed_port']}", url], timeout + 0.2)
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
        # This successful request already exercised the recorded leaf. A second
        # /delay call mutates Mihomo's alive/history state and can change URLTest
        # selection, so the supervisor only reads and validates the live graph.
        effective_policy(policy, proxies)

    def run(self, fast=False):
        def listeners():
            try:
                subprocess.run([self.settings['listener_check'], 'core', 'dns'],
                               capture_output=True, timeout=self.remaining(4.5), check=True)
            except (OSError, subprocess.SubprocessError):
                raise ProbeFailure('config', 'managed TCP/UDP listener identity mismatch') from None
        self.timed('listeners', listeners)
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
                    raise failures[-1]
                self.timed("tun", lambda: self.tun_test(log_path))
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
