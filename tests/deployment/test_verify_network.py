"""Unit cases for the input manifest and frozen export in verify-network.py."""

import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(sys.argv.pop(1)).resolve()
SPEC = importlib.util.spec_from_file_location("verify_network", SCRIPT)
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class VerifyNetworkInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="verify-inputs-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        files = {
            "flake.nix": "{}\n",
            "flake.lock": '{"nodes": {}}\n',
            ".sops.yaml": "keys: []\n",
            "home/a.nix": "a-v1\n",
            "system/b.py": "b-v1\n",
            "tests/t.py": "t-v1\n",
            "overlays/o.nix": "o-v1\n",
            "local-data/dns-blocklist.txt.xz": "data-v1\n",
            "docs/readme.md": "manual\n",
            "audit/notes.md": "notes\n",
        }
        for name, text in files.items():
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.git("init", "-q")
        self.git("add", ".")
        self.git("commit", "-qm", "initial")

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.email=test@example.invalid",
             "-c", "user.name=Test", *args],
            cwd=self.repo, capture_output=True, text=True, check=True,
        ).stdout

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_manifest_binds_tracked_and_untracked_inputs(self):
        baseline = VERIFY.collect_inputs(self.repo)
        self.assertEqual(baseline["commit"], self.git("rev-parse", "HEAD").strip())
        self.assertEqual(baseline["untracked_build_inputs"], [])

        self.write("home/a.nix", "a-v2\n")
        modified = VERIFY.collect_inputs(self.repo)
        self.assertEqual(modified["commit"], baseline["commit"])
        self.assertNotEqual(modified["diff_sha256"], baseline["diff_sha256"])
        self.assertNotEqual(modified["manifest_sha256"], baseline["manifest_sha256"])
        self.assertNotEqual(modified["input_sha256"], baseline["input_sha256"])

        self.write("home/new.nix", "new\n")
        untracked = VERIFY.collect_inputs(self.repo)
        self.assertEqual(untracked["untracked_build_inputs"], ["home/new.nix"])

        self.write("docs/untracked.md", "outside build inputs\n")
        outside = VERIFY.collect_inputs(self.repo)
        self.assertEqual(outside["untracked_build_inputs"], ["home/new.nix"])

        self.git("add", "home/new.nix")
        self.git("commit", "-qm", "add input")
        committed = VERIFY.collect_inputs(self.repo)
        self.assertNotEqual(committed["commit"], baseline["commit"])
        self.assertEqual(committed["untracked_build_inputs"], [])
        self.assertNotEqual(committed["input_sha256"], baseline["input_sha256"])

    def test_runtime_verdict_requires_success_freshness_boot_and_generation(self):
        runtime, proc = self.root / "run", self.root / "proc"
        (runtime / "netsec").mkdir(parents=True)
        (runtime / "proxy-mode").mkdir()
        (proc / "sys/kernel/random").mkdir(parents=True)
        (proc / "sys/kernel/random/boot_id").write_text("fixture-boot\n")
        (proc / "uptime").write_text("1000 0\n")
        (runtime / "proxy-mode/state.json").write_text('{"policy_version": 7}')
        good = {"result": "ok", "version": 7, "boot_id": "fixture-boot",
                "monotonic_seconds": 999}
        for change in ({}, {"result": "failed"}, {"result": "degraded"},
                       {"version": 6}, {"boot_id": "old"},
                       {"monotonic_seconds": 0}, {"monotonic_seconds": 1001}):
            (runtime / "netsec/state.json").write_text(json.dumps(good | change))
            self.assertEqual(VERIFY.runtime_verification(runtime, proc)["ok"], not change)

    def test_ignored_untracked_build_input_is_detected(self):
        self.write(".gitignore", "local-data/*\n")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "ignore local data")
        self.write("local-data/generated.dat", "generated\n")
        inputs = VERIFY.collect_inputs(self.repo)
        self.assertIn("local-data/generated.dat", inputs["untracked_build_inputs"])

    def test_ephemeral_files_are_not_build_inputs(self):
        self.write("home/__pycache__/module.pyc", "bytecode\n")
        self.write("tests/result-old", "old result\n")
        inputs = VERIFY.collect_inputs(self.repo)
        paths = {entry["path"] for entry in inputs["files"]}
        self.assertNotIn("home/__pycache__/module.pyc", paths)
        self.assertNotIn("tests/result-old", paths)
        self.assertEqual(inputs["untracked_build_inputs"], [])

    def test_frozen_export_reproduces_the_recorded_manifest(self):
        self.write("home/a.nix", "a-v2\n")
        (self.repo / "system/b.py").unlink()
        self.write("home/new.nix", "new\n")
        self.write("docs/untracked.md", "not a build input\n")
        inputs = VERIFY.collect_inputs(self.repo)
        self.assertEqual(inputs["untracked_build_inputs"], ["home/new.nix"])

        frozen = self.root / "frozen"
        frozen.mkdir(mode=0o700)
        VERIFY.export_frozen(self.repo, inputs, frozen)
        self.assertEqual(VERIFY.file_manifest(frozen), inputs["files"])
        self.assertEqual((frozen / "home/a.nix").read_text(), "a-v2\n")
        self.assertFalse((frozen / "system/b.py").exists())
        self.assertEqual((frozen / "home/new.nix").read_text(), "new\n")
        self.assertFalse((frozen / "docs/untracked.md").exists())

    def test_frozen_identity_is_stable_until_the_copy_changes(self):
        inputs = VERIFY.collect_inputs(self.repo)
        frozen = self.root / "frozen"
        frozen.mkdir(mode=0o700)
        VERIFY.export_frozen(self.repo, inputs, frozen)
        identity = VERIFY.frozen_identity(frozen)
        self.assertEqual(identity, VERIFY.frozen_identity(frozen))
        self.assertEqual(identity["input_sha256"], identity["manifest_sha256"])
        with (frozen / "flake.nix").open("a") as handle:
            handle.write("# changed\n")
        self.assertNotEqual(identity, VERIFY.frozen_identity(frozen))

    def test_private_writes_use_owner_only_modes(self):
        path = self.root / "evidence.json"
        VERIFY.write_private(path, b"{}\n")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


