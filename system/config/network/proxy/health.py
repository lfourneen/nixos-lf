"""Bounded Mihomo health checks and recovery within managed proxy groups."""

import hashlib
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
import errno
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
from urllib.parse import quote, urlencode, urlsplit

SNAPSHOT_TOOLS = {}
FAILED_SESSIONS = {'node-auth', 'session-unavailable'}


def failure_stage(detail):
    """Persist the failed step without addresses, node names or curl output."""
    for prefix, stage in (
        ('mixed HTTP request failed', 'mixed-request'),
        ('sampled sessions passed but HTTP targets', 'target-unavailable'),
        ('authorized leaves did not answer', 'leaf-unavailable'),
        ('sampled leaves rejected authentication', 'node-auth'),
        ('sampled leaves could not establish', 'session-unavailable'),
        ('managed validator did not resolve', 'validator-dns'),
        ('system resolver did not answer', 'application-dns'),
        ('remote path unavailable during an active Rules lease', 'remote-outage'),
        ('no exact-source-port route log', 'mixed-route-log'),
        ('mixed route proof unavailable', 'mixed-route-proof'),
        ('fake-IP TCP probe failed', 'tun-request'),
        ('fake-IP TCP probe for', 'tun-route-log'),
        ('TUN route proof unavailable', 'tun-route-proof'),
        ('recorded leaf no longer', 'selection'),
        ('controller reply unavailable', 'controller'),
        ('controller socket unavailable', 'controller'),
        ('managed DNS did not return', 'fake-ip-dns'),
        ('TUN DNS self-test', 'tun-dns'),
        ('real-address DNS self-test', 'real-address-dns'),
        ('WireGuard', 'ip-tunnel'),
        ('total probe deadline', 'deadline'),
        ('configuration changed', 'configuration'),
        ('managed TCP/UDP listener', 'listener'),
        ('listener observation unavailable', 'listener-observation'),
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
            raise ProbeFailure("obs", f"cannot verify the authorized snapshot: {error}")
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
        self.dns_workers = threading.local()
        self.diagnostics = dict.fromkeys(('data_plane_ok', 'dns_ok', 'tun_ok', 'route_ok',
                                         'config_ok', 'listener_ok'))
        self.route_sources = {}
        self.exit_identities = {}
        self.routing = {}
        self.core_mode = 'unknown'
        self.tun_name = None
        self.sessions = {}
        self.dns_transport_leaf = None
        self.failure_stages = []
        self.snapshot = None

    def note_failure(self, stage):
        if stage != 'unknown' and (not self.failure_stages or self.failure_stages[-1] != stage):
            self.failure_stages.append(stage)

    def dns_leaf(self, name):
        self.diagnostics['dns_leaf'] = hashlib.sha256(name.encode()).hexdigest()[:12]
        observed = self.sessions.get(name)
        self.diagnostics['dns_leaf_session'] = observed or 'unmeasured'

    def remaining(self, maximum):
        remaining = min(maximum, self.deadline - self.clock())
        if remaining <= 0:
            raise ProbeFailure("obs", "total probe deadline exhausted")
        return remaining

    def command(self, argv, maximum=2.5):
        duration = self.remaining(maximum)
        cancelled = getattr(self.dns_workers, 'cancelled', None)
        if cancelled is not None:
            # A DNS candidate stays scoped to its own child process. A winning
            # path can cancel the other lookups without waiting for dig's timeout.
            try:
                with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) as child:
                    deadline = self.clock() + duration
                    while not cancelled.is_set() and self.clock() < deadline:
                        try:
                            stdout, _ = child.communicate(timeout=min(0.05, max(0.001, deadline - self.clock())))
                            return stdout if child.returncode == 0 else ""
                        except subprocess.TimeoutExpired:
                            pass
                    child.kill()
            except OSError:
                pass
            return ""
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

    def api(self, path, maximum=1.0, *, body=None, cleanup=False):
        duration = maximum if cleanup else self.remaining(maximum)
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
            timer = threading.Timer(duration if cleanup else self.remaining(duration), expire)
            timer.daemon = True
            timer.start()
            if body is None:
                connection.request("GET", path)
            else:
                connection.request("PUT", path, json.dumps(body), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            value = response.read(8 * 1024 * 1024 + 1)
            if len(value) > 8 * 1024 * 1024:
                raise ValueError("controller response too large")
            if response.status == 409 and body is not None:
                raise ProbeFailure('obs', 'recorded leaf no longer matches the selector generation')
            if response.status != 200:
                raise ValueError('controller request failed')
            return json.loads(value)
        except (OSError, ValueError, http.client.HTTPException) as error:
            if isinstance(error, http.client.RemoteDisconnected) or getattr(error, "errno", None) in (
                    errno.ENOENT, errno.ECONNREFUSED, errno.ECONNRESET, errno.EPIPE):
                raise ProbeFailure('controller-unavailable', 'controller socket unavailable') from None
            raise ProbeFailure("obs", "controller reply unavailable or invalid") from None
        finally:
            if timer is not None:
                timer.cancel()
            connection.close()

    def initial_config(self, fast):
        deadline = self.clock() + self.remaining(2.0)
        while True:
            try:
                return self.api('/configs')
            except ProbeFailure as error:
                wait = min(0.15, deadline - self.clock())
                if fast or error.layer != 'controller-unavailable' or wait <= 0:
                    raise
                time.sleep(wait)

    def selector(self, group):
        if self.routing.get('routing_requested') == 'proxy-only' or self.routing.get('egress_requested'):
            return None, []
        if self.settings.get('dns_leaf_group'):
            record = self.api('/proxies/' + group)
        else:
            record = self.api('/proxies').get('proxies', {}).get(group, {})
        members, current = record.get('all'), record.get('now')
        if (record.get('type') != 'Selector' or not isinstance(members, list)
                or not all(isinstance(name, str) for name in members) or current not in members):
            return None, []
        index = members.index(current)
        return current, list(dict.fromkeys(members[index + 1:] + members[:index]))

    def leaf_candidates(self, url):
        current, members = self.selector('lf-remote')
        if current and self.settings.get('session_urls') and current not in self.sessions:
            self.sample_sessions([current, *members[:3]])
        if self.settings.get('session_urls') or self.sessions or self.dns_transport_leaf in members:
            def rank(name):
                outcome = self.sessions.get(name)
                if outcome == 'ok' or (outcome is None and name == self.dns_transport_leaf):
                    return 0
                return 1 if outcome is None else 2
            return sorted(members, key=rank)[:4]
        if not members or len(members) > 4 or self.deadline - self.clock() < 4:
            # A group delay contacts every member, including the current leaf.
            return members[:4]
        path = '/group/lf-remote/delay?url=' + quote(url, safe='') + '&timeout=1200'
        try:
            delays = self.api(path, 1.8)
        except ProbeFailure as error:
            if error.layer != 'obs':
                raise
            return members
        if not isinstance(delays, dict):
            return members
        return sorted(members, key=lambda name: delays[name] if type(delays.get(name)) is int
                      and delays[name] > 0 else float('inf'))

    def selection_binding(self, group, current):
        path = '/proxies/' + group + '/selection'
        observed = self.api(path)
        if observed.get('now') != current or not isinstance(observed.get('token'), str):
            raise ProbeFailure('obs', 'recorded leaf no longer matches the selector generation')
        return {'path': path, 'token': observed['token']}

    def try_leaf(self, binding, name, check):
        trial = self.api(binding['path'], body=dict(action='trial', token=binding['token'], name=name))
        committed = False
        try:
            result = check()
            if result:
                confirmed = self.api(binding['path'], body=dict(action='commit', token=trial['token']))
                binding['token'] = confirmed['token']
                committed = True
            return result
        finally:
            if not committed:
                # Even a killed probe cannot leave its trial in place. Mihomo
                # also owns the rollback timer if this request cannot arrive.
                try:
                    reverted = self.api(binding['path'], 0.45, body=dict(action='rollback', token=trial['token']),
                                        cleanup=True)
                    binding['token'] = reverted['token']
                except ProbeFailure:
                    binding['token'] = ''

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
        self.diagnostics['tun_ok'] = False
        self.tun_name = self.application_dns(lambda name: self.tun_target(name, log_path))
        self.diagnostics['tun_ok'] = True

    def fake_ip(self, name):
        if name in self.fake_ips:
            return self.fake_ips[name]
        if name in self.fake_ip_failures:
            raise self.fake_ip_failures[name]
        dig = self.settings['dig']
        timeout = self.settings.get("dns_timeout_seconds", 3)
        network = ipaddress.ip_network(self.settings["fake_ip_net"], strict=False)
        cancelled = getattr(self.dns_workers, 'cancelled', None)
        for _ in range(2):
            if cancelled is not None and cancelled.is_set():
                raise ProbeFailure('interrupted', 'unused DNS candidate cancelled')
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

    def application_dns(self, check=None):
        if not self.settings.get('require_tun'):
            return
        self.diagnostics['dns_ok'] = False
        failures = []
        names = list(dict.fromkeys(self.settings.get('tun_probe_names', [self.settings['tun_probe_name']])))
        cancelled = threading.Event()

        def lookup(name):
            self.dns_workers.cancelled = cancelled
            self.fake_ip(name)

        with ThreadPoolExecutor(max_workers=min(4, len(names))) as workers:
            pending = {workers.submit(lookup, name): name for name in names}
            try:
                while pending:
                    started = self.clock()
                    done, _ = wait(pending, timeout=self.remaining(30), return_when=FIRST_COMPLETED)
                    self.timings['application_dns'] = self.timings.get('application_dns', 0) + round(
                        (self.clock() - started) * 1000)
                    if not done:
                        raise ProbeFailure('obs', 'total probe deadline exhausted')
                    for future in sorted(done, key=lambda item: names.index(pending[item])):
                        name = pending.pop(future)
                        try:
                            future.result()
                            if check is not None:
                                check(name)
                            return name
                        except ProbeFailure as error:
                            if error.layer in {'config', 'route', 'interrupted'}:
                                raise
                            failures.append(error)
            finally:
                cancelled.set()
                for future in pending:
                    future.cancel()
        raise next((error for error in failures if error.layer == 'dns'), failures[-1])

    def tun_target(self, name, log_path, *, url=None):
        diagnostic = url is not None
        url = url or f'http://{name}/'
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
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
        if not diagnostic:
            self.diagnostics['dns_ok'] = True
        # A root fake-IP TCP flow is excluded from gost's REDIRECT. It exercises
        # the core's effective TUN/auto-redirect data path as well as DNS hijack.
        # Let the kernel allocate a fresh ephemeral port. A small, repeatedly
        # scanned range reuses ports across different fake-IP candidates; their
        # redirects share one endpoint and conntrack can then rewrite a port.
        output, records = self.timed('tun_http', lambda: self.request([self.settings["curl"], "--noproxy", "*", "--head", "-sS",
                               "-o", "/dev/null", "--max-time", "2.5", "--max-filesize", "1048576",
                               "--resolve", f"{name}:{port}:{fake_ip}",
                               "-w", "%{http_code} %{local_port} %{local_ip}", url], 2.7, name, port))
        fields = output.split()
        if len(fields) != 3 or fields[0] not in {"200", "204", "301", "302", "307", "308"} \
                or not fields[1].isdigit():
            detail = getattr(self, "last_error", "") or output.strip()[:120] or "empty reply"
            raise ProbeFailure("obs", f"fake-IP TCP probe failed for {fake_ip}: {detail}")
        self.timed('domestic_route' if diagnostic else 'tun_route',
                   lambda: self.prove_route(log_path, name, fields[1], fields[2], records,
                          'domestic' if diagnostic else 'TUN',
                          'direct' if diagnostic else self.settings.get('tun_route_classes', {}).get(name, 'proxy'),
                          destination_port=port))

    def target(self, url, log_path, maximum=None):
        parsed = urlsplit(url)
        host = parsed.hostname
        if not host:
            raise ProbeFailure("config", "invalid health target URL")
        # Fresh source ports disambiguate the log from other local applications.
        if maximum is None:
            maximum = self.settings['request_timeout_seconds']
            if self.settings.get('session_urls'):
                maximum = min(maximum, max(1, (self.deadline - self.clock() - 8) / self.target_slots))
        timeout = self.remaining(min(maximum, self.remaining(30) / self.target_slots))
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
        if url == self.settings.get('delay_primary'):
            self.diagnostics['delay_primary_ok'] = True
            self.diagnostics.pop('delay_fallback', None)

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

    def ip_tunnel(self, fast):
        settings = self.settings.get('ip_tunnel')
        if not settings:
            return
        self.diagnostics['ip_tunnel_ok'] = False
        try:
            completed = subprocess.run([sys.executable, settings['module'], 'verify' if fast else 'probe'],
                                       input=json.dumps(settings), text=True, capture_output=True,
                                       timeout=self.remaining(5), check=True)
            outcome = json.loads(completed.stdout)
        except subprocess.TimeoutExpired:
            outcome = {'stage': 'deadline'}
        except (OSError, ValueError, subprocess.SubprocessError):
            outcome = {'stage': 'unavailable'}
        self.diagnostics['ip_tunnel'] = outcome
        if outcome['stage'] != ('path-verified' if fast else 'echo-verified'):
            raise ProbeFailure('tun', 'WireGuard path proof failed')
        self.diagnostics['ip_tunnel_ok'] = True

    def attempt_targets(self, log_path):
        failures = []
        primary = self.settings.get('delay_primary')
        failed_primary = False
        for number, url in enumerate(self.settings["urls"]):
            self.last_target = url
            self.target_slots = len(self.settings['urls']) - number + int(self.settings.get('require_tun', False))
            try:
                self.timed(f"target_{number}", lambda: self.target(url, log_path))
                if url == primary:
                    self.diagnostics['delay_primary_ok'] = True
                elif failed_primary and url == self.settings.get('delay_fallback'):
                    self.diagnostics['delay_fallback'] = hashlib.sha256((primary + '\n' + url).encode()).hexdigest()
                return True
            except ProbeFailure as error:
                if error.layer in {"route", "config", "interrupted"}:
                    raise
                if url == primary and failure_stage(error.detail) == 'mixed-request':
                    failed_primary = True
                    self.diagnostics['delay_primary_ok'] = False
                self.note_failure(failure_stage(error.detail))
                failures.append(error)
        raise failures[-1]

    def rotate_leaf(self, log_path, url, dns_name=None):
        """Start after the current leaf so unmeasured members get a turn across rounds."""
        current, members = self.selector('lf-remote')
        if not members:
            return False
        remote = self.selection_binding('lf-remote', current)
        urls = list(dict.fromkeys([url, *self.settings['urls']]))
        dns_leaf, dns_alternatives = self.selector('lf-dns') if dns_name and self.settings.get('dns_leaf_group') else (None, [])
        dns = self.selection_binding('lf-dns', dns_leaf) if dns_alternatives else None

        def prove_http(candidate=None):
            limit = min(self.deadline - 1, self.clock() + 2.5)
            self.target_slots = 1
            for target in urls:
                remaining = limit - self.clock()
                if remaining <= 0.5:
                    break
                try:
                    self.target(target, log_path, maximum=remaining)
                    return candidate is None or self.exit_identities.get('mixed') == hashlib.sha256(
                        candidate.encode()).hexdigest()
                except ProbeFailure as error:
                    if failure_stage(error.detail) != 'mixed-request':
                        raise
            return False

        for name in self.leaf_candidates(url):
            if self.deadline - self.clock() <= 2 or not remote['token']:
                break
            if self.sessions.get(name) in FAILED_SESSIONS:
                continue
            if dns_name:
                # Repair the independent DNS path while the application
                # selector still points at its original leaf.
                if (name not in dns_alternatives or not dns or not dns['token']
                        or self.sessions.get(dns_leaf) in FAILED_SESSIONS and self.sessions.get(name) != 'ok'
                        or not self.try_leaf(dns, name, lambda: self.dns_answer(dns_name))):
                    continue
                dns_name = None
                self.dns_transport_leaf = name
                self.dns_leaf(name)
                self.diagnostics['dns_leaf_rotated'] = True
                self.diagnostics['managed_dns_ok'] = True
                self.fake_ip_failures.clear()
                # DNS can restore the original application's full route.
                if prove_http():
                    return True
            if self.sessions.get(current) == 'ok':
                if self.deadline - self.clock() < 3.5:
                    break
                path = '/proxies/' + quote(name, safe='') + '/health-delay?'
                if not self.api(path + urlencode({'url': url}), 1.5).get('ok'):
                    continue
            if self.try_leaf(remote, name, lambda: prove_http(name)):
                return True
        return False

    def dns_answer(self, name):
        if not self.settings.get('dns_leaf_group'):
            answer = self.command([self.settings['dig'], '+time=2', '+tries=1', '+short', '@127.0.0.1',
                                   '-p', str(self.settings['dns_port']), name, 'A'], 2.2)
            for line in answer.splitlines():
                try:
                    if ipaddress.ip_address(line).version == 4:
                        return True
                except ValueError:
                    pass
            return False
        # The controller resolver returns real records without the fake-IP middleware.
        try:
            response = self.api('/dns/query?' + urlencode(dict(name=name, type='A')), 2.2)
        except ProbeFailure as error:
            if error.layer != 'obs':
                raise
            return False
        if response.get('Status') != 0:
            return False
        fake = ipaddress.ip_network(self.settings.get('fake_ip_net', '198.18.0.0/15'), strict=False)
        for record in response.get('Answer') or []:
            try:
                address = ipaddress.ip_address(record['data'])
                if record.get('type') == 1 and address.version == 4 and address not in fake:
                    return True
            except (KeyError, TypeError, ValueError):
                continue
        return False

    def sample_sessions(self, names):
        targets = self.settings.get('session_urls', [])
        pending = [name for name in names if name not in self.sessions]
        if not targets or not pending or self.deadline - self.clock() < 2:
            return

        def sample(name):
            outcome = None
            for target in targets:
                if self.deadline - self.clock() < 2:
                    break
                path = '/proxies/' + quote(name, safe='') + '/session?' + urlencode(dict(url=target))
                try:
                    result = self.api(path, 1.5).get('session')
                except ProbeFailure as error:
                    if error.layer != 'obs':
                        raise
                    continue
                if result in ('ok', 'node-auth'):
                    return result
                if result == 'session-unavailable':
                    outcome = result
            return outcome

        with ThreadPoolExecutor(max_workers=4) as workers:
            self.sessions.update((name, value) for name, value in zip(pending, workers.map(sample, pending)) if value)
        measured = list(self.sessions.values())
        self.diagnostics['leaf_sessions'] = {result: measured.count(result)
                                            for result in ('ok', 'node-auth', 'session-unavailable')}

    def sample_dns_batch(self, current, leaves):
        """Advance past previously sampled alternatives within the same routing transaction."""
        if not (self.snapshot and self.routing and self.settings.get('session_urls')):
            self.sample_sessions([current, *leaves[:3]])
            return leaves[:3]
        runtime = Path(self.settings['runtime'])
        path = runtime / 'dns-leaf-cursor.json'
        binding = dict(self.routing, boot_id=(runtime / 'boot-id').read_text().strip(), digest=self.snapshot)
        ordered = [current, *leaves]
        identities = [hashlib.sha256(name.encode()).hexdigest() for name in ordered]
        try:
            record = json.loads(path.read_text())
            offset = identities.index(record['last']) + 1 if record['binding'] == binding else 0
        except (OSError, ValueError, KeyError, TypeError):
            offset = 0
        alternatives = [name for name in ordered[offset:] + ordered[:offset] if name != current][:3]
        self.sample_sessions([current, *alternatives])
        measured = [leaf for leaf in alternatives if leaf in self.sessions]
        if measured:
            record = dict(binding=binding, last=hashlib.sha256(measured[-1].encode()).hexdigest())
            temporary = path.with_suffix('.new')
            try:
                temporary.write_text(json.dumps(record))
                temporary.replace(path)
            except OSError:
                pass
        return alternatives

    def recover_dns(self, name):
        self.diagnostics['managed_dns_ok'] = False
        current, leaves = self.selector('lf-dns') if self.settings.get('dns_leaf_group') else (None, [])
        if current:
            self.dns_leaf(current)
        # A negative result for one site's name does not identify a broken DNS
        # transport. Use at most one other configured remote health target.
        other = next((urlsplit(url).hostname for url in self.settings['urls']
                      if urlsplit(url).hostname not in (None, name)), None)
        def resolved():
            if self.dns_answer(name):
                return name
            if other and self.deadline - self.clock() >= 4 and self.dns_answer(other):
                return other
            return None

        ready = resolved()
        if not ready:
            self.note_failure('validator-dns')
        if not ready and current and self.deadline - self.clock() >= 4:
            binding = self.selection_binding('lf-dns', current)
            alternatives = self.sample_dns_batch(current, leaves)
            self.dns_leaf(current)
            alternatives.sort(key=lambda leaf: self.sessions.get(leaf) != 'ok')
            for leaf in alternatives:
                if self.deadline - self.clock() < 3 or not binding['token']:
                    break
                outcome = self.sessions.get(leaf)
                if outcome in FAILED_SESSIONS or (self.sessions.get(current) == 'ok' and outcome != 'ok'):
                    continue
                def check():
                    answer = resolved()
                    if answer:
                        return answer
                    # Cold recovery may already have cached the negative
                    # answer from the dead leaf. In C only, keep a leaf
                    # authenticated against the configured DoH endpoint while
                    # the validator warms up. It still cannot prove B.
                    if outcome == 'ok' and self.routing.get('routing_requested') == 'rules':
                        try:
                            if (Path(self.settings['runtime']) / 'status').read_text().strip() == 'blocked':
                                return True
                        except OSError:
                            pass
                    return None

                result = self.try_leaf(binding, leaf, check)
                if result:
                    current = leaf
                    self.dns_leaf(leaf)
                    self.diagnostics['dns_leaf_rotated'] = True
                    if isinstance(result, str):
                        ready = result
                    else:
                        self.dns_transport_leaf = leaf
                    break
        if not ready:
            raise ProbeFailure('dns', 'managed validator did not resolve the probe name')
        self.dns_transport_leaf = current
        self.diagnostics['managed_dns_ok'] = True
        self.fake_ip_failures.clear()
        return ready

    def application_path(self):
        answered = self.system_dns_probe(self.settings.get('application_dns_check'))
        if answered is None:
            return
        self.diagnostics['application_dns_ok'] = answered
        if not answered:
            raise ProbeFailure('application', 'system resolver did not answer the probe name')

    def system_dns_probe(self, argv):
        status = Path(self.settings['runtime']) / 'status'
        if not argv or self.deadline - self.clock() < 3.5:
            return
        try:
            if status.read_text().strip() != 'proxy':
                return
            try:
                result = subprocess.run(argv, capture_output=True, timeout=self.remaining(3.5), check=False)
                answered = result.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                answered = False
            if status.read_text().strip() != 'proxy':
                return
        except OSError:
            return
        return answered

    def rules_outage(self, error):
        """Identify a failed foreign path under the current Rules authority."""
        if (not self.settings.get('authorization_module') or self.routing.get('routing_requested') != 'rules'
                or self.core_mode != 'rule' or error.layer not in ('obs', 'dns', 'node', 'application')
                or 'mixed-request' not in self.failure_stages or self.diagnostics['route_ok'] is True):
            return False
        # Observation loss, listener mismatch and ambiguous routing proof do
        # not qualify for the specific remote-outage decision.
        if failure_stage(error.detail) not in ('mixed-request', 'leaf-unavailable', 'node-auth',
                'session-unavailable', 'target-unavailable', 'validator-dns', 'application-dns'):
            return False
        try:
            spec = importlib.util.spec_from_file_location('proxy_authorization', self.settings['authorization_module'])
            auth = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(auth)
            return auth.rules_lease_active(Path(self.settings['runtime']).parent)
        except (OSError, ValueError, TypeError, KeyError):
            return False

    def domestic_path(self, log_path):
        url = self.settings.get('domestic_probe_url')
        if not url:
            return
        self.diagnostics['domestic_dns_ok'] = self.system_dns_probe(self.settings.get('domestic_dns_check'))
        if self.deadline - self.clock() < 3.5:
            return
        try:
            self.tun_target(urlsplit(url).hostname, log_path, url=url)
            self.diagnostics['domestic_route_ok'] = True
        except ProbeFailure as error:
            if error.layer == 'interrupted':
                raise
            self.diagnostics['domestic_route_ok'] = False

    def data_paths(self, log_path):
        recovered = False
        try:
            self.attempt_targets(log_path)
        except ProbeFailure as error:
            self.note_failure(failure_stage(error.detail))
            url = getattr(self, 'last_target', self.settings['urls'][0])
            name = urlsplit(url).hostname
            if failure_stage(error.detail) != 'mixed-request' or not name or self.deadline - self.clock() < 2:
                raise
            recovered = True
            dns_error = None
            resolved_name = None
            try:
                resolved_name = self.timed('validator_dns', lambda: self.recover_dns(name))
            except ProbeFailure as failure:
                if failure.layer != 'dns':
                    raise
                self.note_failure(failure_stage(failure.detail))
                dns_error = failure
            # A DNS recovery can make the current leaf usable again.
            if resolved_name and resolved_name != name:
                url = next(target for target in self.settings['urls'] if urlsplit(target).hostname == resolved_name)
            if not dns_error and (self.diagnostics.get('dns_leaf_rotated') or resolved_name and resolved_name != name):
                try:
                    self.target_slots = 1
                    self.target(url, log_path, maximum=2.5)
                except ProbeFailure as retry_error:
                    if failure_stage(retry_error.detail) != 'mixed-request':
                        raise
                else:
                    error = None
            if error and not self.timed('leaf_recovery', lambda: self.rotate_leaf(log_path, url, name if dns_error else None)):
                observed = set(self.sessions.values())
                if observed == {'node-auth'}:
                    raise ProbeFailure('node', 'sampled leaves rejected authentication') from None
                if observed and 'ok' not in observed:
                    raise ProbeFailure('node', 'sampled leaves could not establish proxy sessions') from None
                if dns_error:
                    raise dns_error from None
                self.timed('application_path', self.application_path)
                if 'ok' in observed:
                    raise ProbeFailure('obs', 'sampled sessions passed but HTTP targets did not respond') from None
                raise ProbeFailure('node', 'authorized leaves did not answer the mixed request') from None
        def transparent_paths():
            self.timed('tun', lambda: self.tun_test(log_path))
            self.timed('real_dns', self.real_dns)
        try:
            transparent_paths()
        except ProbeFailure as error:
            if error.layer not in ('dns', 'obs'):
                raise
            name = self.tun_name or next(iter(self.fake_ip_failures), self.settings.get('tun_probe_name'))
            if error.layer == 'dns' and name and self.settings.get('dns_leaf_group') \
                    and self.deadline - self.clock() >= 4:
                self.timed('validator_dns', lambda: self.recover_dns(name))
                transparent_paths()
                recovered = True
            else:
                self.timed('application_path', self.application_path)
                raise
        if recovered:
            try:
                self.timed('application_path', self.application_path)
            except ProbeFailure as error:
                if error.layer != 'application':
                    raise

    def delay_diagnosis(self):
        if self.diagnostics.get('delay_primary_ok') is not False or self.deadline - self.clock() < 4:
            return
        try:
            host = urlsplit(self.settings['delay_primary']).hostname
            self.diagnostics['delay_primary_dns_ok'] = self.dns_answer(host)
        except ProbeFailure as error:
            if error.layer == 'interrupted':
                raise

    def run(self, fast=False):
        self.routing = self.routing_binding()
        if not fast and self.settings.get('digest_file'):
            try:
                self.snapshot = Path(self.settings['digest_file']).read_text().strip()
            except FileNotFoundError:
                pass
        def listeners():
            try:
                result = subprocess.run([self.settings['listener_check'], 'core', 'dns'],
                                        capture_output=True, timeout=self.remaining(4.5), check=False)
            except (OSError, subprocess.SubprocessError):
                raise ProbeFailure('obs', 'listener observation unavailable') from None
            if result.returncode == 1:
                raise ProbeFailure('config', 'managed TCP/UDP listener identity mismatch') from None
            if result.returncode:
                raise ProbeFailure('obs', 'listener observation unavailable')
        self.diagnostics['listener_ok'] = False
        self.timed('listeners', listeners)
        self.diagnostics['listener_ok'] = True
        self.diagnostics['config_ok'] = False
        initial = self.initial_config(fast)
        self.core_mode = initial.get('mode', 'unknown')
        fingerprint = self.timed("config", lambda: check_config(initial, self.settings))
        self.diagnostics['config_ok'] = True
        if not fast and self.routing.get('egress_requested'):
            graph = self.api('/proxies')['proxies']
            selected = graph['lf-remote']['now']
            self.diagnostics['node_udp_supported'] = graph[selected].get('udp') is True
        if fast:
            self.timed('ip_tunnel', lambda: self.ip_tunnel(fast=True))
            return fingerprint
        self.timed('ip_tunnel', lambda: self.ip_tunnel(fast=False))
        with tempfile.TemporaryDirectory(prefix="health-", dir=self.settings["runtime"]) as temporary:
            log_path = Path(temporary) / "logs"
            with log_path.open("w") as stream:
                reader = subprocess.Popen([self.settings["curl"], "--noproxy", "*", "-NsS",
                                           "--max-time", str(self.remaining(30)), "--unix-socket",
                                           self.settings["socket"], "http://localhost/logs?level=info"],
                                          stdout=stream, stderr=subprocess.DEVNULL)
            try:
                time.sleep(min(0.15, self.remaining(0.15)))
                outage = None
                try:
                    self.data_paths(log_path)
                except ProbeFailure as error:
                    if not self.rules_outage(error):
                        raise
                    outage = error
                if self.settings.get('dns_leaf_group') and 'dns_leaf' not in self.diagnostics and self.deadline - self.clock() > 1.5:
                    try:
                        leaf, alternatives = self.selector('lf-dns')
                    except ProbeFailure as error:
                        if error.layer not in ('obs', 'controller-unavailable'):
                            raise
                    else:
                        if leaf:
                            if (Path(self.settings['runtime']) / 'profile-refresh-verifying.json').exists() \
                                    and self.deadline - self.clock() > 4:
                                self.timed('session_sample', lambda: self.sample_sessions([leaf, *alternatives[:2]]))
                            self.dns_leaf(leaf)
                self.diagnostics['config_ok'] = False
                final = check_config(self.api("/configs"), self.settings)
                if final != fingerprint:
                    raise ProbeFailure("config", "configuration changed during the probe")
                if self.routing_binding() != self.routing:
                    raise ProbeFailure('config', 'routing changed during the probe')
                self.diagnostics['config_ok'] = True
                if outage is not None:
                    self.note_failure(failure_stage(outage.detail))
                    self.domestic_path(log_path)
                    raise ProbeFailure('rules-degraded', 'remote path unavailable during an active Rules lease')
                self.delay_diagnosis()
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
            + (['real-address-dns'] if settings.get('real_dns_port') else [])
            + (['icmp4-echo', 'icmp6-echo'] if settings.get('ip_tunnel') else []),
        "udp_data_plane": "not-probed",
    }}
    try:
        result.update(ok=True, reason="", fingerprint=probe.run(fast=fast))
    except ProbeFailure as error:
        result.update(reason=error.layer, detail=error.detail, failure_stage=failure_stage(error.detail))
        probe.note_failure(result['failure_stage'])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        result.update(detail=f"probe error: {type(error).__name__}")
    result.update(probe.diagnostics | probe.routing, core_mode=probe.core_mode,
                  exit_identities=probe.exit_identities, route_sources=probe.route_sources,
                  duration_ms=round((time.monotonic() - probe.start) * 1000), layers=probe.timings)
    if not result['ok']:
        result['failure_stages'] = probe.failure_stages
        result['startup_data_failure'] = result['reason'] in ('obs', 'dns', 'node', 'controller-unavailable') and all(
            result.get(key) is True for key in ('listener_ok', 'config_ok')) and (
                result['reason'] == 'node' or result.get('data_plane_ok') is False
                and 'validator-dns' in probe.failure_stages)
    if os.geteuid() == 0 and not fast and result.get('reason') != 'interrupted':
        classification = result.get('reason')
        if classification not in ('node', 'application', 'tun', 'dns', 'config', 'route', 'obs', 'rules-degraded',
                                  'controller-unavailable', 'direct-mode', 'no-tun', 'core-gone'):
            classification = 'obs'
        if not result['ok']:
            print('proxy health warning: ' + classification, file=sys.stderr)
        record = {key: result[key] for key in ('ok', 'reason', 'duration_ms', 'layers')}
        record.update(probe.diagnostics, route_sources=probe.route_sources)
        record.update(probe.routing, core_mode=probe.core_mode)
        record['failure_stage'] = result.get('failure_stage', 'unknown')
        if not result['ok']:
            record['failure_stages'] = result['failure_stages']
        record['at'] = time.time()
        path = None
        try:
            runtime = Path(settings['runtime'])
            record['boot_id'] = (runtime / 'boot-id').read_text().strip()
            record['digest'] = probe.snapshot
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
