"""Adopt a disposable USBGuard policy without following mutable targets."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / "system/hardware/usbguard/policy.py"
SPEC = importlib.util.spec_from_file_location("policy", SOURCE)
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.store = self.base / "store"
        self.store.mkdir()
        self.source = self.store / "rules"
        self.source.write_text('allow id 1234:abcd\n')
        self.config = self.base / "daemon.conf"
        self.config.write_text(f"RuleFile={self.source}\n")
        self.target = self.base / "private/rules.conf"

    def adopt(self):
        policy.adopt(self.config, self.target, anchor=self.base, store=self.store)

    def test_adoption_is_private_and_idempotent(self):
        self.adopt()
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        self.source.write_text("changed source")
        self.adopt()
        self.assertEqual(self.target.read_text(), 'allow id 1234:abcd\n')

    def test_symlink_target_does_not_touch_sentinel(self):
        self.target.parent.mkdir()
        self.target.symlink_to(self.source)
        before = self.source.read_bytes()
        with self.assertRaises(OSError):
            self.adopt()
        self.assertEqual(self.source.read_bytes(), before)

    def test_hardlinked_policy_is_refused(self):
        self.adopt()
        os.link(self.target, self.base / "alias")
        with self.assertRaises(ValueError):
            self.adopt()

    def test_source_outside_store_is_refused(self):
        self.config.write_text(f"RuleFile={self.base}/outside\n")
        with self.assertRaises(ValueError):
            self.adopt()
        self.assertFalse(self.target.exists())

    def test_default_discovers_configuration_from_service_exec_start(self):
        self.config = self.store / "usbguard-daemon-conf"
        self.config.write_text(f'  RuleFile = "{self.source}"\n')
        unit = f"[Unit]\nDescription=USBGuard\n[Service]\nExecStart=/nix/store/fixture/bin/usbguard-daemon -P -k -c {self.config}\n"
        result = policy.subprocess.CompletedProcess([], 0, unit, "")
        original = policy.config_from_unit
        with patch.object(policy.subprocess, "run", return_value=result), patch.object(
            policy, "config_from_unit", side_effect=lambda text: original(text, store=self.store)
        ):
            policy.adopt(None, self.target, anchor=self.base, store=self.store)
        self.assertEqual(self.target.read_bytes(), self.source.read_bytes())

    def test_existing_policy_needs_no_legacy_config_or_running_manager(self):
        self.adopt()
        with patch.object(policy, "active_config", side_effect=AssertionError("must not query")):
            policy.adopt(None, self.target, anchor=self.base, store=self.store)

    def test_exec_start_dropin_reset_selects_the_effective_configuration(self):
        selected = self.store / "selected-config"
        text = f"[Service]\nExecStart=/old -c {self.store}/old\n[Service]\nExecStart=\nExecStart=/new -c {selected}\n"
        self.assertEqual(policy.config_from_unit(text, self.store), selected)

    def test_ambiguous_or_external_configuration_is_refused(self):
        for text in (
            "[Service]\nExecStart=/daemon\n",
            "[Service]\nExecStart=/daemon -c /etc/outside\n",
            f"[Service]\nExecStart=/daemon -c {self.store}/one\nExecStart=/daemon -c {self.store}/two\n",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                policy.config_from_unit(text, self.store)


if __name__ == "__main__":
    unittest.main()
