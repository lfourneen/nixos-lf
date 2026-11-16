"""Unit tests for the root-only Age key bootstrap helper."""

import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/system-age-key.py"

sys.path.insert(0, str(SCRIPT.parent))
import importlib.util

spec = importlib.util.spec_from_file_location("system_age_key", SCRIPT)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class AgeKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="age-key-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_creates_private_key_and_reports_only_public_recipient(self):
        key, recipient = helper.create_key(
            self.root / "age", "identity.txt", allow_unprivileged=True
        )
        self.assertTrue(recipient.startswith("age1"))
        self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
        self.assertNotIn("AGE-SECRET-KEY-1", recipient)
        self.assertIn("AGE-SECRET-KEY-1", key.read_text())

    def test_refuses_to_overwrite_an_existing_key(self):
        directory = self.root / "age"
        helper.create_key(directory, "identity.txt", allow_unprivileged=True)
        with self.assertRaises(helper.KeyError_):
            helper.create_key(directory, "identity.txt", allow_unprivileged=True)

    def test_refuses_group_or_world_writable_directory(self):
        directory = self.root / "age"
        directory.mkdir(mode=0o777)
        os.chmod(directory, 0o777)
        with self.assertRaises(helper.KeyError_):
            helper.create_key(directory, "identity.txt", allow_unprivileged=True)

    def test_refuses_symlinked_directory_component(self):
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        link.symlink_to(real)
        with self.assertRaises(helper.KeyError_):
            helper.create_key(link / "age", "identity.txt", allow_unprivileged=True)

    def test_print_recipient_never_prints_private_material(self):
        directory = self.root / "age"
        _, recipient = helper.create_key(directory, "identity.txt", allow_unprivileged=True)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--directory", str(directory),
             "--key-name", "identity.txt", "--print-recipient"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), recipient)
        self.assertNotIn("AGE-SECRET-KEY-1", result.stdout)


    def test_adopt_existing_identity_keeps_the_recipient(self):
        source_dir = self.root / "desktop"
        source, recipient = helper.create_key(
            source_dir, "keys.txt", allow_unprivileged=True
        )
        target_dir = self.root / "system"
        target, adopted_recipient, adopted_from = helper.adopt_identity(
            source, target_dir, "system.key", allow_unprivileged=True
        )
        self.assertEqual(adopted_recipient, recipient)
        self.assertEqual(adopted_from, source)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o600)


    def test_adopt_creates_missing_private_parent_directories(self):
        source, _ = helper.create_key(
            self.root / "desktop", "keys.txt", allow_unprivileged=True
        )
        target_dir = self.root / "secrets" / "age"
        target, _, _ = helper.adopt_identity(
            source, target_dir, "system.key", allow_unprivileged=True
        )
        self.assertTrue(target.is_file())
        self.assertEqual(stat.S_IMODE(target_dir.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((self.root / "secrets").stat().st_mode), 0o700
        )


    def test_adopt_refuses_an_unexpanded_shell_path(self):
        with self.assertRaises(helper.KeyError_):
            helper.adopt_identity(
                "$HOME/.config/age.txt",
                self.root / "system",
                "identity.txt",
                allow_unprivileged=True,
            )

    def test_adopt_refuses_an_existing_target(self):
        source, _ = helper.create_key(
            self.root / "desktop", "keys.txt", allow_unprivileged=True
        )
        target_dir = self.root / "system"
        helper.adopt_identity(source, target_dir, "system.key", allow_unprivileged=True)
        with self.assertRaises(helper.KeyError_):
            helper.adopt_identity(source, target_dir, "system.key", allow_unprivileged=True)

    def test_adopt_refuses_a_group_readable_source(self):
        source, _ = helper.create_key(
            self.root / "desktop", "keys.txt", allow_unprivileged=True
        )
        os.chmod(source, 0o644)
        with self.assertRaises(helper.KeyError_):
            helper.adopt_identity(
                source, self.root / "system", "system.key", allow_unprivileged=True
            )

    def test_adopt_refuses_a_symlink_source(self):
        source, _ = helper.create_key(
            self.root / "desktop", "keys.txt", allow_unprivileged=True
        )
        link = self.root / "link.txt"
        link.symlink_to(source)
        with self.assertRaises(helper.KeyError_):
            helper.adopt_identity(
                link, self.root / "system", "system.key", allow_unprivileged=True
            )

    def test_cli_adopt_reports_the_recipient_without_private_material(self):
        source, recipient = helper.create_key(
            self.root / "desktop", "keys.txt", allow_unprivileged=True
        )
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--adopt", str(source),
             "--directory", str(self.root / "system"),
             "--allow-unprivileged"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(recipient, result.stdout)
        self.assertIn("no .sops.yaml edit", result.stdout)
        self.assertNotIn("AGE-SECRET-KEY-1", result.stdout)
        self.assertNotIn("AGE-SECRET-KEY-1", result.stderr)

    def test_print_recipient_works_for_an_identity_without_comments(self):
        source, recipient = helper.create_key(
            self.root / "desktop", "keys.txt", allow_unprivileged=True
        )
        bare = self.root / "bare.txt"
        identity_line = next(
            line for line in source.read_text().splitlines()
            if line.startswith("AGE-SECRET-KEY-1")
        )
        bare.write_text(identity_line + "\n")
        os.chmod(bare, 0o600)
        target, adopted_recipient, _ = helper.adopt_identity(
            bare, self.root / "system", "system.key", allow_unprivileged=True
        )
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--directory", str(self.root / "system"),
             "--key-name", "system.key", "--print-recipient"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), recipient)


    def test_add_sops_recipient_updates_only_matching_rules(self):
        config = self.root / ".sops.yaml"
        config.write_text(
            "creation_rules:\n"
            "  - path_regex: (secrets|soul|skill|proxy)\\.yaml$\n"
            "    key_groups:\n"
            "      - age:\n"
            "          - \"age1old\"\n"
            "  - path_regex: personal\\.yaml$\n"
            "    key_groups:\n"
            "      - age:\n"
            "          - \"age1personal\"\n"
        )
        os.chmod(config, 0o644)
        recipient = "age1newrecipient"
        changed, backup = helper.add_sops_recipient(config, recipient)
        self.assertTrue(changed)
        text = config.read_text()
        self.assertIn(f'- "{recipient}"', text)
        self.assertEqual(text.count(recipient), 1)
        self.assertEqual(text.count("age1personal"), 1)
        self.assertIsNotNone(backup)
        self.assertTrue(backup.is_file())
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        changed_again, backup_again = helper.add_sops_recipient(config, recipient)
        self.assertFalse(changed_again)
        self.assertIsNone(backup_again)

    def test_add_sops_recipient_refuses_a_config_without_a_matching_rule(self):
        config = self.root / ".sops.yaml"
        config.write_text(
            "creation_rules:\n"
            "  - path_regex: personal\\.yaml$\n"
            "    key_groups:\n"
            "      - age:\n"
            "          - \"age1personal\"\n"
        )
        with self.assertRaises(helper.KeyError_):
            helper.add_sops_recipient(config, "age1newrecipient")

    def test_cli_generate_can_write_the_sops_config(self):
        config = self.root / ".sops.yaml"
        config.write_text(
            "creation_rules:\n"
            "  - path_regex: (secrets|soul|skill|proxy)\\.yaml$\n"
            "    key_groups:\n"
            "      - age:\n"
            "          - \"age1old\"\n"
        )
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--generate",
             "--write-sops-config", "--sops-config", str(config),
             "--directory", str(self.root / "system"),
             "--key-name", "identity.txt", "--allow-unprivileged"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        recipient = next(
            line.split("public recipient: ", 1)[1]
            for line in result.stdout.splitlines()
            if line.startswith("public recipient: ")
        )
        text = config.read_text()
        self.assertIn("added the recipient", result.stdout)
        self.assertEqual(text.count(f'- "{recipient}"'), 1)
        self.assertEqual(text.count("age1old"), 1)
        self.assertNotIn("AGE-SECRET-KEY-1", result.stdout)

    def test_requires_root_without_explicit_override(self):
        if os.geteuid() == 0:
            self.skipTest("test intended for an unprivileged runner")
        directory = self.root / "age"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--generate", "--directory", str(directory)],
            capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("run as root", result.stderr)
        self.assertFalse(directory.exists())


if __name__ == "__main__":
    unittest.main()
