"""Resolve the desktop identity declared by the disposable VM."""
import json
from pathlib import Path
import pwd

SETTINGS = json.loads(Path('/etc/netsec-desktop.json').read_text())
USER = pwd.getpwnam(SETTINGS['user'])
HOME = Path(USER.pw_dir)
GUI = Path(SETTINGS['guiData'])
if not GUI.is_relative_to(HOME):
    raise ValueError('VM GUI data must belong to the configured desktop home')
RUNTIME = Path('/run/user') / str(USER.pw_uid)
SOCKET = str(RUNTIME / 'clash-verge-rev/verge-mihomo.sock')
