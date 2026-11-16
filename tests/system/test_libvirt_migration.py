#!/usr/bin/env python3
"""Behavior checks for scripts/libvirt-qemu-migrate.sh using temporary roots."""

from pathlib import Path
import grp
import os
import pwd
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "libvirt-qemu-migrate.sh"


class MigrationScriptTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="libvirt-migrate-")
        self.root = Path(self._tmp.name).resolve()
        self.user = pwd.getpwuid(os.geteuid()).pw_name
        self.group = grp.getgrgid(os.getegid()).gr_name
        (self.root / "images").mkdir()
        (self.root / "qemu").mkdir()
        (self.root / "images" / "disk.qcow2").write_bytes(b"image")
        (self.root / "qemu" / "nvram.fd").write_bytes(b"nvram")

    def tearDown(self):
        self._tmp.cleanup()

    def run_script(self, *args, root=None, owner=None, group=None):
        env = os.environ.copy()
        env["LIBVIRT_QEMU_MIGRATE_ROOT"] = str(root if root is not None else self.root)
        env["LIBVIRT_QEMU_MIGRATE_OWNER"] = owner if owner is not None else self.user
        env["LIBVIRT_QEMU_MIGRATE_GROUP"] = group if group is not None else self.group
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def snapshot(self):
        return {
            path: (path.stat().st_uid, path.stat().st_gid)
            for path in sorted(self.root.rglob("*"))
        }

    def test_dry_run_changes_nothing(self):
        before = self.snapshot()
        result = self.run_script(owner="root", group="root")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY-RUN", result.stdout)
        self.assertIn("chown -R root:root", result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_apply_requires_root(self):
        if os.geteuid() == 0:
            self.skipTest("test asserts the non-root refusal path")
        before = self.snapshot()
        result = self.run_script("--apply", owner="root", group="root")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires root", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_symlink_target_is_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep").write_bytes(b"keep")
        (self.root / "images").rename(self.root / "images-real")
        (self.root / "images").symlink_to(outside)
        before = self.snapshot()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_symlink_inside_target_is_refused(self):
        (self.root / "images" / "alias").symlink_to(self.root / "qemu")
        before = self.snapshot()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink inside target tree", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_unsafe_root_is_refused(self):
        result = self.run_script(root=Path("/"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe target root", result.stderr)

    def test_symlinked_root_is_refused(self):
        link = self.root.parent / (self.root.name + "-link")
        link.symlink_to(self.root)
        try:
            result = self.run_script(root=link)
        finally:
            link.unlink()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("contains a symlink", result.stderr)

    def test_already_owned_trees_are_idempotent(self):
        before = self.snapshot()
        first = self.run_script("--apply")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("already migrated", first.stdout)
        second = self.run_script("--apply")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already migrated", second.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_apply_and_second_run_as_root(self):
        if os.geteuid() != 0:
            self.skipTest("apply path requires root")
        try:
            account = pwd.getpwnam("qemu-libvirtd")
            target_group = grp.getgrnam("qemu-libvirtd")
        except KeyError:
            self.skipTest("qemu-libvirtd account is not present")
        first = self.run_script("--apply", owner="qemu-libvirtd", group="qemu-libvirtd")
        self.assertEqual(first.returncode, 0, first.stderr)
        for path in self.root.rglob("*"):
            self.assertEqual(path.stat().st_uid, account.pw_uid, path)
            self.assertEqual(path.stat().st_gid, target_group.gr_gid, path)
        second = self.run_script("--apply", owner="qemu-libvirtd", group="qemu-libvirtd")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already migrated", second.stdout)

    def test_unknown_argument_is_refused(self):
        result = self.run_script("--now")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown argument", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