class VerifyNetworkRunTests(unittest.TestCase):
    """Run the real script against a throwaway repository and a fake nix."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="verify-run-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.write("flake.nix", "{}\n")
        self.write("flake.lock", '{"nodes": {}}\n')
        self.write(".sops.yaml", "keys: []\n")
        self.write("home/a.nix", "a-v1\n")
        self.write("system/b.py", "b-v1\n")
        self.write("tests/t.py", "t-v1\n")
        self.write("overlays/o.nix", "o-v1\n")
        self.write("local-data/dns-blocklist.txt.xz", "data-v1\n")
        self.write("system/config/network/maintenance/sources.json",
                   '{"components": {"fake": {"checks": ["fake-check"]}}}\n')
        scripts = self.repo / "scripts"
        scripts.mkdir()
        shutil.copyfile(SCRIPT, scripts / "verify-network.py")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("commit", "-qm", "initial")
        self.fakebin = self.root / "fakebin"
        self.fakebin.mkdir()
        self.fake_nix = self.fakebin / "nix"
        self.fake_nix.write_text(f"""#!{sys.executable}
import os
import subprocess
import sys

repo = {str(self.repo)!r}
action = os.environ.get("FAKE_ACTION", "none")
args = sys.argv[1:]
if args and args[0] == "build":
    if action == "modify":
        with open(os.path.join(repo, "home/a.nix"), "a") as handle:
            handle.write("changed\\n")
    elif action == "untracked":
        with open(os.path.join(repo, "home/new.nix"), "w") as handle:
            handle.write("new\\n")
    elif action == "commit":
        subprocess.run(["git", "-C", repo, "-c", "user.email=fake@example.invalid",
                        "-c", "user.name=Fake", "commit", "--allow-empty", "-q", "-m", "change"])
    print("[]")
elif args and args[0] == "eval":
    if "nixosConfigurations" in " ".join(args):
        print('{{"kernel":"k","mihomo":"m","gost":"g","unbound":"u"}}')
    else:
        print('{{"drv":"/nix/store/fake.drv","out":"/nix/store/fake-out"}}')
elif args and args[0] == "path-info":
    sys.exit(1)
