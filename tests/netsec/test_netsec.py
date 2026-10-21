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
SOURCE = (MODULES / "proxy-mode.nix").read_text()


def shell_function(name):
    match = re.search(r"^    " + name + r"\(\) \{\n.*?^    \}", SOURCE, re.M | re.S)
    if not match:
        raise AssertionError(f"missing shell function {name}")
    return match.group(0).replace("''${", "${")


class HealthTests(unittest.TestCase):
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
            probe.tun_test = lambda: None
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
        self.assertEqual(calls, [])


def policy_document():
    return {"nftables": [
        {"table": {"family": "inet", "name": "lf_filter"}},
        {"chain": {"family": "inet", "table": "lf_filter", "name": "input", "hook": "input", "prio": 0, "policy": "drop"}},
        {"chain": {"family": "inet", "table": "lf_filter", "name": "lf_blocked_guard", "hook": "output", "prio": -200}},
        {"rule": {"family": "inet", "table": "lf_filter", "chain": "input", "expr": [{"accept": None}]}},
        {"rule": {"family": "inet", "table": "lf_filter", "chain": "input", "expr": [{"drop": None}]}},
    ]}


class RulesTests(unittest.TestCase):
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
