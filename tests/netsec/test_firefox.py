"""Launch prerequisites and transition races, with no host state changes."""
import importlib.util
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("firefox_guard", os.environ.get(
    "FIREFOX_GUARD", Path(__file__).resolve().parents[2] / "home/programs/firefox/guard.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


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
