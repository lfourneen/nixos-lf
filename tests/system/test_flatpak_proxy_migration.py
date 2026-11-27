import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("migration", sys.argv.pop(1))
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

PROXY = "http://127.0.0.1:33332"
LEGACY = dict.fromkeys(("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"), PROXY)
LEGACY["NO_PROXY"] = "127.0.0.1,localhost,::1,192.168.1.1,*.local"


class FlatpakProxyMigrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = self.root / "store"
        self.profiles = self.root / "profiles"
        self.current = self.root / "current-system"
        self.record = self.root / "gcroots/flatpak-state.json"
        self.overrides = self.root / "data/flatpak/overrides"
        self.path = self.overrides / "global"
        for path in (self.store, self.profiles, self.record.parent, self.overrides):
            path.mkdir(parents=True)
        self.owner = os.geteuid()

    def state(self, slot, environment):
        path = self.store / (slot * 32 + "-flatpak-state.json")
        path.write_text(json.dumps({
            "version": 2,
            "overrides": {"settings": {"global": {"Environment": environment}}},
        }))
        path.chmod(0o444)
        return path

    def recorded(self, slot, environment):
        self.record.unlink(missing_ok=True)
        self.record.symlink_to(self.state(slot, environment))

    def deployed(self, number, slot, state, reference_only=False):
        generation = self.store / (slot * 32 + "-nixos-system-fixture")
        unit = generation / "etc/systemd/system/flatpak-managed-install.service"
        unit.parent.mkdir(parents=True)
        installer = self.store / (slot * 32 + "-flatpak-managed-install")
        command = "NEW_STATE=$(" + str(self.store / ("p" * 32 + "-coreutils/bin/cat")) + " " + str(state) + ")"
        if reference_only:
            command = "# " + command
        installer.write_text("#!/bin/bash\n" + command + "\n")
        installer.chmod(0o555)
        unit.write_text("[Service]\nExecStart=" + str(installer) + "\n")
        unit.chmod(0o444)
        (self.profiles / f"system-{number}-link").symlink_to(generation)
        return generation

    def apply(self):
        migration.migrate(self.path, self.record, self.profiles, self.current, self.store, LEGACY, self.owner)

    def test_system_override_matches_exact_owned_values_and_preserves_other_keys(self):
        self.recorded("a", LEGACY)
        instance = self.overrides.parent
        env = dict(os.environ, XDG_DATA_HOME=str(instance.parent), FLATPAK_SYSTEM_DIR=str(instance),
                   XDG_CACHE_HOME=str(self.root / "cache"), XDG_CONFIG_HOME=str(self.root / "config"))

        def flatpak(*args):
            return subprocess.run(["flatpak", "override", *args], env=env, text=True,
                                  capture_output=True, check=True).stdout

        # --user lets the unprivileged Nix builder use the real Flatpak
        # serializer. Then inspect the same tree as a system installation.
        initial = dict(LEGACY, https_proxy="http://manual.invalid:8080", QT_THEME="custom")
        flatpak("--user", *(f"--env={key}={value}" for key, value in initial.items()))
        flatpak("--user", "--env=HTTP_PROXY=http://app.invalid:8080", "org.example.ProxyFixture")
        self.assertIn(f"HTTP_PROXY={PROXY}", flatpak("--system", "--show"))
        self.path.chmod(0o640)
        self.apply()
        result = flatpak("--system", "--show")
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "NO_PROXY"):
            self.assertNotIn(f"\n{key}=", result)
        self.assertIn("https_proxy=http://manual.invalid:8080", result)
        self.assertIn("QT_THEME=custom", result)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertIn("HTTP_PROXY=http://app.invalid:8080", flatpak("--system", "--show", "org.example.ProxyFixture"))

    def test_repair_after_transparent_install_uses_deployed_provenance_once(self):
        self.recorded("a", {"GTK_THEME": "Adwaita"})
        legacy = self.state("b", LEGACY)
        self.deployed(6, "b", legacy)
        current = self.deployed(7, "a", self.record.readlink())
        # The newest profile may have been prepared for boot but never activated.
        self.deployed(8, "c", self.state("c", {"HTTP_PROXY": "http://other.invalid"}))
        self.current.symlink_to(current)
        original = f"[Environment]\nHTTP_PROXY={PROXY}\n"
        self.path.write_text(original)
        self.apply()
        self.assertEqual(self.path.read_text(), "[Environment]\n")
        self.path.write_text(original)  # an operator adds the same value later
        self.apply()
        self.assertEqual(self.path.read_text(), original)

        self.recorded("d", LEGACY)  # a new explicit -> transparent cycle
        self.apply()
        self.assertEqual(self.path.read_text(), "[Environment]\n")

    def test_state_values_or_unreferenced_store_artifacts_do_not_grant_ownership(self):
        self.recorded("a", {"GTK_THEME": "Adwaita"})
        legacy = self.state("b", LEGACY)
        self.deployed(6, "b", legacy, reference_only=True)
        current = self.deployed(7, "a", self.record.readlink())
        self.current.symlink_to(current)
        original = f"[Environment]\nHTTP_PROXY={PROXY}\nOTHER=quoted%literal\n"
        self.path.write_text(original)
        self.apply()
        self.assertEqual(self.path.read_text(), original)
        self.assertFalse((self.overrides.parent / migration.RECEIPT).exists())

    def test_only_latest_explicit_declaration_is_considered(self):
        self.recorded("a", {"GTK_THEME": "Adwaita"})
        self.deployed(5, "b", self.state("b", LEGACY))
        self.deployed(6, "c", self.state("c", {"HTTPS_PROXY": PROXY}))
        self.current.symlink_to(self.deployed(7, "a", self.record.readlink()))
        self.path.write_bytes(f"# keep formatting\r\n[Environment]\r\nHTTP_PROXY={PROXY}\r\nHTTPS_PROXY={PROXY}\r\n".encode())
        self.apply()
        self.assertEqual(self.path.read_bytes(), f"# keep formatting\r\n[Environment]\r\nHTTP_PROXY={PROXY}\r\n".encode())


if __name__ == "__main__":
    unittest.main()
