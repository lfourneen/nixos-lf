"""Real process identities; only explicitly created test PIDs may be stopped."""
import importlib.util
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("gui", sys.argv.pop(1))
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)


class GuiProcessTests(unittest.TestCase):
    def setUp(self):
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)
            if child.stdout:
                child.stdout.close()

    def start(self, name, stubborn=False):
        setup = "signal.signal(signal.SIGTERM, signal.SIG_IGN);" if stubborn else ""
        code = "import signal,time;" + setup + "print('ready',flush=True);time.sleep(30)"
        child = subprocess.Popen([name, "-c", code], executable=sys.executable, stdout=subprocess.PIPE)
        self.children.append(child)
        self.assertEqual(child.stdout.readline(), b"ready\n")
        return child

    def observe(self):
        return [record for child in self.children if (record := gui.process(child.pid)) is not None]

    def test_recognizes_public_and_both_wrapped_gui_names_only(self):
        expected = set()
        for name in ("clash-verge", ".clash-verge-launcher", ".clash-verge-wrapped"):
            expected.add(self.start("/test/" + name).pid)
        for name in ("clash-verge-service", "verge-mihomo", "bash"):
            self.start("/test/" + name)
        self.assertEqual({record["pid"] for record in self.observe()}, expected)
        # Enumerating real /proc must find the same test GUIs without selecting
        # unrelated helpers. We never stop any other user's/session's GUI here.
        actual = {record["pid"] for record in gui.processes()}
        self.assertTrue(expected <= actual)

    def test_prepare_waits_for_old_gui_and_preserves_current_instance(self):
        old = self.start("/nix/store/old/bin/.clash-verge-launcher")
        current_name = "/nix/store/new/bin/clash-verge"
        current = self.start(current_name)
        service = self.start("/test/clash-verge-service")
        gui.prepare(current_name, observe=self.observe, grace=1)
        self.assertEqual(old.wait(timeout=2), -signal.SIGTERM)
        self.assertIsNone(current.poll())
        self.assertIsNone(service.poll())
        gui.prepare(current_name, observe=self.observe, grace=1)
        self.assertIsNone(current.poll())

    def test_stubborn_old_gui_cannot_keep_the_singleton_alive(self):
        old = self.start("/test/.clash-verge-launcher", stubborn=True)
        started = time.monotonic()
        gui.prepare("/test/clash-verge", observe=self.observe, grace=0.1)
        self.assertEqual(old.wait(timeout=2), -signal.SIGKILL)
        self.assertLess(time.monotonic() - started, 3)

    def test_cli_running_and_stop_wait_for_both_gui_formats(self):
        old = self.start("/test/.clash-verge-launcher")
        current = self.start("/test/clash-verge")
        service = self.start("/test/clash-verge-service")
        # Only enumerate this test's children; a manual test run must never
        # stop another GUI belonging to the caller's desktop session.
        with patch.object(gui, "processes", self.observe):
            with patch.object(sys, "argv", ["gui", "--running"]):
                self.assertEqual(gui.main(), 0)
            with patch.object(sys, "argv", ["gui", "--stop"]):
                self.assertEqual(gui.main(), 0)
            with patch.object(sys, "argv", ["gui", "--running"]):
                self.assertEqual(gui.main(), 1)
        self.assertEqual(old.wait(timeout=2), -signal.SIGTERM)
        self.assertEqual(current.wait(timeout=2), -signal.SIGTERM)
        self.assertIsNone(service.poll())

    def test_changed_process_identity_is_not_signaled(self):
        child = self.start("/test/.clash-verge-launcher")
        record = gui.process(child.pid)
        record["start"] += "-different"
        self.assertTrue(gui.stop([record], grace=0))
        self.assertIsNone(child.poll())

    def test_parallel_launches_hold_lock_until_gui_is_observable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = str(root / 'clash-verge')
            count = root / 'normalizations'
            defaults = root / 'defaults'
            defaults.write_text(f"#!{sys.executable}\nimport time\nwith open({str(count)!r}, 'a') as f: f.write('edit\\n')\ntime.sleep(.25)\n")
            defaults.chmod(0o700)
            command = root / 'gui'
            command.write_text(f"#!{sys.executable}\nimport os,sys\nos.execv(sys.executable, [{expected!r}, '-c', 'import time;time.sleep(1.5)'])\n")
            command.chmod(0o700)
            def observe():
                return [record for record in gui.processes() if record['argv0'] == expected]
            results = []
            def run():
                try:
                    results.append(gui.launch(expected, str(command), str(defaults), 'unused', observe=observe))
                except Exception as error:
                    results.append(type(error).__name__)
            with patch.dict(os.environ, {'XDG_STATE_HOME': directory}):
                threads = [threading.Thread(target=run) for _ in range(2)]
                for thread in threads: thread.start()
                for thread in threads: thread.join(timeout=6)
            self.assertEqual(results, [0, 0])
            self.assertEqual(count.read_text(), 'edit\n')


if __name__ == "__main__":
    unittest.main()
