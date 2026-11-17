"""Exercise the launch-boundary system-proxy write interception.

This never launches the GUI and never changes the caller's real proxy setting.
It checks the command shims on a temporary PATH, the absolute-path mount mask in
a private namespace, and the memory-backend fallback for the absolute gsettings
path that PATH cannot replace.
"""
from importlib.machinery import SourceFileLoader
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ARGS = sys.argv[1:]
sys.argv = sys.argv[:1]
GUI = Path(ARGS[0]).resolve()
SHIM = Path(ARGS[1]).resolve()
CONTROL_PATH = Path(ARGS[2]).resolve()
GSETTINGS = Path(ARGS[3]).resolve()
DCONF = Path(ARGS[4]).resolve()
KREADCONFIG = Path(ARGS[5]).resolve()
SCHEMA_SHARE = Path(ARGS[6]).resolve()
BWRAP = Path(ARGS[7]).resolve()


def load(name, path):
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


gui = load("gui", GUI)


class GuiControlTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="gui-controls-"))
        self.log = self.root / "calls.log"
        self.real = {}
        for name, output in {
            "gsettings": "'none'",
            "dconf": "none",
            "kreadconfig": "0",
            "kwriteconfig": "",
        }.items():
            path = self.root / name
            path.write_text(
                "#!/bin/sh\n"
                f"printf '%s %s\\n' \"$0\" \"$*\" >> {str(self.log)!r}\n"
                + (f"printf '%s\\n' {output!r}\n" if output else "")
            )
            path.chmod(0o700)
            self.real[name] = path
        self.env = dict(os.environ)
        self.env.update({
            "LF_GUI_REAL_GSETTINGS": str(self.real["gsettings"]),
            "LF_GUI_REAL_DCONF": str(self.real["dconf"]),
            "LF_GUI_REAL_KREADCONFIG": str(self.real["kreadconfig"]),
            "LF_GUI_REAL_KWRITECONFIG": str(self.real["kwriteconfig"]),
        })

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_shim(self, name, *args, env=None):
        return subprocess.run([str(CONTROL_PATH / name), *args], env=env or self.env,
                              capture_output=True, text=True, timeout=10)

    def calls(self):
        return self.log.read_text() if self.log.exists() else ""

    def test_writes_are_silent_and_reads_are_forwarded(self):
        for name, command, args in [
            ("gsettings", "set", ("org.gnome.system.proxy", "mode", "'manual'")),
            ("gsettings", "reset", ("org.gnome.system.proxy", "mode")),
            ("dconf", "write", ("/system/proxy/mode", "'manual'")),
            ("dconf", "reset", ("/system/proxy/mode",)),
            ("gsettings", "future-write", ("org.gnome.system.proxy",)),
            ("dconf", "future-write", ("/system/proxy/",)),
            ("kwriteconfig5", "write", ("--file", "kioslaverc", "--group", "G", "--key", "K", "V")),
            ("kwriteconfig6", "write", ("--file", "kioslaverc", "--group", "G", "--key", "K", "V")),
        ]:
            result = self.run_shim(name, command, *args)
            self.assertEqual(result.returncode, 0, (name, command, result.stderr))
        self.assertEqual(self.calls(), "", "a blocked write reached the real helper")

        for name, args in [
            ("gsettings", ("get", "org.gnome.system.proxy", "mode")),
            ("dconf", ("read", "/system/proxy/mode")),
            ("kreadconfig5", ("--file", "kioslaverc", "--group", "G", "--key", "K")),
            ("kreadconfig6", ("--file", "kioslaverc", "--group", "G", "--key", "K")),
        ]:
            result = self.run_shim(name, *args)
            self.assertEqual(result.returncode, 0, (name, result.stderr))
        self.assertEqual(len(self.calls().splitlines()), 4, self.calls())

    def test_gsettings_read_drops_the_memory_backend(self):
        record = self.root / "backend"
        real = self.root / "gsettings-backend"
        real.write_text(
            "#!/bin/sh\n"
            f"printf '%s' \"${{GSETTINGS_BACKEND-unset}}\" > {str(record)!r}\n"
            "printf \"'none'\\n\"\n"
        )
        real.chmod(0o700)
        env = dict(self.env, LF_GUI_REAL_GSETTINGS=str(real), GSETTINGS_BACKEND="memory")
        result = self.run_shim("gsettings", "get", "org.gnome.system.proxy", "mode", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(record.read_text(), "unset")

    def test_environment_installs_path_shims_without_changing_desktop_backends(self):
        env = {
            "PATH": "/usr/bin",
            "LF_GUI_CONTROL_SHIM": str(SHIM),
            "LF_GUI_CONTROL_PATH": str(CONTROL_PATH),
        }
        actual = gui.control_environment(env)
        self.assertEqual(actual["PATH"], str(CONTROL_PATH) + os.pathsep + "/usr/bin")
        self.assertNotIn("GSETTINGS_BACKEND", actual)
        self.assertNotIn("KDE_SESSION_VERSION", actual)

    def test_missing_namespace_falls_back_to_memory_backend(self):
        env = gui.control_environment({
            "PATH": os.environ.get("PATH", ""),
            "LF_GUI_CONTROL_SHIM": str(SHIM),
            "LF_GUI_CONTROL_PATH": str(CONTROL_PATH),
            "LF_GUI_BWRAP": str(self.root / "missing-bwrap"),
            "LF_GUI_CONTROL_MASKS": "[]",
        })
        argv, directory = gui.control_argv("/bin/true", env)
        self.assertEqual(argv, ["/bin/true"])
        self.assertIsNone(directory)
        self.assertEqual(env["GSETTINGS_BACKEND"], "memory")
        self.assertEqual(env["KDE_SESSION_VERSION"], "5")

    def test_mount_mask_hides_the_absolute_helper(self):
        target = self.root / "gsettings"
        target.write_text("#!/bin/sh\nprintf 'REAL\\n'\n")
        target.chmod(0o700)
        env = gui.control_environment({
            "PATH": os.environ.get("PATH", ""),
            "LF_GUI_CONTROL_SHIM": str(SHIM),
            "LF_GUI_CONTROL_PATH": str(CONTROL_PATH),
            "LF_GUI_BWRAP": str(BWRAP),
            "XDG_RUNTIME_DIR": str(self.root),
            "LF_GUI_CONTROL_MASKS": json.dumps([
                {"target": str(target), "real": str(target),
                 "variable": "LF_GUI_REAL_GSETTINGS"},
            ]),
        })
        argv, directory = gui.control_argv(str(target), env)
        try:
            if directory is None:
                self.skipTest("private mount namespace is unavailable")
            write = subprocess.run([*argv, "set", "org.gnome.system.proxy", "mode", "'manual'"],
                                   env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(write.returncode, 0, write.stderr)
            self.assertEqual(write.stdout, "", "the mask did not replace the real helper")
            read = subprocess.run([*argv, "get", "org.gnome.system.proxy", "mode"],
                                  env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(read.returncode, 0, read.stderr)
            self.assertEqual(read.stdout, "REAL\n")
        finally:
            if directory is not None:
                shutil.rmtree(directory, ignore_errors=True)

    def test_absolute_gsettings_memory_backend_does_not_persist(self):
        schemas = list(SCHEMA_SHARE.rglob("glib-2.0/schemas"))
        if not schemas:
            self.skipTest("desktop proxy schema is unavailable")
        env = dict(os.environ, GSETTINGS_SCHEMA_DIR=str(schemas[0]))
        before = subprocess.run([str(GSETTINGS), "get", "org.gnome.system.proxy", "mode"],
                                env=env, capture_output=True, text=True, timeout=10)
        if before.returncode:
            self.skipTest("gsettings cannot read the desktop proxy schema")
        write_env = dict(env, GSETTINGS_BACKEND="memory")
        write = subprocess.run([str(GSETTINGS), "set", "org.gnome.system.proxy", "mode", "'manual'"],
                               env=write_env, capture_output=True, text=True, timeout=10)
        self.assertEqual(write.returncode, 0, write.stderr)
        after = subprocess.run([str(GSETTINGS), "get", "org.gnome.system.proxy", "mode"],
                               env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(after.returncode, 0, after.stderr)
        self.assertEqual(after.stdout, before.stdout)


if __name__ == "__main__":
    unittest.main()
