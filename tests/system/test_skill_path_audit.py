"""Check declared skill roots with inert skill files and out-of-tree sentinels."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "home/config/agent-skills/skill-path-audit.py"


class SkillPathAuditTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.home = self.base / "home"
        self.desktop = self.home / ".codex/skills"
        self.hermes = self.home / ".hermes/skills"
        self.desktop.mkdir(parents=True)
        self.hermes.mkdir(parents=True)
        self.source = self.base / "approved"
        self.source.mkdir()
        self.write_skill(self.source)
        self.config = self.hermes.parent / "config.yaml"
        self.settings = {
            "home": str(self.home),
            "roots": {
                str(self.desktop): {"links": {"approved": str(self.source)}},
                str(self.hermes): {"copies": {"approved": str(self.source)}, "private": ["sandbox"]},
            },
            "names": ["approved", "sandbox"],
            "projectRoots": [".agents/skills"],
            "environment": {"DSH_HOME": str(self.home / ".dsh")},
            "hermesConfig": str(self.config),
        }

    def audit(self, *args, env=None):
        settings = self.base / "settings.json"
        settings.write_text(json.dumps(self.settings))
        result = subprocess.run(
            [sys.executable, SCRIPT, settings, *args], env=env, capture_output=True,
            text=True, check=True, timeout=10,
        )
        self.assertEqual(result.stderr, "")
        return json.loads(result.stdout)

    @staticmethod
    def write_skill(directory, *, body="approved-body"):
        (directory / "SKILL.md").write_text(f"---\nname: approved\n---\n{body}\n")

    def test_managed_link_and_copy_with_same_name_elsewhere(self):
        (self.desktop / "approved").symlink_to(self.source)
        local = self.hermes / "approved"
        local.mkdir()
        self.write_skill(local)
        repo = self.base / "project"
        (repo / ".git").mkdir(parents=True)
        project_skill = repo / ".agents/skills/other-id"
        project_skill.mkdir(parents=True)
        self.write_skill(project_skill, body="unreviewed-body")

        report = self.audit("--project", project_skill)
        self.assertIn({"entry": "approved", "status": "managed"}, report["roots"][str(self.desktop)])
        self.assertIn({"entry": "approved", "status": "managed-copy"}, report["roots"][str(self.hermes)])
        self.assertIn(
            {"entry": "other-id/SKILL.md", "status": "name-collision"},
            report["roots"][str(project_skill.parent)],
        )
        self.assertNotIn("unreviewed-body", json.dumps(report))
        self.write_skill(local, body="modified")
        self.assertIn(
            {"entry": "approved", "status": "content-mismatch"},
            self.audit()["roots"][str(self.hermes)],
        )

    def test_unknown_links_and_private_values_are_not_read_or_printed(self):
        other = self.base / "outside"
        other.mkdir()
        self.write_skill(other, body="OUTSIDE-BODY")
        (self.desktop / "approved").symlink_to(other)
        (self.desktop / "other").symlink_to(other)
        linked_root = self.home / "linked-root"
        linked_root.symlink_to(other)
        (other / "nested.md").write_text("---\nname: OUTSIDE-METADATA\n---\n")
        self.settings["roots"][str(linked_root)] = {}
        self.config.write_text("skills:\n  external_dirs: [/PRIVATE-SOURCE]\n")
        sandbox = self.hermes / "sandbox"
        sandbox.mkdir(mode=0o700)
        private = sandbox / "SKILL.md"
        private.write_text("PRIVATE-BODY")
        private.chmod(0o200)

        report = self.audit(env={**os.environ, "DSH_HOME": "/PRIVATE-ENV"})
        summary = json.dumps(report)
        for value in ("OUTSIDE-BODY", "OUTSIDE-METADATA", "PRIVATE-BODY", "PRIVATE-SOURCE", "PRIVATE-ENV"):
            self.assertNotIn(value, summary)
        self.assertEqual(report["environment-overrides"], ["DSH_HOME"])
        self.assertTrue(report["hermes-config"]["external-roots-configured"])
        self.assertIn({"entry": "sandbox", "status": "private"}, report["roots"][str(self.hermes)])
        self.assertIn({"entry": "approved", "status": "link-mismatch"}, report["roots"][str(self.desktop)])
        self.assertIn({"entry": "other", "status": "uninspected-link"}, report["roots"][str(self.desktop)])
        self.assertEqual(report["roots"][str(linked_root)], [{"entry": ".", "status": "unreadable"}])


if __name__ == "__main__":
    unittest.main()
