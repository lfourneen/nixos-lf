"""Exercise the root Hermes state guard against disposable state roots."""

import grp
import os
import pwd
import importlib.util
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "system/programs/hermes/hermes-state.py"

USER = pwd.getpwuid(os.geteuid()).pw_name
GROUP = grp.getgrgid(os.getegid()).gr_name
SPEC = importlib.util.spec_from_file_location("hermes_state", SCRIPT)
STATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STATE)

LAYOUT = {
    ".hermes": 0o700,
    "home": 0o700,
    "workspace": 0o2770,
    ".local": 0o700,
    ".config": 0o700,
    ".cache": 0o700,
    ".playwright-profiles": 0o700,
}
RUNTIME = ("cron", "sessions", "logs", "memories", "plugins", "skills")


class HermesStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hermes-state-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "state"

    def guard(self, root=None, managed_files=("skills/agently-mail/SKILL.md",)):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root or self.root),
                "--user",
                USER,
                "--group",
                GROUP,
                *(part for name in managed_files for part in ("--managed-file", name)),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    def assert_fails(self, result, needle):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(needle, result.stderr)

    @staticmethod
    def snapshot(path):
        st = path.lstat()
        return (st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode))

    def sentinel_directory(self):
        path = self.base / "outside" / "directory"
        path.mkdir(parents=True)
        os.chmod(path, 0o750)
        data = path / "data"
        data.write_text("sentinel")
        os.chmod(data, 0o640)
        return path

    def sentinel_file(self):
        path = self.base / "outside" / "file"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sentinel")
        os.chmod(path, 0o641)
        return path

    def state(self, name="state"):
        root = self.base / name
        root.mkdir()
        return root

    def test_creates_layout_and_marker(self):
        result = self.guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name, mode in LAYOUT.items():
            st = (self.root / name).lstat()
            self.assertTrue(stat.S_ISDIR(st.st_mode), name)
            self.assertEqual(stat.S_IMODE(st.st_mode), mode, name)
            self.assertEqual((st.st_uid, st.st_gid), (os.geteuid(), os.getegid()))
        for name in RUNTIME:
            st = (self.root / ".hermes" / name).lstat()
            self.assertEqual(stat.S_IMODE(st.st_mode), 0o700, name)
        marker = self.root / ".state-version"
        self.assertEqual(stat.S_IMODE(marker.lstat().st_mode), 0o644)
        self.assertEqual(marker.read_text().strip(), "4")
        backups = list((self.root / ".backup").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual(stat.S_IMODE((self.root / ".backup").lstat().st_mode), 0o700)
        manifest = backups[0] / "manifest.txt"
        self.assertEqual(stat.S_IMODE(manifest.lstat().st_mode), 0o600)
        self.assertIn(f"{self.root}\t", manifest.read_text())

    def test_rerun_is_idempotent(self):
        self.assertEqual(self.guard().returncode, 0, "first run failed")
        marker = self.root / ".state-version"
        before = {
            str(path): self.snapshot(path)
            for path in [self.root, *(self.root / name for name in LAYOUT)]
        }
        marker_mode = marker.stat().st_mtime_ns
        backups = sorted(path.name for path in (self.root / ".backup").iterdir())
        result = self.guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        after = {
            str(path): self.snapshot(path)
            for path in [self.root, *(self.root / name for name in LAYOUT)]
        }
        self.assertEqual(before, after)
        self.assertEqual(marker.stat().st_mtime_ns, marker_mode)
        self.assertEqual(sorted(path.name for path in (self.root / ".backup").iterdir()), backups)

    def test_marker_skips_migration(self):
        self.root.mkdir()
        marker = self.root / ".state-version"
        marker.write_text("4\n")
        os.chmod(marker, 0o644)
        result = self.guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / ".backup").exists())
        self.assertTrue((self.root / ".hermes" / "plugins").is_dir())

    def test_old_marker_runs_migration_once(self):
        self.root.mkdir()
        marker = self.root / ".state-version"
        marker.write_text("1\n")
        os.chmod(marker, 0o644)
        result = self.guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list((self.root / ".backup").iterdir())), 1)
        self.assertEqual(marker.read_text().strip(), "4")
        os.chmod(self.root / "workspace", 0o700)
        self.assertEqual(self.guard().returncode, 0)
        self.assertEqual(
            stat.S_IMODE((self.root / "workspace").lstat().st_mode), 0o2770
        )
        self.assertEqual(len(list((self.root / ".backup").iterdir())), 1)

    def test_old_marker_restores_owner_access_to_skill_tree(self):
        self.root.mkdir()
        skills = self.root / ".hermes" / "skills"
        skill = skills / "agently-mail"
        skill.mkdir(parents=True)
        payload = skill / "SKILL.md"
        payload.write_text("legacy\n")
        os.chmod(payload, 0o444)
        os.chmod(skill, 0o555)
        os.chmod(skills, 0o555)
        marker = self.root / ".state-version"
        marker.write_text("3\n")
        os.chmod(marker, 0o644)
        result = self.guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(skill.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(skills.stat().st_mode), 0o700)
        self.assertEqual(marker.read_text().strip(), "4")
        self.assertEqual(payload.read_text(), "legacy\n")
        self.assertEqual(stat.S_IMODE(payload.stat().st_mode), 0o444)
        installed = self.base / "new-skill.md"
        installed.write_text("managed replacement\n")
        result = subprocess.run(["install", "-m", "0640", str(installed), str(payload)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload.read_text(), "managed replacement\n")
        manifest = next((self.root / ".backup").glob("*/manifest.txt")).read_text()
        self.assertIn(f"{skill}\t{os.geteuid()}\t{os.getegid()}\t0555", manifest)

    def test_unmanaged_state_is_not_traversed_or_reowned(self):
        private = self.root / ".hermes" / "operator-managed"
        private.mkdir(parents=True)
        data = private / "state.db"
        data.write_text("untouched")
        os.link(data, private / "state.link")
        target = self.sentinel_directory()
        (private / "reference").symlink_to(target)
        private.chmod(0o555)
        before = (self.snapshot(private), self.snapshot(data), self.snapshot(target))
        result = self.guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, (self.snapshot(private), self.snapshot(data), self.snapshot(target)))
        self.assertEqual(data.read_text(), "untouched")

    def test_managed_parent_symlink_fails_without_touching_target(self):
        target = self.sentinel_directory()
        skills = self.root / ".hermes" / "skills"
        skills.mkdir(parents=True)
        (skills / "agently-mail").symlink_to(target)
        before = (self.snapshot(target), (target / "data").read_text())
        result = self.guard()
        self.assert_fails(result, "symlinks")
        self.assertEqual(before, (self.snapshot(target), (target / "data").read_text()))
        self.assertFalse((self.root / ".state-version").exists())

    def test_managed_path_traversal_is_refused_before_layout_changes(self):
        for name in ("../outside/file", "/etc/file", "skills/./file", "skills//file", "skills/file\n"):
            with self.subTest(name=name):
                result = self.guard(managed_files=[name])
                self.assert_fails(result, "invalid managed file path")
                self.assertFalse(self.root.exists())

    def test_parent_replaced_between_stat_and_open_is_not_followed(self):
        target = self.sentinel_directory()
        skills = self.root / ".hermes" / "skills"
        parent = skills / "agently-mail"
        parent.mkdir(parents=True)
        original = STATE.open_dir
        replaced = False
        before = (self.snapshot(target), (target / "data").read_text())

        def race(parent_fd, name, path, device):
            nonlocal replaced
            if name == "agently-mail" and not replaced:
                replaced = True
                os.rename(name, "saved", src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                os.symlink(target, name, dir_fd=parent_fd)
            return original(parent_fd, name, path, device)

        with patch.object(STATE, "open_dir", side_effect=race):
            with self.assertRaises(STATE.StateError):
                STATE.apply(str(self.root), USER, GROUP, ["skills/agently-mail/SKILL.md"])
        self.assertTrue(replaced)
        self.assertEqual(before, (self.snapshot(target), (target / "data").read_text()))

    def test_newer_marker_version_aborts(self):
        self.root.mkdir()
        marker = self.root / ".state-version"
        marker.write_text("5\n")
        os.chmod(marker, 0o644)
        result = self.guard()
        self.assert_fails(result, "not supported")
        self.assertFalse((self.root / ".backup").exists())

    def test_directory_symlink_aborts_without_touching_target(self):
        sentinel = self.sentinel_directory()
        before = (
            self.snapshot(sentinel),
            self.snapshot(sentinel / "data"),
            (sentinel / "data").read_text(),
        )
        self.root.mkdir()
        (self.root / "workspace").symlink_to(sentinel)
        result = self.guard()
        self.assert_fails(result, "symlink")
        after = (
            self.snapshot(sentinel),
            self.snapshot(sentinel / "data"),
            (sentinel / "data").read_text(),
        )
        self.assertEqual(before, after)
        self.assertFalse((self.root / ".state-version").exists())

    def test_nested_runtime_symlink_aborts(self):
        sentinel = self.sentinel_directory()
        before = (self.snapshot(sentinel), (sentinel / "data").read_text())
        self.root.mkdir()
        (self.root / ".hermes").mkdir()
        (self.root / ".hermes" / "logs").symlink_to(sentinel)
        result = self.guard()
        self.assert_fails(result, "symlink")
        self.assertEqual(before, (self.snapshot(sentinel), (sentinel / "data").read_text()))

    def test_file_symlink_aborts_without_touching_target(self):
        for name in (".gitconfig", ".state-version"):
            with self.subTest(name=name):
                sentinel = self.sentinel_file()
                before = (self.snapshot(sentinel), sentinel.read_text())
                root = self.state(f"state{name.replace('.', '-')}")
                (root / name).symlink_to(sentinel)
                result = self.guard(root)
                self.assert_fails(result, "symlink")
                self.assertEqual(before, (self.snapshot(sentinel), sentinel.read_text()))

    def test_hardlinked_marker_aborts(self):
        sentinel = self.sentinel_file()
        before = (self.snapshot(sentinel), sentinel.read_text())
        self.root.mkdir()
        os.link(sentinel, self.root / ".state-version")
        result = self.guard()
        self.assert_fails(result, "hardlinked")
        self.assertEqual(before, (self.snapshot(sentinel), sentinel.read_text()))

    def test_symlinked_ancestor_aborts_without_touching_sentinel(self):
        sentinel = self.sentinel_directory()
        before = self.snapshot(sentinel)
        alias = self.base / "alias"
        alias.symlink_to(sentinel.parent)
        result = self.guard(alias / sentinel.name)
        self.assert_fails(result, "symlink")
        self.assertEqual(self.snapshot(sentinel), before)

    def test_symlinked_root_aborts(self):
        sentinel = self.sentinel_directory()
        before = (self.snapshot(sentinel), (sentinel / "data").read_text())
        (self.base / "linked-state").symlink_to(sentinel)
        result = self.guard(self.base / "linked-state")
        self.assert_fails(result, "symlink")
        self.assertEqual(before, (self.snapshot(sentinel), (sentinel / "data").read_text()))

    def test_backup_symlink_aborts(self):
        sentinel = self.sentinel_file()
        before = (self.snapshot(sentinel), sentinel.read_text())
        self.root.mkdir()
        stamp = self.root / ".backup" / "20260101T000000Z"
        stamp.mkdir(parents=True)
        os.chmod(self.root / ".backup", 0o700)
        os.chmod(stamp, 0o700)
        (stamp / "entry").symlink_to(sentinel)
        marker = self.root / ".state-version"
        marker.write_text("3\n")
        os.chmod(marker, 0o644)
        result = self.guard()
        self.assert_fails(result, "symlink")
        self.assertEqual(before, (self.snapshot(sentinel), sentinel.read_text()))


@unittest.skipUnless(
    os.geteuid() == 0 and len(Path("/proc/self/uid_map").read_text().splitlines()) > 1,
    "requires a disposable rootless user namespace with subordinate IDs",
)
class HermesIdentityTests(unittest.TestCase):
    def test_root_owned_parent_is_repaired_then_rendering_runs_as_service_uid(self):
        uid = gid = 65534
        user, group = pwd.getpwuid(uid).pw_name, grp.getgrgid(gid).gr_name
        with tempfile.TemporaryDirectory(prefix="hermes-identity-") as temporary:
            base = Path(temporary)
            base.chmod(0o755)
            root = base / "state"
            parent = root / ".hermes/skills/agently-mail"
            parent.mkdir(parents=True)
            sentinel = base / "sentinel"
            sentinel.write_text("outside sentinel")
            sentinel.chmod(0o444)
            target = parent / "SKILL.md"
            os.link(sentinel, target)
            parent.chmod(0o555)
            (root / ".state-version").write_text("3\n")
            source = base / "replacement"
            source.write_text("new managed skill")
            source.chmod(0o644)

            def drop_identity():
                os.setgid(gid)
                os.setuid(uid)

            def render():
                return subprocess.run(
                    ["install", "-m", "0640", str(source), str(target)],
                    preexec_fn=drop_identity, capture_output=True, text=True)

            before = (sentinel.read_text(), HermesStateTests.snapshot(sentinel))
            denied = render()
            self.assertNotEqual(denied.returncode, 0)
            STATE.apply(str(root), user, group, ["skills/agently-mail/SKILL.md"])
            self.assertEqual(parent.stat().st_uid, uid)
            self.assertEqual(target.stat().st_uid, 0)
            result = render()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.stat().st_uid, uid)
            self.assertEqual(target.read_text(), "new managed skill")
            self.assertEqual(before, (sentinel.read_text(), HermesStateTests.snapshot(sentinel)))
            self.assertEqual(root.stat().st_uid, 0)
            marker_time = (root / ".state-version").stat().st_mtime_ns
            STATE.apply(str(root), user, group, ["skills/agently-mail/SKILL.md"])
            self.assertEqual((root / ".state-version").stat().st_mtime_ns, marker_time)
            self.assertEqual(len(list((root / ".backup").iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
