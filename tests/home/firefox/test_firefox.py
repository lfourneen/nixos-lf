"""Launch prerequisites and transition races, with no host state changes."""
import importlib.util
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import stat
from socketserver import UnixStreamServer
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("firefox_guard", (os.environ.get("FIREFOX_GUARD") or
    Path(__file__).resolve().parents[3] / "home/programs/firefox/guard.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
spec = importlib.util.spec_from_file_location("firefox_sandbox", (os.environ.get("FIREFOX_SANDBOX") or
    Path(__file__).resolve().parents[3] / "home/programs/firefox/sandbox.py"))
sandbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sandbox)


class GraphicsDevicesTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.devices = Path(work.name) / "dev"
        self.sysfs = Path(work.name) / "sys"
        self.char = {}
        for name in ("dri/renderD128", "dri/renderD129", "dri/card0", "dri/card1",
                     "nvidia0", "nvidiactl", "nvidia-uvm", "nvidia-modeset", "nvidia-uvm-tools"):
            path = self.devices / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
            self.char[path] = os.makedev(226, 129 if name == "dri/renderD129" else 128)
        # A regular file with a device-shaped name must not be exported.
        (self.devices / "dri/renderD130").touch()
        for minor, vendor, card in ((128, "0x10de", "card0"), (129, "0x1002", "card1")):
            device = self.sysfs / f"dev/char/226:{minor}/device"
            (device / "drm" / card).mkdir(parents=True)
            (device / "vendor").write_text(vendor)
        original_stat = Path.stat

        def device_stat(path, *args, **kwargs):
            if path in self.char:
                return SimpleNamespace(st_mode=stat.S_IFCHR | 0o666, st_rdev=self.char[path])
            return original_stat(path, *args, **kwargs)

        self.patch = mock.patch.object(Path, "stat", device_stat)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def selected(self, nvidia):
        return {str(p.relative_to(self.devices)) for p in
                sandbox.graphics_devices(nvidia, self.devices, self.sysfs)}

    def test_render_only_does_not_expose_primary_or_nvidia_devices(self):
        self.assertEqual(self.selected(False), {"dri/renderD128", "dri/renderD129"})

    def test_nvidia_access_is_scoped_to_its_gpu_and_rendering_interfaces(self):
        self.assertEqual(self.selected(True), {"dri/renderD128", "dri/renderD129", "dri/card0",
                                              "nvidia0", "nvidiactl", "nvidia-uvm"})

    def test_driver_configuration_alone_cannot_expose_unrelated_primary_nodes(self):
        (self.sysfs / "dev/char/226:128/device/vendor").write_text("0x1002")
        self.assertEqual(self.selected(True), {"dri/renderD128", "dri/renderD129"})


class LaunchTests(unittest.TestCase):
    def test_strict_socket_is_checked_before_reporting_prerequisites_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.sock'
            with self.assertRaisesRegex(ValueError, 'strict proxy socket is unavailable'):
                sandbox.prerequisites({'requireProxy': True, 'proxySocket': str(path)})
            path.touch()
            with self.assertRaisesRegex(ValueError, 'invalid ownership or permissions'):
                sandbox.prerequisites({'requireProxy': True, 'proxySocket': str(path)})

    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.profile = Path(work.name) / "profile"
        self.settings = dict(profile=str(self.profile), strictPrivacy=True, maxAge=120,
                             verificationMaxAge=420, fonts="/fonts", fontList="/fc-list",
                             fontDirectories=["/store/fonts"])

    def prepare(self, check, core=None, **kwargs):
        return guard.prepare(self.settings, "/current/bin/.firefox-unchecked", check=check,
                             check_core=core, check_fonts=lambda _: None, **kwargs)

    def test_proxy_requires_fresh_verification_and_current_core(self):
        bad = lambda **_: dict(mode="proxy", ready=False, reasons=["rules verification stale"])
        with self.assertRaisesRegex(ValueError, "verification stale"):
            self.prepare(bad)
        self.assertFalse(self.profile.exists())
        good = lambda **_: dict(mode="proxy", ready=True, policy_version=8)
        core = mock.Mock()
        command = self.prepare(good, core)
        core.assert_called_once_with(self.settings, good())
        self.assertEqual(command, ["/current/bin/.firefox-unchecked", "--profile", str(self.profile)])
        self.assertEqual(self.profile.stat().st_mode & 0o777, 0o700)
        with self.assertRaisesRegex(ValueError, "core is old"):
            self.prepare(good, mock.Mock(side_effect=ValueError("core is old")))

    def test_live_core_preflight_uses_desktop_socket_and_preserves_rejection(self):
        # Exercise the actual subprocess/API path rather than mocking verify_core.
        # The privileged snapshot contract is checked by the root supervisor;
        # this desktop view contains only the live API/listener expectations.
        directory = self.profile.parent
        listener_check = directory / "listeners"
        listener_check.write_text(f"#!{sys.executable}\nraise SystemExit(0)\n")
        listener_check.chmod(0o700)
        config = {"mode": "rule", "mixed-port": 7897}

        class Controller(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/configs":
                    self.send_error(404)
                    return
                body = json.dumps(config).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        socket_path = directory / "gui.sock"
        server = UnixStreamServer(str(socket_path), Controller)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join)
        self.addCleanup(server.shutdown)
        profiles = {}
        for mode, digest in (("rules", "a" * 64), ("proxy-only", "b" * 64)):
            profile = directory / (mode + ".json")
            profile.write_text(json.dumps({
                "socket": str(socket_path), "listener_check": str(listener_check), "budget_seconds": 4,
                "expected": {"managed-policy": digest, "mixed-port": 7897},
            }))
            profiles[mode] = str(profile)
        self.settings.update(
            healthModule=(os.environ.get("FIREFOX_HEALTH") or str(Path(__file__).resolve().parents[3]
                / "system/config/network/proxy/health.py")),
            healthSettings=profiles)
        routing = dict(mode="proxy", ready=True, policy_version=8, routing_consistent=True,
                       routing_phase="ready", routing_epoch=7)
        for mode, digest in (("rules", "a" * 64), ("proxy-only", "b" * 64)):
            with self.subTest(mode=mode):
                routing.update(routing_requested=mode, routing_applied=mode, routing_policy_digest=digest)
                config["managed-policy"] = digest
                self.assertEqual(self.prepare(lambda **_: routing, check_only=True), [])
        self.assertFalse(self.profile.exists())
        good = lambda **_: routing
        config["managed-policy"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "managed-policy differs"):
            self.prepare(good, check_only=True)
        routing["routing_policy_digest"] = "a" * 64
        with self.assertRaisesRegex(ValueError, "routing policy digest differs"):
            self.prepare(good, check_only=True)
        routing["routing_policy_digest"] = "b" * 64
        config["managed-policy"] = "b" * 64
        listener_check.write_text(f"#!{sys.executable}\nraise SystemExit(1)\n")
        with self.assertRaisesRegex(ValueError, "listener identity mismatch"):
            self.prepare(good, check_only=True)
        listener_check.write_text(f"#!{sys.executable}\nraise SystemExit(0)\n")
        config["mode"] = "direct"
        with self.assertRaisesRegex(ValueError, "core mode is direct"):
            self.prepare(good, check_only=True)
        health_settings = Path(profiles["proxy-only"])
        health_settings.write_text(health_settings.read_text().replace("gui.sock", "absent.sock"))
        with self.assertRaisesRegex(ValueError, "controller reply unavailable"):
            self.prepare(good, check_only=True)

    def test_direct_admits_offline_portal_but_no_other_mode(self):
        offline = lambda **_: dict(mode="direct", ready=False, policy_version=7)
        core = mock.Mock(side_effect=AssertionError("A must not need a core"))
        self.assertEqual(self.prepare(offline, core, check_only=True), [])
        self.assertFalse(self.profile.exists())
        self.assertEqual(self.prepare(offline, core, values=["http://192.168.1.1/"])[-1],
                         "http://192.168.1.1/")
        for mode in ("blocked", "unenforced", "unknown"):
            with self.assertRaisesRegex(ValueError, mode):
                self.prepare(lambda **_: dict(mode=mode, ready=False))

    def test_transition_or_generation_change_cannot_authorize_launch(self):
        start = dict(mode="proxy", ready=True, policy_version=7, routing_epoch=4, egress_epoch=2)
        for end in (dict(mode="proxy", ready=True, policy_version=8),
                    dict(mode="blocked", ready=False, policy_version=8),
                    dict(mode="proxy", ready=False, policy_version=7),
                    dict(start, routing_epoch=5), dict(start, egress_epoch=3)):
            rounds = iter([start, end])
            with self.assertRaisesRegex(ValueError, "state changed"):
                self.prepare(lambda **_: next(rounds), mock.Mock())
        self.assertFalse(self.profile.exists())

    def test_cli_cannot_replace_profile_enable_debugging_or_inject_options(self):
        for option in ("-profile", "--profile=/other", "--marionette", "--remote-debugging-port", "--"):
            with self.assertRaises(ValueError):
                guard.arguments([option])
        self.assertEqual(guard.arguments(["--name", "firefox", "--new-window", "https://example.org"]),
                         ["--new-window", "https://example.org"])

    def test_fonts_reject_host_inventory_and_host_environment(self):
        with mock.patch.dict(os.environ, {"FONTCONFIG_FILE": "/personal", "FONTCONFIG_SYSROOT": "/host",
                                        "MOZ_DISABLE_CONTENT_SANDBOX": "1"}):
            env = guard.environment(self.settings)
            self.assertEqual(env["FONTCONFIG_FILE"], "/fonts")
            self.assertNotIn("FONTCONFIG_SYSROOT", env)
            self.assertNotIn("MOZ_DISABLE_CONTENT_SANDBOX", env)
            self.assertEqual(env["GDK_BACKEND"], "wayland")
        for output in ("", "/store/fonts/good.ttf\n/home/user/font.ttf\n"):
            with mock.patch.object(guard.subprocess, "run", return_value=SimpleNamespace(
                    returncode=0, stdout=output, stderr="")):
                with self.assertRaisesRegex(ValueError, "font inventory"):
                    guard.verify_fonts(self.settings)

    def test_existing_old_process_requires_restart(self):
        self.profile.mkdir()
        (self.profile / "lock").symlink_to("127.0.1.1:+" + str(os.getpid()))
        with self.assertRaisesRegex(ValueError, "close the existing Firefox"):
            guard.verify_running_generation(self.profile, "/current/bin/.firefox-unchecked")


class StrictBrowserTests(unittest.TestCase):
    def test_portal_checks_explicit_verified_direct_without_dns_and_binds_one_grant(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mode_path, netsec = root / 'proxy-mode', root / 'netsec'
            mode_path.mkdir()
            netsec.mkdir()
            original = os.environ.get('FIREFOX_STATE') or str(Path(__file__).resolve().parents[3]
                / 'system/config/network/proxy/state.py')
            state_file = root / 'state.py'
            state_file.write_text(f"from functools import partial\nfrom runpy import run_path\n"
                f"portal_authority = partial(run_path({original!r})['portal_authority'], root={directory!r})\n")
            profile = root / 'temporary-profile'
            settings = dict(portalOnly=True, strictPrivacy=False, profile=str(profile),
                            maxAge=10, verificationMaxAge=10, stateModule=str(state_file))
            boot = '00000000-1111-2222-3333-444444444444'
            (mode_path / 'boot-id').write_text(boot)
            def publish(mode, version=5, age=0):
                stamp = time.clock_gettime(time.CLOCK_BOOTTIME) - age
                common = dict(mode=mode, policy_version=version, monotonic_seconds=stamp)
                for file in ('state.json', 'heartbeat.json'):
                    (mode_path / file).write_text(json.dumps(common))
                (netsec / 'state.json').write_text(json.dumps(dict(result='ok', version=version,
                    boot_id=boot, monotonic_seconds=stamp)))
            intent = mode_path / 'intent'
            publish('direct')
            for mode, requested, age in [('direct', None, 0), ('direct', 'auto', 0),
                    ('proxy', 'proxy', 0), ('blocked', 'direct', 0), ('direct', 'direct', 15)]:
                with self.subTest(mode=mode, requested=requested, age=age):
                    if requested:
                        intent.write_text(requested)
                    publish(mode, age=age)
                    with self.assertRaisesRegex(ValueError, 'verified explicit Mode A'):
                        guard.prepare(settings, '/unused', check_only=True)
                    self.assertFalse(profile.exists())
            publish('direct')
            self.assertEqual(guard.prepare(settings, '/unused', check_only=True), [])
            self.assertEqual(settings['portalAuthority'], (boot, 5))
            with self.assertRaisesRegex(ValueError, 'authorization changed'):
                guard.prepare(settings, '/unused', check_fonts=lambda _: publish('direct', version=6))

    def test_follow_system_guard_still_checks_current_proxy_health(self):
        # Existing readiness/race tests cover the shared contract. Strict mode
        # must additionally refuse A before touching the profile or desktop.
        with tempfile.TemporaryDirectory() as directory:
            settings = {'requireProxy':True, 'profile':directory, 'maxAge':10, 'verificationMaxAge':10}
            with self.assertRaisesRegex(ValueError, 'requires healthy proxy'):
                guard.prepare(settings, '/unused', check=lambda **_: {'mode':'direct'})


if __name__ == "__main__":
    unittest.main()
