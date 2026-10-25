"""Security regressions: real policy functions, isolated tools, no live network."""

import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import socket
import threading
import time
import tempfile
import unittest
from unittest.mock import patch


MODULES = Path(os.environ.get("NETSEC_MODULES", Path(__file__).resolve().parents[2]
                              / "system/programs/systemd"))


def module(name):
    spec = importlib.util.spec_from_file_location(name, MODULES / "netsec" / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


health, link, rules, nm_dns = (module(name) for name in ("health", "link", "rules", "nm_dns"))
dns_policy, resolved_policy, state, alerts = (module(name) for name in ("dns_policy", "resolved_policy", "state", "alerts"))
SOURCE = (MODULES / "proxy-mode.nix").read_text()


def shell_function(name):
    match = re.search(r"^    " + name + r"\(\) \{\n.*?^    \}", SOURCE, re.M | re.S)
    if not match:
        raise AssertionError(f"missing shell function {name}")
    return match.group(0).replace("''${", "${")


class HealthTests(unittest.TestCase):
    def test_managed_policy_and_route_scope_drift(self):
        settings = {"expected": {"managed-policy": "verified", "tun": {"include-uid": [], "route-address": []}}}
        config = {"mode": "rule", "managed-policy": "verified", "tun": {}}
        health.check_config(config, settings)
        for value in [dict(config, **{"managed-policy": ""}),
                      dict(config, tun={"include-uid": [1000]}),
                      dict(config, tun={"route-address": ["198.18.0.0/30"]})]:
            with self.assertRaises(health.ProbeFailure): health.check_config(value, settings)

    def test_tun_dns_alone_is_not_a_success(self):
        probe = health.Probe({"budget_seconds": 12, "require_tun": True, "dig": "dig", "curl": "curl",
                              "tun_probe_name": "probe.test", "dns_port": 1053, "fake_ip_net": "198.18.0.0/16"})
        commands = []
        def command(argv, _maximum):
            commands.append(argv)
            return "198.18.1.2\n" if argv[0] == "dig" else "000 15110"
        probe.command = command
        with self.assertRaises(health.ProbeFailure): probe.tun_test()
        self.assertTrue(any("--resolve" in argv for argv in commands))

    def test_controller_reads_have_a_total_deadline(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / "controller.sock")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path); server.listen()
            def slow():
                connection, _ = server.accept()
                try:
                    connection.recv(4096)
                    connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 10000\r\n\r\n{')
                    time.sleep(0.3)
                finally:
                    connection.close(); server.close()
            worker = threading.Thread(target=slow); worker.start()
            probe = health.Probe({"budget_seconds": 0.08, "socket": path})
            started = time.monotonic()
            with self.assertRaises(health.ProbeFailure): probe.api("/configs")
            self.assertLess(time.monotonic() - started, 0.25)
            worker.join()

    def test_exact_log_source_and_host(self):
        line = lambda port, host: json.dumps({"payload": f"[TCP] 127.0.0.1:{port} --> {host}:80 match Rule using group[node]"})
        content = "\n".join([line(15011, "www.gstatic.com"), line(15010, "www.gstatic.com.evil")])
        self.assertIsNone(health.route_line(content, "www.gstatic.com", 15010))
        self.assertEqual(health.route_line(content + "\n" + line(15010, "www.gstatic.com"),
                                           "www.gstatic.com", 15010), "group[node]")

    def test_nested_and_unusual_policy_names(self):
        graph = {"DIRECT-中转[fast]": {"type": "Selector", "now": "nested/name"},
                 "nested/name": {"type": "Selector", "now": "node"}, "node": {"type": "Shadowsocks"}}
        self.assertEqual(health.effective_policy("DIRECT-中转[fast][node]", graph),
                         ("DIRECT-中转[fast]", "node"))
        graph["nested/name"]["now"] = "DIRECT"
        graph["DIRECT"] = {"type": "Direct"}
        with self.assertRaises(health.ProbeFailure) as error:
            health.effective_policy("DIRECT-中转[fast][DIRECT]", graph)
        self.assertEqual(error.exception.layer, "route")

    def test_cyclic_and_unselected_groups_fail_closed(self):
        for graph in ({"a": {"now": "a"}}, {"a": {"all": ["node"]}}):
            with self.assertRaises(health.ProbeFailure):
                health.effective_policy("a[node]", graph)

    def test_configuration_drift_and_fingerprint(self):
        config = {"mode": "rule", "routing-mark": 6666, "mixed-port": 7897,
                  "tun": {"enable": True, "stack": "Mixed"}}
        settings = {"expected": {"routing-mark": 6666, "mixed-port": 7897,
                                 "tun": {"enable": True, "stack": "mixed"}}}
        first = health.check_config(config, settings)
        config["mode"] = "global"
        self.assertNotEqual(first, health.check_config(config, settings))
        config["routing-mark"] = 0
        with self.assertRaises(health.ProbeFailure) as error:
            health.check_config(config, settings)
        self.assertEqual(error.exception.layer, "config")

    def test_deadline_shared_by_all_commands(self):
        clock = [100.0]
        probe = health.Probe({"budget_seconds": 12}, clock=lambda: clock[0])
        self.assertEqual(probe.remaining(30), 12)
        clock[0] = 111
        self.assertEqual(probe.remaining(30), 1)
        clock[0] = 112
        with self.assertRaises(health.ProbeFailure):
            probe.remaining(1)

    def test_complete_fallback_and_direct_refusal(self):
        class Reader:
            def terminate(self): pass
            def wait(self, timeout=None): return 0

        with tempfile.TemporaryDirectory() as directory:
            settings = {"budget_seconds": 12, "runtime": directory, "curl": "unused",
                        "socket": "unused", "urls": ["http://first", "http://second"], "expected": {}}
            probe = health.Probe(settings)
            probe.api = lambda path: {"mode": "rule"}
            probe.tun_test = lambda *_args: None
            seen = []

            def target(url, _path):
                seen.append(url)
                if url.endswith("first"):
                    raise health.ProbeFailure("obs", "target outage")

            probe.target = target
            with patch.object(health.subprocess, "Popen", return_value=Reader()), patch.object(health.time, "sleep"):
                probe.run()
            self.assertEqual(seen, settings["urls"])
            seen.clear()

            def direct(url, _path):
                seen.append(url)
                raise health.ProbeFailure("route", "DIRECT")

            probe.target = direct
            with patch.object(health.subprocess, "Popen", return_value=Reader()), patch.object(health.time, "sleep"):
                with self.assertRaises(health.ProbeFailure):
                    probe.run()
            self.assertEqual(seen, ["http://first"])


class LinkTests(unittest.TestCase):
    def test_raw_lease_parser(self):
        self.assertEqual(link.resolvers("domain_name_servers = 192.0.2.53 127.0.0.1\nlease_time = 10"), ["192.0.2.53"])
        self.assertEqual(link.resolvers("domain_name_servers='192.0.2.53,192.0.2.54'"), ["192.0.2.53", "192.0.2.54"])

    def test_dhcp_changes_keep_budget_but_reconnect_does_not(self):
        settings = {"ip": "ip", "nmcli": "nmcli", "dhcpcd": "dhcpcd", "dhcpcd_config": "unused",
                    "interfaces": ["ens1"], "dhcp_interfaces": ["ens1"]}
        server, downs = ["192.0.2.53"], ["0"]

        def command(argv):
            if "route" in argv:
                return '[{"dev":"ens1","gateway":"100.67.0.1"}]'
            if "addr" in argv:
                return '[{"addr_info":[{"local":"100.67.1.2","scope":"global"}]}]'
            return "domain_name_servers='" + server[0] + "'"

        def value(path):
            return downs[0] if str(path).endswith("carrier_down_count") else "stable"

        def read():
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                link.main(settings)
            return json.loads(output.getvalue())

        with patch.object(link, "command", side_effect=command), patch.object(link, "kernel_value", side_effect=value):
            first = read()
            server[0] = "192.0.2.54"
            changed = read()
            self.assertEqual(first["identity"], changed["identity"])
            self.assertNotEqual(first["fingerprint"], changed["fingerprint"])
            downs[0] = "1"
            self.assertNotEqual(first["identity"], read()["identity"])

    def test_wifi_uses_original_dhcp_options(self):
        settings = {"ip": "ip", "nmcli": "nmcli", "interfaces": ["wlo1"], "dhcp_interfaces": []}
        calls = []

        def command(argv):
            calls.append(argv)
            if "route" in argv: return '[{"dev":"wlo1"}]'
            if "addr" in argv: return '[]'
            if "DHCP4.OPTION" in argv: return "domain_name_servers = 192.0.2.53"
            return "/org/freedesktop/NetworkManager/ActiveConnection/1"

        with patch.object(link, "command", side_effect=command), contextlib.redirect_stdout(io.StringIO()):
            link.main(settings)
        self.assertTrue(any("DHCP4.OPTION" in argv for argv in calls))
        self.assertFalse(any("IP4.DNS" in argv for argv in calls))


class NetworkManagerTests(unittest.TestCase):
    def test_profile_and_active_link_are_both_constrained(self):
        calls = []

        def run(argv, **_kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "no\nno\n192.0.2.53\n\ncorp\n", "")

        settings = {"interface": "wlo1", "exceptions": [], "nmcli": "nmcli",
                    "resolvectl": "resolvectl", "resolver": "127.0.0.1:1054"}
        nm_dns.apply(settings, "wlo1", "up", "test-uuid", run=run)
        modify = next(argv for argv in calls if "modify" in argv)
        self.assertIn("ipv4.ignore-auto-dns", modify)
        self.assertIn("ipv6.ignore-auto-dns", modify)
        self.assertTrue(any("reapply" in argv for argv in calls))
        self.assertIn(["resolvectl", "dns", "wlo1", "127.0.0.1:1054"], calls)
        calls.clear()
        settings["exceptions"] = ["test-uuid"]
        nm_dns.apply(settings, "wlo1", "up", "test-uuid", run=run)
        self.assertFalse(any("modify" in argv for argv in calls))
        self.assertIn(["resolvectl", "dns", "wlo1", "127.0.0.1:1054"], calls)


class DNSPolicyTests(unittest.TestCase):
    def test_expiry_generation_and_mode_gate(self):
        request = {"policy_version": 10, "monotonic_seconds": 100,
                   "plaintext_until": 110, "plaintext": ["192.0.2.53"], "split": []}
        direct = {"mode": "direct", "version": 10}
        value = dns_policy.transaction(request, direct, 101)
        self.assertIn("192.0.2.53 timeout 9s", value)
        self.assertNotIn("add element", dns_policy.transaction(request, direct, 111))
        for policy in [{"mode": "proxy", "version": 10}, {"mode": "blocked", "version": 10},
                       {"mode": "direct", "version": 11}]:
            self.assertNotIn("add element", dns_policy.transaction(request, policy, 101))

    def test_split_dns_is_explicit_scoped_and_sanitized(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            output = "approved" if "GENERAL.CON-UUID" in argv else "10.1.2.53\n\n~corp.example,~.\n"
            return subprocess.CompletedProcess(argv, 0, output, "")
        settings = {"nmcli": "nmcli", "wireless": "wlo1", "split_profiles": ["approved"]}
        entries = dns_policy.split_dns(settings, run=run)
        self.assertEqual(entries, [{"domain": "corp.example", "server": "10.1.2.53"}])
        request = {"policy_version": 10, "monotonic_seconds": 100, "split": entries}
        self.assertIn("dns_split4 { 10.1.2.53 timeout 120s }",
                      dns_policy.transaction(request, {"mode": "proxy", "version": 10}, 101))
        with self.assertRaises(ValueError): dns_policy.addresses(["10.1.2.53; flush ruleset"])
        self.assertEqual(dns_policy.domains(["~.", "bad/domain", "~corp.example"]), ["corp.example"])
        settings["split_profiles"] = []
        self.assertEqual(dns_policy.split_dns(settings, run=run), [])

    def test_resolved_reconcile_only_managed_tun(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            output = "Link 6 (Mihomo): 198.18.0.2" if "dns" in argv else "Link 6 (Mihomo): ~."
            return subprocess.CompletedProcess(argv, 0, output, "")
        settings = {"ip": "ip", "resolvectl": "resolvectl", "tun": "Mihomo"}
        with self.assertRaises(ValueError): resolved_policy.reconcile(settings, check=True, run=run)
        resolved_policy.reconcile(settings, run=run)
        self.assertIn(("resolvectl", "revert", "Mihomo"), calls)
        self.assertTrue(all("tailscale0" not in argv for argv in calls))


class StateTests(unittest.TestCase):
    def test_freshness_and_both_modes_require_current_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def write(path, value):
                target = root / path; target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(value))
            for mode, dns_mode in [("proxy", "proxy"), ("direct", "dot")]:
                write("proxy-mode/state.json", {"mode": mode, "policy_version": 10})
                fresh = {"mode": mode, "policy_version": 10, "monotonic_seconds": 100}
                write("proxy-mode/heartbeat.json", fresh)
                write("gost-relay/state.json", fresh)
                write("dns-upstream/state.json", dict(fresh, mode=dns_mode))
                write("netsec/state.json", {"result": "ok", "version": 10})
                self.assertTrue(state.readiness(root, mode, now=101)["ready"])
                self.assertFalse(state.readiness(root, mode, now=221)["ready"])
                write("netsec/state.json", {"result": "ok", "version": 9})
                self.assertFalse(state.readiness(root, mode, now=101)["ready"])

    def test_alert_recovery_does_not_clear_another_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            alerts.update(root, "dns-policy.service", "failed")
            alerts.update(root, "nftables-verify", "mismatch")
            alerts.update(root, "nftables-verify")
            self.assertTrue((root / "failed").exists())
            self.assertEqual((root / "failed").read_text().strip(), "dns-policy.service")
            alerts.update(root, "dns-policy.service")
            self.assertFalse((root / "failed").exists())


def policy_document():
    return {"nftables": [
        {"table": {"family": "inet", "name": "lf_filter"}},
        {"chain": {"family": "inet", "table": "lf_filter", "name": "input", "hook": "input", "prio": 0, "policy": "drop"}},
        {"chain": {"family": "inet", "table": "lf_filter", "name": "lf_blocked_guard", "hook": "output", "prio": -200}},
        {"rule": {"family": "inet", "table": "lf_filter", "chain": "input", "expr": [{"accept": None}]}},
        {"rule": {"family": "inet", "table": "lf_filter", "chain": "input", "expr": [{"drop": None}]}},
    ]}


class RulesTests(unittest.TestCase):
    def test_only_bounded_dns_lease_elements_are_ignored(self):
        expected, actual = policy_document(), policy_document()
        definition = {"family": "inet", "table": "lf_filter", "name": "dns_plaintext4",
                      "type": "ipv4_addr", "flags": ["timeout"], "timeout": 120000}
        expected["nftables"].append({"set": definition})
        live = copy.deepcopy(definition)
        live["elem"] = [{"elem": {"val": "192.0.2.53", "timeout": 2000, "expires": 1000}}]
        actual["nftables"].append({"set": live})
        rules.compare(expected, actual)
        live["elem"][0]["elem"]["timeout"] = 121000
        with self.assertRaises(ValueError): rules.compare(expected, actual)
        live["elem"][0]["elem"]["timeout"] = 2000
        live["policy"] = "accept"
        with self.assertRaises(ValueError): rules.compare(expected, actual)

    def test_static_comparison_keeps_hook_policy_and_order(self):
        expected = policy_document()
        for mutate in (lambda d: d["nftables"][1]["chain"].update(policy="accept"),
                       lambda d: d["nftables"][2]["chain"].update(prio=0),
                       lambda d: d["nftables"].reverse()):
            actual = copy.deepcopy(expected)
            mutate(actual)
            with self.assertRaises(ValueError):
                rules.compare(expected, actual, static=True)

    def test_only_dynamic_rule_bodies_are_ignored(self):
        expected, actual = policy_document(), policy_document()
        actual["nftables"].append({"rule": {"family": "inet", "table": "lf_filter", "chain": "lf_blocked_guard", "expr": [{"drop": None}]}})
        rules.compare(expected, actual, static=True)
        with self.assertRaises(ValueError): rules.compare(expected, actual)
        actual["nftables"].append({"table": {"family": "inet", "name": "lf_bootstrap"}})
        with self.assertRaises(ValueError): rules.compare(expected, actual, static=True)


class SupervisorTests(unittest.TestCase):
    def run_shell(self, script, directory):
        result = subprocess.run(["bash", "-c", script], cwd=directory, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result.stdout

    def setup_shell(self, directory):
        for name, value in {"status": "proxy", "reason": "", "version": "10", "rules": "rules"}.items():
            (directory / name).write_text(value + "\n")
        return f'''
STATUS={str(directory / 'status')!r}; REASON={str(directory / 'reason')!r}; VERSION={str(directory / 'version')!r}
MARKER={str(directory / 'marker')!r}; RULES={str(directory / 'rules')!r}
CAT=cat; IS_CLASH_ON=true; REQUIRE_TUN=1; static_ok=1; installed=proxy
obs_fails=0; fails=0; changed=0; woken=0; verifies=0; applies=0; full_repairs=0; snapshots=0
last_full_repair=-30
lock_state() {{ :; }}; unlock_state() {{ :; }}
read_intent() {{ echo proxy; }}; read_version() {{ cat "$VERSION"; }}
core_stamp() {{ echo 123; }}; rules_present() {{ return 0; }}
tun_ready() {{ return 0; }}; mode_direct() {{ return 1; }}
forget_snapshot() {{ snapshots=$((snapshots+1)); }}; loud() {{ :; }}; log() {{ :; }}
request_verify() {{ verifies=$((verifies+1)); }}; request_wake() {{ :; }}
safety_mode() {{ echo blocked; }}
static_matches() {{ [ "$static_ok" = 1 ]; }}
matches() {{ [ "$static_ok" = 1 ] && [ "$installed" = "$1" ]; }}
load_complete() {{ full_repairs=$((full_repairs+1)); static_ok=1; installed="$1"; }}
publish_state() {{
 if [ "${{1:-policy}}" = policy ]; then echo "$(( $(read_version) + 1 ))" > "$VERSION"; request_verify; fi
 echo "$mode" > "$STATUS"; echo "$reason" > "$REASON"
}}
rules_on_proxy() {{ applies=$((applies+1)); installed=proxy; }}
rules_on_blocked() {{ installed=blocked; }}
health_ok() {{ return 0; }}
'''

    def test_reason_only_changes_do_not_invalidate_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = self.setup_shell(directory) + shell_function("sync_pass") + '''
health_ok() { health_gate=obs; return 1; }
sync_pass
health_ok() { return 0; }
sync_pass
[ "$(read_version)" = 10 ] && [ "$verifies" = 0 ] && [ "$reason" = "" ] && [ "$snapshots" -ge 4 ]
'''
            self.run_shell(script, directory)

    def test_static_drift_is_fully_repaired(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = self.setup_shell(directory) + shell_function("sync_pass") + '''
static_ok=0
sync_pass
[ "$full_repairs" = 1 ] && [ "$mode" = proxy ] && [ "$pass_verified" = 1 ] && [ "$applies" = 1 ]
'''
            self.run_shell(script, directory)

    def test_failed_full_repair_cannot_claim_proxy(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = self.setup_shell(directory) + shell_function("sync_pass") + '''
static_ok=0
load_complete() { return 1; }
if sync_pass; then exit 1; fi
[ "$mode" = unenforced ] && [ "$verifies" = 1 ] && [ "$applies" = 0 ]
'''
            self.run_shell(script, directory)

    def test_new_snapshot_window_observes_external_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "proxy.json").write_text("valid\n")
            (directory / "live").write_text("valid\n")
            nft = directory / "nft"
            nft.write_text('#!/bin/sh\necho read >> "$READS"\ncat "$LIVE"\n')
            nft.chmod(0o700)
            check = directory / "check"
            check.write_text('#!/bin/sh\ncmp -s "$2" "$3"\n')
            check.chmod(0o700)
            helpers = "\n".join(line.strip() for line in SOURCE.splitlines()
                                if line.strip().startswith(("take_snapshot()", "forget_snapshot()")))
            script = f'''export READS={str(directory / 'reads')!r} LIVE={str(directory / 'live')!r}
NFT={str(nft)!r}; RULE_CHECK={str(check)!r}; EXPECTED={str(directory)!r}; SNAPSHOT={str(directory / 'snapshot')!r}
snapshot_ok=0
''' + helpers + "\n" + shell_function("matches") + '''
matches proxy || exit 1
echo drifted > "$LIVE"
forget_snapshot
if matches proxy; then exit 1; fi
[ "$(wc -l < "$READS")" = 2 ]
'''
            self.run_shell(script, directory)

    def test_acknowledging_old_request_does_not_erase_new_request(self):
        verifier = (MODULES / "nftables-verify.nix").read_text()
        ack = next(line.strip() for line in verifier.splitlines() if line.strip().startswith("ack_requests()"))
        ack = ack.replace("''${", "${")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script = f'''touch {str(directory / 'old')!r}
requests=({str(directory / 'old')!r})
touch {str(directory / 'new')!r}
''' + ack + f'''\nack_requests
test ! -e {str(directory / 'old')!r}
test -e {str(directory / 'new')!r}
'''
            self.run_shell(script, directory)

    def test_publisher_separates_policy_version_and_observation_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = shell_function("publish_state").replace("/run/proxy-mode", str(directory))
            source = source.replace("${pkgs.coreutils}/bin/date", "date")
            script = f'''JQ=jq; VERSION={str(directory / 'version')!r}; STATUS={str(directory / 'status')!r}; REASON={str(directory / 'reason')!r}
mode=proxy; reason=""; verifies=0
read_version() {{ cat "$VERSION" 2>/dev/null || echo 0; }}
atomic_write() {{ printf '%s\\n' "$2" > "$1"; }}
request_verify() {{ verifies=$((verifies+1)); }}
''' + source + '''
publish_state
reason=obs:1/2; publish_state observation
reason=""; publish_state observation
[ "$(jq -r '.policy_version' ''' + str(directory / "state.json") + ''')" = 1 ] && [ "$verifies" = 1 ]
'''
            self.run_shell(script, directory)


if __name__ == "__main__":
    unittest.main(verbosity=2)
