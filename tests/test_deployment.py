"""Exercise deployment in a miniature checkout and disposable destination."""

import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(sys.argv.pop(1)).resolve()


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="deployment-layout-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "checkout"
        self.dest = self.root / "destination"
        self.keep = {
            "flake.nix", "flake.lock", ".sops.yaml",
            "home/config/user.nix", "home/wallpapers/local.png",
            "overlays/default.nix", "system/config/network.nix",
            "system/secrets/secrets.yaml",
            "system/programs/hermes-skills/example/SKILL.md",
            "system/programs/hermes-skills/example/references/guide.md",
            "tests/netsec/fixture.py", "local-data/dns-blocklist.txt.xz",
            "local-data/README.md",
        }
        excluded = {
            "docs/README.md", "docs/CN/network_CN.md", "docs/example.json",
            "README.md", "README_CN.md", "LICENSE", "audit/local.txt",
            "home/README.md", "system/config/notes.md", "tests/README.md",
            "home/config/editor.swp", "system/config/backup.nix.bak",
            "overlays/.direnv/cache", "tests/result-old/output",
        }
        for name in self.keep | excluded:
            self.write(self.repo / name, name)
        target_script = self.repo / "scripts/push-to-dir.sh"
        target_script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SCRIPT, target_script)

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def deploy(self, destination=None):
        return subprocess.run(
            ["bash", str(self.repo / "scripts/push-to-dir.sh")],
            env={**os.environ, "SUDO": "", "DEST_DIR": str(destination or self.dest)},
            cwd=self.repo,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_build_inputs_without_repository_docs(self):
        self.write(self.dest / "docs/old.md", "obsolete documentation")
        self.write(self.dest / "PROXY.md", "obsolete root manual")
        self.write(self.dest / "system/stale.nix", "obsolete configuration")
        inode = self.dest.stat().st_ino
        for _ in range(2):
            result = self.deploy()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.dest.stat().st_ino, inode)
            files = {str(p.relative_to(self.dest)) for p in self.dest.rglob("*") if p.is_file()}
            self.assertEqual(files, self.keep)
            for name in self.keep:
                self.assertEqual((self.dest / name).read_bytes(), (self.repo / name).read_bytes())
            for path in [self.dest, *self.dest.rglob("*")]:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_missing_input_leaves_existing_destination_intact(self):
        (self.repo / "local-data/dns-blocklist.txt.xz").unlink()
        sentinel = self.dest / "sentinel"
        self.write(sentinel, "keep destination")
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dns-blocklist.txt.xz is missing", result.stderr)
        self.assertEqual(list(self.dest.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_text(), "keep destination")

    def test_missing_input_does_not_create_destination(self):
        (self.repo / "local-data/dns-blocklist.txt.xz").unlink()
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.dest.exists())


if __name__ == "__main__":
    unittest.main()
