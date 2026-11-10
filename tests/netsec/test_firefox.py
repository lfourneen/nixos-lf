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
from types import SimpleNamespace
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("firefox_guard", os.environ.get(
    "FIREFOX_GUARD", Path(__file__).resolve().parents[2] / "home/programs/firefox/guard.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
spec = importlib.util.spec_from_file_location("firefox_sandbox", os.environ.get(
    "FIREFOX_SANDBOX", Path(__file__).resolve().parents[2] / "home/programs/firefox/sandbox.py"))
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
        core.assert_called_once_with(self.settings)
        self.assertEqual(command, ["/current/bin/.firefox-unchecked", "--profile", str(self.profile)])
        self.assertEqual(self.profile.stat().st_mode & 0o777, 0o700)
        with self.assertRaisesRegex(ValueError, "core is old"):
            self.prepare(good, lambda _: (_ for _ in ()).throw(ValueError("core is old")))

    def test_live_core_preflight_uses_desktop_socket_and_preserves_rejection(self):
        # Exercise the actual subprocess/API path rather than mocking verify_core.
        # The privileged snapshot contract is checked by the root supervisor;
        # this desktop view contains only the live API/listener expectations.
        directory = self.profile.parent
        listener_check = directory / "listeners"
        listener_check.write_text(f"#!{sys.executable}\nraise SystemExit(0)\n")
        listener_check.chmod(0o700)
        config = {"mode": "rule", "managed-policy": "expected", "mixed-port": 7897}

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
        health_settings = directory / "desktop-health.json"
        health_settings.write_text(json.dumps({
            "socket": str(socket_path), "listener_check": str(listener_check),
            "budget_seconds": 4, "expected": {"managed-policy": "expected", "mixed-port": 7897},
        }))
        self.settings.update(
            healthModule=os.environ.get("FIREFOX_HEALTH", str(Path(__file__).resolve().parents[2]
                / "system/programs/systemd/netsec/health.py")),
            healthSettings=str(health_settings))
        good = lambda **_: dict(mode="proxy", ready=True, policy_version=8)
        self.assertEqual(self.prepare(good, check_only=True), [])
        self.assertFalse(self.profile.exists())
        config["managed-policy"] = "wrong-policy"
        with self.assertRaisesRegex(ValueError, "managed-policy differs"):
            self.prepare(good, check_only=True)
        config["managed-policy"] = "expected"
        config["mode"] = "direct"
        with self.assertRaisesRegex(ValueError, "core mode is direct"):
            self.prepare(good, check_only=True)
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
        start = dict(mode="proxy", ready=True, policy_version=7)
        for end in (dict(mode="proxy", ready=True, policy_version=8),
                    dict(mode="blocked", ready=False, policy_version=8),
                    dict(mode="proxy", ready=False, policy_version=7)):
            rounds = iter([start, end])
            with self.assertRaisesRegex(ValueError, "state changed"):
                self.prepare(lambda **_: next(rounds), lambda _: None)
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


if __name__ == "__main__":
    unittest.main()
