"""Safety cases for scripts/push-to-dir.sh against disposable destinations."""

import fcntl
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deploy_support import DeploymentCase, MARKER  # noqa: E402


SCRIPT = Path(sys.argv.pop(1)).resolve()


class SafetyTests(DeploymentCase):
    script = SCRIPT

    @unittest.skipIf(os.geteuid() == 0, "dangerous destinations are exercised unprivileged only")
    def test_dangerous_destinations_rejected(self):
        cases = [
            ("/", "protected"),
            ("/etc", "protected"),
            ("/home", "protected"),
            ("/root", "protected"),
            (str(self.repo), "overlaps the repository"),
            (str(self.repo / "home"), "overlaps the repository"),
            (str(self.root), "overlaps the repository"),
        ]
        for destination, needle in cases:
            with self.subTest(destination=destination):
                result = self.deploy(dest=destination)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(needle, result.stderr)
        self.assertTrue((self.repo / "flake.nix").is_file())
        self.assertTrue((self.repo / "home/config/user.nix").is_file())

    def test_symlinked_destination_and_component_rejected(self):
        real = self.root / "real-destination"
        real.mkdir()
        (real / "keep.txt").write_text("keep\n")
        link = self.root / "destination-link"
        link.symlink_to(real)
        result = self.deploy(dest=link)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr)
        self.assertEqual((real / "keep.txt").read_text(), "keep\n")

        alias = self.root / "destination-alias"
        alias.symlink_to(real)
        result = self.deploy(dest=alias / "nested")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr)
        self.assertEqual((real / "keep.txt").read_text(), "keep\n")
        self.assertFalse((real / "nested").exists())

    def test_concurrent_deployment_lock_rejected(self):
        self.state.mkdir(mode=0o700)
        self.lock.touch(mode=0o600)
        descriptor = os.open(self.lock, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            result = self.deploy()
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("lock", result.stderr.lower())
        self.assertFalse(self.dest.exists())

    def test_unmarked_non_empty_destination_requires_adoption(self):
        self.write(self.dest / "old.txt", "old tree\n")
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--adopt-existing", result.stderr)
        self.assertEqual((self.dest / "old.txt").read_text(), "old tree\n")
        self.assertFalse((self.dest / MARKER).exists())

        result = self.deploy(adopt=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_marker()
        self.assertFalse((self.dest / "old.txt").exists())
        self.assertEqual((self.dest / "flake.nix").read_text(), "flake-v1\n")

    def test_empty_destination_is_accepted(self):
        self.dest.mkdir()
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_marker()

    def test_adoption_outside_expected_destination_requires_test_mode(self):
        self.write(self.dest / "old.txt", "keep")
        result = self.deploy(adopt=True, env={"DEPLOY_TEST_MODE": "0"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.dest / "old.txt").read_text(), "keep")

    def test_nix_referenced_markdown_is_discovered(self):
        self.write(self.repo / "home/config/runtime/guide.md", "required asset")
        self.write(self.repo / "home/config/asset.nix", '{ text = builtins.readFile ./runtime/guide.md; }')
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.dest / "home/config/runtime/guide.md").read_text(), "required asset")

    def test_insufficient_space_leaves_the_old_tree_intact(self):
        self.write_marker()
        self.write(self.dest / "old.txt", "keep")
        tools = self.root / "bin"
        tools.mkdir()
        fake = tools / "df"
        fake.write_text('#!/bin/sh\nprintf "Filesystem Blocks Used Available Capacity Mounted\\nfixture 100 100 0 100%% /\\n"\n')
        fake.chmod(0o755)
        result = self.deploy(env={"PATH": f"{tools}:{os.environ['PATH']}"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not enough free space", result.stderr)
        self.assertEqual((self.dest / "old.txt").read_text(), "keep")

    def test_term_during_install_restores_the_complete_tree(self):
        self.write_marker()
        self.write(self.dest / "old.txt", "keep")
        tools = self.root / "bin"
        tools.mkdir()
        wrapper = tools / "rsync"
        real = shutil.which("rsync")
        fired = self.root / "signal-sent"
        wrapper.write_text(f'''#!{sys.executable}
import os, signal, subprocess, sys
args = sys.argv[1:]
if "--delete-after" in args and args[-1].startswith("/proc/") and not os.path.exists({str(fired)!r}):
    subprocess.run([{real!r}, *args], check=True)
    open({str(fired)!r}, "w").close()
    os.kill(os.getppid(), signal.SIGTERM)
    sys.exit(0)
os.execv({real!r}, [{real!r}, *args])
''')
        wrapper.chmod(0o755)
        result = self.deploy(env={"PATH": f"{tools}:{os.environ['PATH']}"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual({p.name for p in self.dest.iterdir()}, {MARKER, "old.txt"})
        self.assertEqual((self.dest / "old.txt").read_text(), "keep")
        log = next(self.state.glob("backup-*/recovery.log"))
        self.assertIn("status=rolled-back", log.read_text())

    def test_unknown_marker_rejected(self):
        self.write_marker("version=999\n")
        self.write(self.dest / "old.txt", "old tree\n")
        result = self.deploy(adopt=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown deployment marker", result.stderr)
        self.assertEqual((self.dest / "old.txt").read_text(), "old tree\n")

    def test_missing_second_input_leaves_destination_intact(self):
        self.write_marker()
        self.write(self.dest / "old.txt", "old tree\n")
        (self.repo / "flake.lock").unlink()
        result = self.deploy(adopt=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("flake.lock is missing", result.stderr)
        self.assertEqual((self.dest / "old.txt").read_text(), "old tree\n")
        self.assertEqual(list(self.state.glob("staging.*")), [])

    def test_failure_mid_copy_leaves_old_tree_recoverable(self):
        self.write_marker()
        self.write(self.dest / "flake.nix", "flake-old\n")
        self.write(self.dest / "home/config/user.nix", "user-old\n")
        wrapper_dir = self.root / "bin"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "rsync"
        real_rsync = shutil.which("rsync")
        self.assertIsNotNone(real_rsync)
        wrapper.write_text(f"""#!{sys.executable}
import os
import subprocess
import sys
real = {real_rsync!r}
dest = {str(self.dest)!r}
args = sys.argv[1:]
final = "--delete-after" in args
is_dest = args[-1].startswith("/proc/") and args[-1].endswith("/fd/8/")
is_dest = is_dest and not os.path.exists({str(self.root / "failed-once")!r})
if final and is_dest:
    open({str(self.root / "failed-once")!r}, "w").close()
    subprocess.run([real, *args[:-2], "--include=/flake.nix", "--exclude=*", *args[-2:]], check=False)
    sys.exit(1)
os.execv(real, [real, *args])
""")
        wrapper.chmod(0o755)
        result = self.deploy(adopt=True, env={"PATH": f"{wrapper_dir}:{os.environ['PATH']}"})
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("rsync failed", result.stderr)
        self.assertEqual((self.dest / "flake.nix").read_text(), "flake-old\n")
        self.assertEqual((self.dest / "home/config/user.nix").read_text(), "user-old\n")

        backups = list(self.state.glob("backup-*"))
        self.assertEqual(len(backups), 1, backups)
        backup = backups[0]
        self.assertIn("status=rolled-back", (backup / "recovery.log").read_text())
        self.assertEqual((backup / "files/flake.nix").read_text(), "flake-old\n")
        self.assertIn("flake.nix", (backup / "manifest-before.sha256").read_text())

        restore = subprocess.run(
            ["rsync", "-a", f"{backup / 'files'}/", f"{self.dest}/"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(restore.returncode, 0, restore.stderr)
        self.assertEqual((self.dest / "flake.nix").read_text(), "flake-old\n")
        self.assertEqual((self.dest / "home/config/user.nix").read_text(), "user-old\n")

    def test_successful_deployment_backs_up_changed_files(self):
        self.write_marker()
        self.write(self.dest / "flake.nix", "flake-old\n")
        self.write(self.dest / "home/config/user.nix", "user-old\n")
        result = self.deploy(adopt=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_marker()
        backups = list(self.state.glob("backup-*"))
        self.assertEqual(len(backups), 1, backups)
        backup = backups[0]
        self.assertIn("status=success", (backup / "recovery.log").read_text())
        self.assertEqual((backup / "files/flake.nix").read_text(), "flake-old\n")
        self.assertEqual((backup / "files/home/config/user.nix").read_text(), "user-old\n")
        self.assertTrue((backup / "added-by-deployment.txt").is_file())

        restore = subprocess.run(
            ["rsync", "-a", f"{backup / 'files'}/", f"{self.dest}/"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(restore.returncode, 0, restore.stderr)
        self.assertEqual((self.dest / "flake.nix").read_text(), "flake-old\n")
        self.assertEqual((self.dest / "home/config/user.nix").read_text(), "user-old\n")


if __name__ == "__main__":
    unittest.main()
