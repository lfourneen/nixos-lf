"""Safety cases for scripts/sync-to-git.sh against disposable trees."""

import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(sys.argv.pop(1)).resolve()
UNSET = object()


class SyncToGitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sync-to-git-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = self.root / "source"
        self.out = self.root / "out"
        files = {
            "flake.nix": (0o640, "flake\n"),
            "secrets/secrets.yaml": (0o600, "secret\n"),
            "scripts/run.sh": (0o755, "#!/bin/sh\n"),
            "docs/manual.md": (0o644, "manual\n"),
        }
        for name, (mode, text) in files.items():
            path = self.src / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            path.chmod(mode)
        self.src.chmod(0o750)

    def run_sync(self, dest=UNSET, force=False):
        env = {
            **os.environ,
            "SUDO": "",
            "SRC_DIR": str(self.src),
            "DEST_BASE": str(self.out),
        }
        env.pop("DEST_DIR", None)
        if dest is not UNSET:
            env["DEST_DIR"] = str(dest)
        command = ["bash", str(SCRIPT)]
        if force:
            command.append("--force")
        return subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)

    def test_default_destination_is_new_and_private(self):
        legacy = self.out / "nixos"
        legacy.mkdir(parents=True)
        (legacy / "sentinel").write_text("keep\n")
        result = self.run_sync()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        destinations = [p for p in self.out.glob("nixos-*") if p.is_dir()]
        self.assertEqual(len(destinations), 1, destinations)
        destination = destinations[0]
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
        self.assertEqual((destination / "flake.nix").read_text(), "flake\n")
        self.assertEqual((legacy / "sentinel").read_text(), "keep\n")

    def test_modes_are_preserved(self):
        result = self.run_sync()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        destination = next(self.out.glob("nixos-*"))
        expected = {
            "flake.nix": 0o640,
            "secrets/secrets.yaml": 0o600,
            "scripts/run.sh": 0o755,
            "docs/manual.md": 0o644,
        }
        for name, mode in expected.items():
            self.assertEqual(stat.S_IMODE((destination / name).stat().st_mode), mode, name)
            self.assertEqual((destination / name).read_bytes(), (self.src / name).read_bytes())

    def test_existing_non_empty_destination_is_refused(self):
        dest = self.root / "destination"
        dest.mkdir()
        (dest / "old.txt").write_text("old\n")
        result = self.run_sync(dest=dest)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("non-empty", result.stderr)
        self.assertEqual((dest / "old.txt").read_text(), "old\n")

    def test_force_preserves_previous_destination_as_backup(self):
        dest = self.root / "destination"
        dest.mkdir()
        (dest / "old.txt").write_text("old\n")
        (dest / "old.txt").chmod(0o640)
        result = self.run_sync(dest=dest, force=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((dest / "flake.nix").read_text(), "flake\n")
        backups = list(self.root.glob("destination.backup-*"))
        self.assertEqual(len(backups), 1, backups)
        backup = backups[0]
        self.assertEqual((backup / "old.txt").read_text(), "old\n")
        self.assertEqual(stat.S_IMODE((backup / "old.txt").stat().st_mode), 0o640)

    def test_empty_explicit_destination_is_used(self):
        dest = self.root / "destination"
        dest.mkdir()
        result = self.run_sync(dest=dest)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((dest / "secrets/secrets.yaml").read_text(), "secret\n")
        self.assertEqual(stat.S_IMODE((dest / "secrets/secrets.yaml").stat().st_mode), 0o600)

    def test_destination_inside_source_is_refused(self):
        dest = self.src / "nested"
        result = self.run_sync(dest=dest)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("overlaps the source", result.stderr)
        self.assertFalse(dest.exists())


if __name__ == "__main__":
    unittest.main()