elif args and args[0] == "--version":
    print("fake-nix 0.0")
""")
        self.fake_nix.chmod(0o755)

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "user.email=test@example.invalid",
             "-c", "user.name=Test", *args],
            cwd=self.repo, capture_output=True, text=True, check=True,
        ).stdout

    def run_verify(self, action="none", extra=()):
        archive = self.root / f"evidence-{action}-{len(list(self.root.glob('evidence-*')))}"
        env = {
            **os.environ,
            "PATH": f"{self.fakebin}:{os.environ.get('PATH', '')}",
            "FAKE_ACTION": action,
        }
        result = subprocess.run(
            [sys.executable, str(self.repo / "scripts/verify-network.py"),
             "--component", "fake", "--allow-untracked", "--archive", str(archive),
             *extra],
            cwd=self.repo, env=env, capture_output=True, text=True, timeout=120,
        )
        if not (archive / "results.json").is_file():
            self.fail("verify-network produced no evidence: " + result.stdout + result.stderr)
        evidence = json.loads((archive / "results.json").read_text())
        return result, evidence, archive

    def test_passing_run_records_derivation_and_private_archive(self):
        result, evidence, archive = self.run_verify()
        check_log = archive / "fake-check.log"
        detail = check_log.read_text() if check_log.exists() else ""
        self.assertEqual(result.returncode, 0,
                         result.stdout + result.stderr + detail + json.dumps(evidence, indent=2))
        record = evidence["checks"][0]
        self.assertEqual(record["status"], "passed")
        self.assertEqual(record["derivation"], "/nix/store/fake.drv")
        self.assertEqual(record["output_path"], "/nix/store/fake-out")
        self.assertIs(record["output_was_valid_before"], False)
        self.assertEqual(record["build_kind"], "built")
        self.assertEqual(evidence["packages"],
                         {"kernel": "k", "mihomo": "m", "gost": "g", "unbound": "u"})
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((archive / "results.json").stat().st_mode), 0o600)

    def test_changed_file_during_check_fails_as_mixed_input(self):
        result, evidence, _ = self.run_verify(action="modify")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(evidence["input_changed"])
        record = evidence["checks"][0]
        self.assertEqual(record["status"], "input-changed")
        self.assertNotEqual(record["input_before_sha256"], record["input_after_sha256"])

    def test_new_untracked_input_during_check_fails_as_mixed_input(self):
        result, evidence, _ = self.run_verify(action="untracked")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(evidence["input_changed"])
        self.assertEqual(evidence["checks"][0]["status"], "input-changed")

    def test_commit_change_during_check_fails_as_mixed_input(self):
        result, evidence, _ = self.run_verify(action="commit")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(evidence["input_changed"])
        self.assertEqual(evidence["checks"][0]["status"], "input-changed")

    def test_frozen_run_isolated_from_source_changes(self):
        result, evidence, archive = self.run_verify(action="modify", extra=("--frozen",))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(evidence["frozen"])
        self.assertFalse(evidence["input_changed"])
        self.assertTrue(evidence["source_input_changed"])
        self.assertEqual(evidence["checks"][0]["status"], "passed")
        self.assertEqual(evidence["checks"][0]["input_before_sha256"],
                         evidence["checks"][0]["input_after_sha256"])

    def test_untracked_input_rejected_before_checks_unless_allowed(self):
        self.write("home/pre-existing.nix", "pre\n")
        archive = self.root / "evidence-rejected"
        env = {**os.environ,
               "PATH": f"{self.fakebin}:{os.environ.get('PATH', '')}",
               "FAKE_ACTION": "none"}
        result = subprocess.run(
            [sys.executable, str(self.repo / "scripts/verify-network.py"),
             "--component", "fake", "--archive", str(archive)],
            cwd=self.repo, env=env, capture_output=True, text=True, timeout=120,
        )
        self.assertNotEqual(result.returncode, 0)
        evidence = json.loads((archive / "results.json").read_text())
        self.assertEqual(evidence["status"], "rejected")
        self.assertIn("home/pre-existing.nix", evidence["untracked_build_inputs"])
        self.assertEqual(evidence["checks"][0]["status"], "not-run")


if __name__ == "__main__":
    unittest.main()
