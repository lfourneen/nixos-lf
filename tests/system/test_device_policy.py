#!/usr/bin/env python3
"""Static checks for USB device, input, and crash-dump policy."""

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]


class DevicePolicyTest(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_udev_rules_are_not_world_writable(self):
        text = self.read("system/programs/udev.nix")
        self.assertNotRegex(text, r'MODE\s*=\s*"0666"')

    def test_udev_rules_run_after_usb_id_and_before_uaccess(self):
        text = self.read("system/programs/udev.nix")
        match = re.search(r"lib/udev/rules\.d/(\d+)-[^/]+\.rules", text)
        self.assertIsNotNone(match)
        number = int(match.group(1))
        self.assertGreater(number, 50)
        self.assertLess(number, 73)

    def test_adb_rule_matches_the_adb_interface(self):
        text = self.read("system/programs/udev.nix")
        self.assertIn('ENV{ID_USB_INTERFACES}=="*:ff4201:*"', text)
        self.assertIn('GROUP="adbusers"', text)

    def test_required_groups_are_defined(self):
        text = self.read("system/config/user.nix")
        self.assertRegex(text, r"adbusers\s*=\s*\{\s*\}")
        self.assertRegex(text, r'"usbguard-admin"\s*=\s*\{\s*\}')

    def test_usbguard_applies_the_policy_to_present_devices(self):
        text = self.read("system/hardware/usbguard/usbguard.nix")
        self.assertRegex(text, r'presentDevicePolicy\s*=\s*"apply-policy"')
        self.assertNotRegex(text, r'presentDevicePolicy\s*=\s*"allow"')

    def test_usbguard_ipc_excludes_the_desktop_user(self):
        text = self.read("system/hardware/usbguard/usbguard.nix")
        users = re.search(r"IPCAllowedUsers\s*=\s*\[(.*?)\];", text, re.DOTALL)
        groups = re.search(r"IPCAllowedGroups\s*=\s*\[(.*?)\];", text, re.DOTALL)
        self.assertIsNotNone(users)
        self.assertIsNotNone(groups)
        self.assertIn('"root"', users.group(1))
        self.assertNotIn('"lfour"', users.group(1))
        self.assertIn('"usbguard-admin"', groups.group(1))
        self.assertNotIn('"wheel"', groups.group(1))

    def test_coredump_stores_nothing_and_has_no_max_age(self):
        text = self.read("system/programs/systemd/coredump.nix")
        self.assertNotIn("MaxAge", text)
        self.assertNotIn("coredump-clean", text)
        self.assertRegex(text, r'Storage\s*=\s*"none"')
        self.assertRegex(text, r'ProcessSizeMax\s*=\s*"0"')

    def test_qemu_does_not_run_as_root(self):
        text = self.read("system/programs/virtualisation/default.nix")
        self.assertRegex(text, r"runAsRoot\s*=\s*false")
        self.assertNotRegex(text, r"runAsRoot\s*=\s*true")


if __name__ == "__main__":
    unittest.main(verbosity=2)
