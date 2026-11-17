"""Bounded, cancellable Mihomo health probe. Never changes the core configuration."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import importlib.util
import http.client
import ipaddress
import json
import os
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

SNAPSHOT_TOOLS = {}


def failure_stage(detail):
    """Persist the failed step without addresses, node names or curl output."""
    for prefix, stage in (
        ('mixed HTTP request failed', 'mixed-request'),
        ('no exact-source-port route log', 'mixed-route-log'),
        ('mixed route proof unavailable', 'mixed-route-proof'),
        ('fake-IP TCP probe failed', 'tun-request'),
        ('fake-IP TCP probe for', 'tun-route-log'),
        ('TUN route proof unavailable', 'tun-route-proof'),
        ('recorded leaf no longer', 'selection'),
        ('controller reply unavailable', 'controller'),
        ('managed DNS did not return', 'fake-ip-dns'),
        ('TUN DNS self-test', 'tun-dns'),
        ('real-address DNS self-test', 'real-address-dns'),
        ('total probe deadline', 'deadline'),
        ('configuration changed', 'configuration'),
        ('managed TCP/UDP listener', 'listener'),
        ('authorized snapshot', 'snapshot'),
        ('cannot verify the authorized snapshot', 'snapshot')):
        if detail.startswith(prefix):
            return stage
    return 'unknown'


class ProbeFailure(Exception):
    def __init__(self, layer, detail):
        self.layer, self.detail = layer, detail
        super().__init__(detail)


def config_view(config):
    """Only invariant fields; never persist subscriptions, credentials or nodes."""
    view = {key: config.get(key) for key in (
        "mode", "routing-mark", "mixed-port", "port", "socks-port", "redir-port",
        "tproxy-port", "allow-lan", "bind-address", "ipv6", "log-level",
        "managed-policy")}
    tun = config.get("tun") or {}
    view["tun"] = {key: tun.get(key) for key in (
        "enable", "device", "stack", "auto-route", "auto-redirect", "strict-route",
        "dns-mode", "mtu", "iproute2-table-index", "iproute2-rule-index",
        "auto-redirect-input-mark", "auto-redirect-output-mark",
        "auto-redirect-iproute2-fallback-rule-index", "file-descriptor")}
    view["tun"]["stack"] = str(tun.get("stack", "")).lower()
    # `auto-redirect` is a bool tagged `omitempty` in the core's JSON, so a
    # disabled value disappears from the controller response instead of
    # appearing as `false`. Absence is the disabled state for this field.
    view["tun"]["auto-redirect"] = bool(tun.get("auto-redirect", False))
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
                actual = view["tun"].get(field)
                if actual != value:
                    raise ProbeFailure(
                        "config",
                        f"tun.{field} differs from managed configuration (actual={actual!r}, expected={value!r})",
                    )
        elif view.get(key) != expected:
            raise ProbeFailure("config", f"{key} differs from managed configuration")
    digest_path = settings.get("digest_file")
    directory = settings.get("effective_directory")
    if directory and (settings.get("snapshot_required") or (digest_path and os.path.exists(digest_path))):
        try:
            path = settings['snapshot_tool']
            if path not in SNAPSHOT_TOOLS:
                spec = importlib.util.spec_from_file_location('snapshot_check', path)
                tool = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(tool)
                SNAPSHOT_TOOLS[path] = tool
            # Only metadata/authority checks run in this process. A miss uses
            # the existing cancellable, time-bounded full-verification worker.
            SNAPSHOT_TOOLS[path].verify(directory, digest_path, settings['authorization_file'],
                                        fast=True, cached_only=True)
            return hashlib.sha256(json.dumps(view, sort_keys=True).encode()).hexdigest()
        except (OSError, ValueError, ImportError):
            pass
        try:
            result = subprocess.run([sys.executable, settings["snapshot_tool"], "verify", directory,
                                     digest_path, settings["authorization_file"], "--fast"],
                                    text=True, capture_output=True, timeout=3, check=False)
        except (OSError, subprocess.SubprocessError) as error:
            raise ProbeFailure("config", f"cannot verify the authorized snapshot: {error}")
        if result.returncode:
            raise ProbeFailure("config", "authorized snapshot verification failed")
    return hashlib.sha256(json.dumps(view, sort_keys=True).encode()).hexdigest()


def effective_policy(text, proxies, route_class='proxy'):
    """Validate the connection's recorded leaf, never substitute a later `now`."""
    if route_class not in {'proxy', 'direct', 'reject'}:
        raise ProbeFailure('config', 'invalid approved probe route class')
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
    kind = str(proxies[leaf].get("type", "")).lower()
    direct = leaf.lower() in {'direct', 'compatible'} or kind in {'direct', 'compatible'}
    if route_class == 'direct' and not direct:
        raise ProbeFailure('route', 'probe did not use the approved direct policy')
    if (route_class == 'proxy' and direct) or leaf.lower() in {'reject', 'reject-drop', 'pass'} \
            or kind in {'reject', 'reject-drop', 'pass'} or route_class == 'reject':
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


