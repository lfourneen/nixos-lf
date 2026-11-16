"""Shared fixture for disposable deployment-script tests."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


MARKER = ".nixos-lf-deploy"
MARKER_CONTENT = "version=1\n"

FILES = {
    "flake.nix": "flake-v1\n",
    "flake.lock": "{}\n",
    ".sops.yaml": "sops-v1\n",
    "local-data/dns-blocklist.txt.xz": "blocklist-v1\n",
    "local-data/README.md": "local data\n",
    "home/config/user.nix": "user-v1\n",
    "home/config/agent-skills/safe-delete/SKILL.md": "skill-v1\n",
    "system/config/network/network.nix": "network-v1\n",
    "overlays/default.nix": "overlay-v1\n",
    "tests/network/fixture.py": "fixture-v1\n",
    "scripts/refresh-dns-blocklist.py": "validator-v1\n",
    "system/programs/hermes/hermes-skills/agently-mail/SKILL.md": "hermes-v1\n",
    "README.md": "repository manual\n",
    "docs/maintenance.md": "repository manual\n",
    "audit/TODO.md": "audit notes\n",
}


class DeploymentCase(unittest.TestCase):
    script = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="deploy-safety-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "checkout"
        self.dest = self.root / "destination"
        self.state = self.root / "state"
        self.lock = self.root / "deploy.lock"
        for name, text in FILES.items():
            self.write(self.repo / name, text)
        target = self.repo / "scripts/push-to-dir.sh"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.script, target)
        target.chmod(0o755)
        helper = self.script.parent / "deployment-resources.py"
        if helper.exists():
            shutil.copyfile(helper, self.repo / "scripts/deployment-resources.py")

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def write_marker(self, content=MARKER_CONTENT):
        self.write(self.dest / MARKER, content)

    def deploy(self, dest=None, adopt=False, args=(), env=None):
        command = ["bash", str(self.repo / "scripts/push-to-dir.sh"), *args]
        if adopt:
            command.append("--adopt-existing")
        full_env = {
            **os.environ,
            "SUDO": "",
            "DEST_DIR": str(dest if dest is not None else self.dest),
            "DEPLOY_SKIP_EVAL": "1",
                "DEPLOY_TEST_MODE": "1",
            "DEPLOY_STATE_DIR": str(self.state),
            "DEPLOY_LOCK_FILE": str(self.lock),
        }
        if env:
            full_env.update(env)
        return subprocess.run(
            command,
            env=full_env,
            cwd=self.repo,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def assert_marker(self, dest=None):
        marker = (dest if dest is not None else self.dest) / MARKER
        self.assertEqual(marker.read_text(), MARKER_CONTENT)
