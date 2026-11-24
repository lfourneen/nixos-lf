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
MARKER = ".nixos-lf-deploy"


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="deployment-layout-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "checkout"
        self.dest = self.root / "destination"
        self.keep = {
            "flake.nix", "flake.lock", ".sops.yaml",
            "home/config/user.nix", "home/config/assets/local.png",
            "overlays/default.nix", "overlays/packages.nix", "system/config/network/network.nix",
            "system/secrets/secrets.yaml",
            "home/config/agent-skills/safe-delete/SKILL.md",
            "home/config/agent-skills/safe-delete/references/local-protection.md",
            "home/config/agent-skills/safe-process-stop/SKILL.md",
            "home/config/agent-skills/nix-missing-tool/SKILL.md",
            "home/config/agent-skills/nix-missing-tool/references/project-flakes.md",
            "home/config/agent-skills/nix-missing-tool/references/native-dependencies.md",
            "system/programs/hermes/hermes-skills/agently-mail/SKILL.md",
            "system/programs/hermes/hermes-skills/example/SKILL.md",
            "system/programs/hermes/hermes-skills/example/references/guide.md",
            "tests/network/integration/fixture.py",
            "maintenance/inventory.json",
            "scripts/network-benchmark.py",
            "scripts/push-to-dir.sh",
            "scripts/deployment-resources.py",
            "scripts/refresh-dns-blocklist.py",
            "local-data/dns-blocklist.txt.xz",
            "local-data/README.md",
        }
        excluded = {
            "docs/README.md", "docs/CN/network_CN.md", "docs/example.json",
            "README.md", "README_CN.md", "LICENSE", "audit/local.txt",
            "home/README.md", "system/config/notes.md", "tests/README.md",
            "home/config/notes.md", "overlays/maintenance.md",
            "overlays/maintenance_CN.md", "scripts/README.md",
            "scripts/__pycache__/tool.pyc",
            "home/config/editor.swp", "system/config/backup.nix.bak",
            "overlays/.direnv/cache", "tests/result-old/output",
        }
        for name in self.keep | excluded:
            self.write(self.repo / name, name)
        target_script = self.repo / "scripts/push-to-dir.sh"
        target_script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SCRIPT, target_script)
        shutil.copyfile(SCRIPT.parent / "deployment-resources.py", self.repo / "scripts/deployment-resources.py")

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def deploy(self, destination=None, adopt=True):
        command = ["bash", str(self.repo / "scripts/push-to-dir.sh")]
        if adopt:
            command.append("--adopt-existing")
        return subprocess.run(
            command,
            env={
                **os.environ,
                "SUDO": "",
                "DEST_DIR": str(destination or self.dest),
                "DEPLOY_SKIP_EVAL": "1",
                "DEPLOY_TEST_MODE": "1",
                "DEPLOY_STATE_DIR": str(self.root / "state"),
                "DEPLOY_LOCK_FILE": str(self.root / "deploy.lock"),
            },
            cwd=self.repo,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_build_inputs_without_repository_docs(self):
        self.write(self.dest / "docs/old.md", "obsolete documentation")
        self.write(self.dest / "PROXY.md", "obsolete root manual")
        self.write(self.dest / "overlays/maintenance.md", "obsolete manual")
        self.write(self.dest / "system/stale.nix", "obsolete configuration")
        inode = self.dest.stat().st_ino
        for _ in range(2):
            result = self.deploy()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.dest.stat().st_ino, inode)
            files = {str(p.relative_to(self.dest)) for p in self.dest.rglob("*") if p.is_file()}
            self.assertEqual(files, self.keep | {MARKER})
            for name in self.keep:
                self.assertEqual((self.dest / name).read_bytes(), (self.repo / name).read_bytes(),
                                 name)
            marker = self.dest / MARKER
            self.assertEqual(marker.read_text(), "version=1\n")
            for path in [self.dest, *self.dest.rglob("*")]:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode),
                                 0o700 if path.is_dir() else 0o600, path)

    def test_agent_skill_payload_keeps_directory_structure(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        expected_dirs = set()
        for name in self.keep:
            parent = Path(name).parent
            while str(parent) != ".":
                expected_dirs.add(str(parent))
                parent = parent.parent
        deployed_dirs = {str(p.relative_to(self.dest)) for p in self.dest.rglob("*")
                         if p.is_dir()}
        self.assertEqual(deployed_dirs, expected_dirs)
        for skill in ("safe-delete", "safe-process-stop", "nix-missing-tool"):
            skill_dir = self.dest / "home/config/agent-skills" / skill
            self.assertTrue((skill_dir / "SKILL.md").is_file(), skill_dir)
        references = self.dest / "home/config/agent-skills/nix-missing-tool/references"
        self.assertEqual({p.name for p in references.iterdir()},
                         {"project-flakes.md", "native-dependencies.md"})
        self.assertTrue((self.dest / "system/programs/hermes/hermes-skills/"
                         "agently-mail/SKILL.md").is_file())
        self.assertFalse((self.dest / "docs").exists())
        self.assertFalse((self.dest / "README.md").exists())
        self.assertFalse((self.dest / "audit").exists())
        self.assertFalse((self.dest / "overlays/maintenance.md").exists())

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