def route_line(contents, host, port, address='127.0.0.1', destination_port=None):
    source = '[' + address + ']' if ':' in address else address
    destination = r':[0-9]+' if destination_port is None else ':' + re.escape(str(destination_port))
    pattern = re.compile(r'\[TCP\] ' + re.escape(source) + ':' + re.escape(str(port)) + r" --> "
                         + re.escape(host) + destination + r"(?:\s|$)")
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
        self.target_slots = 1
        self.fake_ips = {}
        self.fake_ip_failures = {}
        self.diagnostics = dict.fromkeys(('data_plane_ok', 'dns_ok', 'tun_ok', 'route_ok',
                                         'config_ok', 'listener_ok'))
        self.route_sources = {}
        self.exit_identities = {}
        self.routing = {}
        self.core_mode = 'unknown'
        self.tun_name = None

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
            if response.status != 200:
                raise ValueError('controller request failed')
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

    def request(self, argv, maximum, host, port):
        """Retain bounded live records while curl still owns its connection."""
        records = {}
        stopped = threading.Event()
        started = time.time()

        def observe():
            while not stopped.is_set():
                try:
                    value = self.api('/connections', 0.25)
                    for item in value.get('connections') or []:
                        metadata = item.get('metadata') or {}
                        if metadata.get('host') != host or metadata.get('network') != 'tcp' \
                                or str(metadata.get('destinationPort')) != str(port):
                            continue
                        # Ignore pre-existing flows even if a source port is reused.
                        stamp = datetime.fromisoformat(item['start'].replace('Z', '+00:00'))
                        if stamp.tzinfo is None or stamp.timestamp() < started:
                            continue
                        if len(records) < 64 and isinstance(item.get('id'), str):
                            records[item['id']] = {key: item.get(key) for key in
                                ('metadata', 'chains', 'rule', 'rulePayload')}
                except (ProbeFailure, ValueError, KeyError, TypeError, AttributeError):
                    pass
                stopped.wait(0.05)

        worker = threading.Thread(target=observe, daemon=True)
        worker.start()
        try:
            output = self.command(argv, maximum)
        finally:
            stopped.set()
            worker.join()
        return output, list(records.values())

    def prove_route(self, log_path, host, port, address, records, stage, route_class='proxy', destination_port=80):
        until = self.clock() + self.remaining(2.5)
        proxies = None
        while True:
            policy = route_line(log_path.read_text(errors='replace'), host, port, address,
                                destination_port) if log_path else None
            matches = [item for item in records if (item.get('metadata') or {}).get('sourceIP') == address
                       and str((item.get('metadata') or {}).get('sourcePort')) == str(port)
                       and (item.get('metadata') or {}).get('host') == host
                       and (item.get('metadata') or {}).get('network') == 'tcp'
                       and str((item.get('metadata') or {}).get('destinationPort')) == str(destination_port)]
            if policy or matches:
                if proxies is None:
                    proxies = self.api('/proxies').get('proxies', {})
                # An observed forbidden leaf is terminal, even if another source disagrees.
                proven = []
                errors = []
                for source, text in ([('log', policy)] if policy else []) + [
                        ('connections', self.connection_policy(item)) for item in matches]:
                    if text is None:
                        continue
                    try:
                        _, leaf = effective_policy(text, proxies, route_class)
                        self.exit_identities[stage] = hashlib.sha256(leaf.encode()).hexdigest()
                        proven.append(source)
                    except ProbeFailure as error:
                        if error.layer == 'route':
                            raise
                        errors.append(error)
                if proven:
                    self.route_sources[stage] = proven[0]
                    return
                if errors:
                    raise errors[-1]
            remaining = until - self.clock()
            if remaining <= 0:
                raise ProbeFailure('obs', f'{stage} route proof unavailable for the exact source and target')
            time.sleep(min(0.05, remaining))

    @staticmethod
    def connection_policy(item):
        chains = item.get('chains')
        if not isinstance(chains, list) or not chains or len(chains) > 32 \
                or not all(isinstance(name, str) and name for name in chains) \
                or not isinstance(item.get('rule'), str) or not isinstance(item.get('rulePayload'), str):
            return None
        return chains[0] if len(chains) == 1 else chains[-1] + '[' + chains[0] + ']'

    def tun_test(self, log_path=None):
        if not self.settings["require_tun"]:
            return
        failures = []
        self.diagnostics['tun_ok'] = False
        for name in self.settings.get('tun_probe_names', [self.settings['tun_probe_name']]):
            try:
                self.tun_target(name, log_path)
                self.diagnostics['tun_ok'] = True
                self.tun_name = name
                return
            except ProbeFailure as error:
                if error.layer in {'config', 'route', 'interrupted'}:
                    raise
                failures.append(error)
        # DNS failures take precedence over a later observation deadline.
        raise next((error for error in failures if error.layer == 'dns'), failures[-1])

    def fake_ip(self, name):
        if name in self.fake_ips:
            return self.fake_ips[name]
        if name in self.fake_ip_failures:
            raise self.fake_ip_failures[name]
        dig = self.settings['dig']
        timeout = self.settings.get("dns_timeout_seconds", 3)
        network = ipaddress.ip_network(self.settings["fake_ip_net"], strict=False)
        for _ in range(2):
            try:
                answer = self.command([dig, f"+time={int(timeout)}", "+tries=1", "+short", "@127.0.0.1",
                                       "-p", str(self.settings["dns_port"]), name, "A"], timeout)
            except ProbeFailure as error:
                if error.layer != 'obs':
                    raise
                break
            for line in answer.splitlines():
                try:
                    address = ipaddress.ip_address(line.strip())
                except ValueError:
                    continue
                if address in network:
                    self.fake_ips[name] = str(address)
                    return str(address)
        error = ProbeFailure("dns", "managed DNS did not return a fake IP")
        self.fake_ip_failures[name] = error
        raise error

    def application_dns(self):
        if not self.settings.get('require_tun'):
            return
        self.diagnostics['dns_ok'] = False
        failures = []
        names = list(dict.fromkeys(self.settings.get('tun_probe_names', [self.settings['tun_probe_name']])))
        # Warm every candidate before TUN TCP, without serial cold-query delays.
        with ThreadPoolExecutor(max_workers=min(4, len(names))) as workers:
            pending = [workers.submit(self.fake_ip, name) for name in names]
            for future in pending:
                try:
                    future.result()
                except ProbeFailure as error:
                    failures.append(error)
        if not self.fake_ips:
            raise failures[-1]

    def tun_target(self, name, log_path):
        fake_ip = self.fake_ip(name)
        dig = self.settings['dig']
        timeout = self.settings.get("dns_timeout_seconds", 3)
        answered = False
        for _ in range(2):
            try:
                answer = self.command([dig, f"+time={int(timeout)}", "+tries=1", "+short", "@" + fake_ip,
                                       "-p", "53", name, "A"], timeout)
            except ProbeFailure as error:
                if error.layer != 'obs':
                    raise
                break
            if any(line.strip() == fake_ip for line in answer.splitlines()):
                answered = True
                break
        if not answered:
            raise ProbeFailure("dns", "TUN DNS self-test did not answer")
        self.diagnostics['dns_ok'] = True
        # A root fake-IP TCP flow is excluded from gost's REDIRECT. It exercises
        # the core's effective TUN/auto-redirect data path as well as DNS hijack.
        # Let the kernel allocate a fresh ephemeral port. A small, repeatedly
        # scanned range reuses ports across different fake-IP candidates; their
        # redirects share one endpoint and conntrack can then rewrite a port.
        output, records = self.timed('tun_http', lambda: self.request([self.settings["curl"], "--noproxy", "*", "--head", "-sS",
                               "-o", "/dev/null", "--max-time", "2.5", "--max-filesize", "1048576",
                               "--resolve", name + ":80:" + fake_ip,
                               "-w", "%{http_code} %{local_port} %{local_ip}", "http://" + name + "/"], 2.7, name, 80))
        fields = output.split()
        if len(fields) != 3 or fields[0] not in {"200", "204", "301", "302", "307", "308"} \
                or not fields[1].isdigit():
            detail = getattr(self, "last_error", "") or output.strip()[:120] or "empty reply"
            raise ProbeFailure("obs", f"fake-IP TCP probe failed for {fake_ip}: {detail}")
        self.timed('tun_route', lambda: self.prove_route(log_path, name, fields[1], fields[2], records, 'TUN',
                         self.settings.get('tun_route_classes', {}).get(name, 'proxy')))

    def target(self, url, log_path):
        parsed = urlsplit(url)
        host = parsed.hostname
        if not host:
            raise ProbeFailure("config", "invalid health target URL")
        # Fresh source ports disambiguate the log from other local applications.
        timeout = self.remaining(min(self.settings["request_timeout_seconds"],
                                     self.remaining(30) / self.target_slots))
        if self.diagnostics['data_plane_ok'] is None:
            self.diagnostics['data_plane_ok'] = False
        self.diagnostics['route_ok'] = False
        output, records = self.timed('mixed_http', lambda: self.request([self.settings["curl"], "-sS", "-o", "/dev/null", "-w",
                               "%{http_code} %{local_port}", "--max-time", str(timeout),
                               "--noproxy", "", "--local-port", "15010-15090",
                               "--interface", self.settings.get('source_address', '127.0.0.1'), "-x",
                               f"http://127.0.0.1:{self.settings['mixed_port']}", url], timeout + 0.2,
                                      host, parsed.port or (443 if parsed.scheme == 'https' else 80)))
        fields = output.split()
        if len(fields) != 2 or fields[0] not in {"200", "204"} or not fields[1].isdigit():
            raise ProbeFailure("obs", "mixed HTTP request failed")
        self.diagnostics['data_plane_ok'] = True
        self.timed('mixed_route', lambda: self.prove_route(log_path, host, fields[1],
                         self.settings.get('source_address', '127.0.0.1'), records, 'mixed',
                         destination_port=parsed.port or (443 if parsed.scheme == 'https' else 80)))
        self.diagnostics['route_ok'] = True

    def routing_binding(self):
        if not self.settings.get('routing_state'):
            return {}
        value = json.loads(Path(self.settings['routing_state']).read_text())
        if value.get('routing_requested') != self.settings['routing_mode'] \
                or value.get('routing_phase') not in ('verifying', 'ready') \
                or value.get('routing_policy_digest') != self.settings['expected']['managed-policy'] \
                or type(value.get('routing_epoch')) is not int or value['routing_epoch'] < 1 \
                or value.get('boot_id') != (Path(self.settings['runtime']) / 'boot-id').read_text().strip():
            raise ProbeFailure('config', 'routing policy or transaction differs')
        keys = ['routing_requested', 'routing_epoch', 'routing_policy_digest']
        if value['routing_requested'] == 'proxy-only' and 'egress_epoch' in value:
            manifest = json.loads((Path(self.settings['effective_directory']) / 'source-manifest.json').read_text())
            if not re.fullmatch('[a-f0-9]{64}', str(value.get('egress_requested'))) \
                    or manifest.get('egress_id') != value['egress_requested']:
                raise ProbeFailure('config', 'egress snapshot differs from selected leaf')
            keys += ['egress_requested', 'egress_epoch']
        return {key: value[key] for key in keys}

    def real_dns(self):
        port = self.settings.get('real_dns_port')
        if not port:
            return
        self.diagnostics['real_dns_ok'] = False
        fake = ipaddress.ip_network(self.settings['fake_ip_net'])
        for name in ([self.tun_name] if self.tun_name else list(self.fake_ips)):
            timeout = self.settings.get('dns_timeout_seconds', 3)
            for _ in range(2):
                try:
                    output = self.command([self.settings['dig'], f'+time={timeout}', '+tries=1', '+short',
                                           '@127.0.0.1', '-p', str(port), name, 'A'], timeout)
                except ProbeFailure as error:
                    if error.layer == 'interrupted':
                        raise
                    break
                addresses = []
                for line in output.splitlines():
                    try:
                        addresses.append(ipaddress.ip_address(line.strip()))
                    except ValueError:
                        pass
                if addresses and all(address not in fake and not (address.is_loopback or address.is_link_local
                        or address.is_multicast or address.is_unspecified) for address in addresses):
                    self.diagnostics['real_dns_ok'] = True
                    return
        raise ProbeFailure('dns', 'real-address DNS self-test did not answer')

    def run(self, fast=False):
        self.routing = self.routing_binding()
        def listeners():
            try:
                subprocess.run([self.settings['listener_check'], 'core', 'dns'],
                               capture_output=True, timeout=self.remaining(4.5), check=True)
            except (OSError, subprocess.SubprocessError):
                raise ProbeFailure('config', 'managed TCP/UDP listener identity mismatch') from None
        self.diagnostics['listener_ok'] = False
        self.timed('listeners', listeners)
        self.diagnostics['listener_ok'] = True
        self.diagnostics['config_ok'] = False
        initial = self.api('/configs')
        self.core_mode = initial.get('mode', 'unknown')
        fingerprint = self.timed("config", lambda: check_config(initial, self.settings))
        self.diagnostics['config_ok'] = True
        if self.routing.get('egress_requested'):
            graph = self.api('/proxies')['proxies']
            selected = graph['lf-remote']['now']
            self.diagnostics['node_udp_supported'] = graph[selected].get('udp') is True
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
                    self.target_slots = len(self.settings['urls']) - number + int(self.settings.get('require_tun', False))
                    try:
                        self.timed(f"target_{number}", lambda: self.target(url, log_path))
                        break
                    except ProbeFailure as error:
                        if error.layer in {"route", "config", "interrupted"}:
                            raise
                        failures.append(error)
                else:
                    raise failures[-1]
                self.timed('application_dns', self.application_dns)
                self.timed("tun", lambda: self.tun_test(log_path))
                self.timed('real_dns', self.real_dns)
                self.diagnostics['config_ok'] = False
                final = check_config(self.api("/configs"), self.settings)
                if final != fingerprint:
                    raise ProbeFailure("obs", "configuration changed during the probe")
                if self.routing_binding() != self.routing:
                    raise ProbeFailure('config', 'routing changed during the probe')
                self.diagnostics['config_ok'] = True
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
    fast = "--fast" in sys.argv[2:]
    result = {"ok": False, "reason": "obs", "scope": {
        "listener_transports": ["tcp", "udp"],
        "requests": [] if fast else ["mixed-tcp"] + (["fake-ip-tcp"] if settings.get("require_tun") else [])
            + (['real-address-dns'] if settings.get('real_dns_port') else []),
        "udp_data_plane": "not-probed",
    }}
    try:
        result.update(ok=True, reason="", fingerprint=probe.run(fast=fast))
    except ProbeFailure as error:
        result.update(reason=error.layer, detail=error.detail, failure_stage=failure_stage(error.detail))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        result.update(detail=f"probe error: {type(error).__name__}")
    result.update(probe.diagnostics | probe.routing, core_mode=probe.core_mode,
                  exit_identities=probe.exit_identities, route_sources=probe.route_sources,
                  duration_ms=round((time.monotonic() - probe.start) * 1000), layers=probe.timings)
    if os.geteuid() == 0 and not fast and result.get('reason') != 'interrupted':
        classification = result.get('reason')
        if classification not in ('node', 'tun', 'dns', 'config', 'route', 'obs', 'direct-mode', 'no-tun', 'core-gone'):
            classification = 'obs'
        if not result['ok']:
            print('proxy health warning: ' + classification, file=sys.stderr)
        record = {key: result[key] for key in ('ok', 'reason', 'duration_ms', 'layers')}
        record.update(probe.diagnostics, route_sources=probe.route_sources)
        record.update(probe.routing, core_mode=probe.core_mode)
        record['failure_stage'] = result.get('failure_stage', 'unknown')
        record['at'] = time.time()
        path = None
        try:
            runtime = Path(settings['runtime'])
            record['boot_id'] = (runtime / 'boot-id').read_text().strip()
            record['digest'] = Path(settings['digest_file']).read_text().strip()
            observations = runtime / 'health-observations.json'
            try:
                history = json.loads(observations.read_text())
                history = [item for item in history if isinstance(item, dict)
                           and item.get('boot_id') == record['boot_id']][-19:]
            except (OSError, ValueError, TypeError):
                history = []
            with tempfile.NamedTemporaryFile(mode='w', prefix='.health-failure-', dir=runtime, delete=False) as stream:
                path = Path(stream.name)
                json.dump(history + [record], stream)
            path.chmod(0o644)
            path.replace(observations)
            if not result['ok']:
                with tempfile.NamedTemporaryFile(mode='w', prefix='.health-failure-', dir=runtime, delete=False) as stream:
                    path = Path(stream.name)
                    json.dump(record, stream)
                path.chmod(0o644)
                path.replace(runtime / 'health-failure.json')
        except (OSError, KeyError):
            # Failure to persist diagnostics cannot turn a failed probe into ok.
            pass
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
