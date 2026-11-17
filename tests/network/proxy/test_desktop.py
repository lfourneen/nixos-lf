"""Keep VM desktop paths independent of the test runner's root home."""
import importlib.util
import json
import os
from pathlib import Path
import pwd
import sys
import unittest
from unittest.mock import patch

SOURCE = Path(sys.argv.pop(1))


class DesktopTests(unittest.TestCase):
    def load(self, user, gui):
        spec = importlib.util.spec_from_file_location('desktop_fixture', SOURCE)
        module = importlib.util.module_from_spec(spec)
        payload = json.dumps({'user': user.pw_name, 'guiData': gui})
        with patch.object(Path, 'read_text', return_value=payload), \
                patch.object(pwd, 'getpwnam', return_value=user), \
                patch.dict(os.environ, {'HOME': '/root', 'XDG_RUNTIME_DIR': '/run/user/0'}):
            spec.loader.exec_module(module)
        return module

    def test_renamed_user_and_custom_home_ignore_root_environment(self):
        user = pwd.struct_passwd(('fixture-user', 'x', 1107, 117, '', '/srv/custom-home', '/bin/sh'))
        data = '/srv/custom-home/.local/share/io.github.clash-verge-rev.clash-verge-rev'
        module = self.load(user, data)
        self.assertEqual(module.USER, user)
        self.assertEqual(module.HOME, Path(user.pw_dir))
        self.assertEqual(module.GUI, Path(data))
        self.assertEqual(module.SOCKET, '/run/user/1107/clash-verge-rev/verge-mihomo.sock')

    def test_unrelated_gui_home_is_refused(self):
        user = pwd.struct_passwd(('fixture-user', 'x', 1107, 117, '', '/srv/custom-home', '/bin/sh'))
        with self.assertRaisesRegex(ValueError, 'configured desktop home'):
            self.load(user, '/root/.local/share/gui')


unittest.main(verbosity=2)
