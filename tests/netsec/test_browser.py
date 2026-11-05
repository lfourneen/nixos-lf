"""Browser launch contract, without launching an application or using a network."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("browser_guard", os.environ.get("BROWSER_GUARD",
    Path(__file__).resolve().parents[2] / "system/programs/browser-guard.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class LaunchTests(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.object(guard, "verify_sandbox")
        self.sandbox = patch.start()
        self.addCleanup(patch.stop)

    def test_launch_requires_current_policy_and_uses_separate_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, installed, prefs = [root / x for x in ("expected", "installed", "prefs")]
            expected.write_text('{}'); installed.write_text('{}')
            prefs.write_text('user_pref("privacy.resistFingerprinting", true);\n')
            settings = {"flatpak": "/flatpak", "strictPrivacy": False, "maxAge": 120, "verificationMaxAge": 420,
                        "userJs": str(prefs), "zenPolicy": str(expected),
                        "chromePolicy": str(expected), "installedZenPolicy": str(installed),
                        "installedChromePolicy": str(installed)}
            def refuse(**kwargs):
                return {"ready": False, "mode": "proxy", "reasons": ["rules verification stale"]}
            for browser in ("zen", "chrome"):
                with self.assertRaisesRegex(ValueError, "verification stale"):
                    guard.prepare(settings, browser, root, refuse)
            self.assertFalse((root / ".var").exists())
            def accept(**kwargs):
                self.assertEqual(kwargs, {"target": "proxy", "maximum": 120, "verification_maximum": 420})
                return {"ready": True, "mode": "proxy"}
            seen = []
            def current_core(value): seen.append(value)
            command = guard.prepare(settings, "zen", root, accept, current_core)
            self.assertEqual(seen, [settings])
            self.assertNotIn("--no-remote", command)
            self.assertEqual(command[-1], str(root / ".zen/netsec-private"))
            userjs = root / ".var/app/app.zen_browser.zen/.zen/netsec-private/user.js"
            self.assertEqual(userjs.read_text(), prefs.read_text())
            userjs.parent.chmod(0o755)
            self.assertEqual(command, guard.prepare(settings, "zen", root, accept, current_core))
            self.assertEqual(userjs.parent.stat().st_mode & 0o777, 0o700)
            command = guard.prepare(settings, "chrome", root, accept, current_core)
            self.assertIn("netsec-private", command[-1])
            installed.write_text('{"old":true}')
            for browser in ("zen", "chrome"):
                with self.assertRaisesRegex(ValueError, "policy is stale"):
                    guard.prepare(settings, browser, root, accept, current_core)
            installed.write_text('{}')
            def old_core(_settings): raise ValueError("current core policy is not verified")
            with self.assertRaisesRegex(ValueError, "core policy"):
                guard.prepare(settings, "zen", root, accept, old_core)
            rounds = iter([{"ready": True, "mode": "proxy", "policy_version": 1},
                           {"ready": True, "mode": "proxy", "policy_version": 2}])
            with self.assertRaisesRegex(ValueError, "state changed"):
                guard.prepare(settings, "zen", root, lambda **_kw: next(rounds), current_core)

    def test_offline_direct_mode_allows_portal_without_dns_or_core(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "policy"; policy.write_text('{}')
            prefs = root / "prefs"; prefs.write_text('// managed\n')
            settings = {"flatpak": "/flatpak", "strictPrivacy": False, "maxAge": 120, "verificationMaxAge": 420,
                        "userJs": str(prefs), "zenPolicy": str(policy), "chromePolicy": str(policy),
                        "installedZenPolicy": str(policy), "installedChromePolicy": str(policy)}
            def offline(**_kwargs):
                return {"ready": False, "mode": "direct", "policy_version": 7,
                        "reasons": ["direct DNS unavailable", "rules verification stale"]}
            def no_core(_settings): self.fail("Mode A must not require an Internet-capable proxy core")
            self.assertEqual(guard.prepare(settings, "zen", root, offline, no_core, check_only=True), [])
            self.assertFalse((root / ".var").exists())
            for browser in ("zen", "chrome"):
                command = guard.prepare(settings, browser, root, offline, no_core)
                self.assertEqual(command[:2], ["/flatpak", "run"])
            for mode in ("blocked", "unenforced", "unknown"):
                with self.assertRaisesRegex(ValueError, "network policy is " + mode):
                    guard.prepare(settings, "zen", root,
                                  lambda **_kwargs: {"ready": False, "mode": mode}, no_core)
            rounds = iter([offline(), {"ready": False, "mode": "blocked", "policy_version": 8}])
            with self.assertRaisesRegex(ValueError, "state changed"):
                guard.prepare(settings, "zen", root, lambda **_kw: next(rounds), no_core)

            fonts = root / "fonts"; fonts.write_text('current')
            old_fonts = root / "old-fonts"; old_fonts.write_text('old')
            settings.update(strictPrivacy=True, fonts=str(fonts), installedZenFonts=str(old_fonts))
            with self.assertRaisesRegex(ValueError, "font configuration is stale"):
                guard.prepare(settings, "zen", root, offline, no_core)


class SandboxTests(unittest.TestCase):
    def test_production_mounts_and_inventory_are_checked_without_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "policy"; policy.write_text('{}')
            fonts = root / "fonts"; fonts.write_text('<fontconfig/>')
            settings = {"flatpak": "/flatpak", "strictPrivacy": True, "fonts": str(fonts),
                        "zenPolicy": str(policy), "chromePolicy": str(policy),
                        "fontDirectories": ["/usr/share/fonts", "/nix/store/fixed-fonts/share/fonts"]}
            output = ("policy:" + hashlib.sha256(policy.read_bytes()).hexdigest() + "  policy\n"
                      + "fontconfig:" + hashlib.sha256(fonts.read_bytes()).hexdigest() + "  fonts\n"
                      + "font:/usr/share/fonts/regular.ttf\nfont:/nix/store/fixed-fonts/share/fonts/cjk.otf\n")
            seen = []
            def runner(args, **kwargs):
                seen.append(args)
                return SimpleNamespace(returncode=0, stdout=output, stderr="")
            for browser in ("zen", "chrome"):
                guard.verify_sandbox(settings, browser, runner)
            self.assertTrue(all(not arg.startswith(("--env", "--filesystem")) for args in seen for arg in args))
            for broken in (output + "font:/run/host/fonts/personal.ttf\n",
                           output.replace("fontconfig:", "missing:"),
                           output[:output.index("font:")]):
                with self.assertRaises(ValueError):
                    guard.verify_sandbox(settings, "chrome", lambda *a, **kw:
                                         SimpleNamespace(returncode=0, stdout=broken, stderr=""))
            with self.assertRaisesRegex(ValueError, "unavailable"):
                guard.verify_sandbox(settings, "chrome", lambda *a, **kw:
                                     SimpleNamespace(returncode=0, stdout=output, stderr="Fontconfig error: fallback"))


if __name__ == "__main__":
    unittest.main()
