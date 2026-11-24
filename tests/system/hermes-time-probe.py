import errno
import json
import os
from pathlib import Path
import socket
import sys


unix_paths = [
    "/run/dbus/system_bus_socket",
    "/run/user/1000/bus",
    "/run/hermes-containers/docker.sock",
    "/run/clash-verge-rev/core/mihomo.sock",
]
host_files = [
    "/home/lfour/.mozilla/firefox/fixture/prefs.js",
    "/var/lib/hermes/.hermes/skills/agently-mail/SKILL.md",
    "/var/lib/hermes/.hermes/.env",
    "/run/secrets/rendered/hermes.env",
    "/var/lib/hermes/workspace/time-host-fixture",
]


def denied(action, errors):
    try:
        action()
    except OSError as exc:
        assert exc.errno in errors, exc
    else:
        raise AssertionError("tool accessed a blocked resource")


def connect_unix(address):
    with socket.socket(socket.AF_UNIX) as client:
        client.connect(address)


def check_environment(names, keys_path):
    keys = json.loads(Path(keys_path).read_text()) + ["DEEPSEEK_API_KEY"]
    assert set(names).isdisjoint(keys)


if sys.argv[1] == "host":
    for path in host_files:
        with open(path, "rb"):
            pass
    for path in unix_paths:
        connect_unix(path)
    with socket.create_connection(("127.0.0.1", 9222), timeout=2):
        pass
    with socket.create_connection(("::1", 9222), timeout=2):
        pass
    connect_unix("\0hermes-time-fixture")
elif sys.argv[1] == "process-env":
    entries = Path("/proc", sys.argv[2], "environ").read_bytes().split(b"\0")
    check_environment((entry.partition(b"=")[0].decode() for entry in entries), sys.argv[3])
else:
    uid = int(sys.argv[1])
    assert os.getresuid() == (uid, uid, uid)
    assert set(os.getgroups()) <= {os.getgid()}
    check_environment(os.environ, sys.argv[2])
    assert os.getcwd() == os.environ["HOME"] == "/work"
    assert Path.cwd().stat().st_uid == uid
    assert Path.cwd().stat().st_mode & 0o777 == 0o700
    Path("write-probe").touch()
    assert os.statvfs("/nix/store").f_flag & os.ST_RDONLY
    for path in host_files:
        denied(lambda: open(path, "rb").close(), {errno.ENOENT, errno.EACCES})
        denied(lambda: os.open(path, os.O_WRONLY), {errno.ENOENT, errno.EACCES, errno.EROFS})
    for path in unix_paths:
        denied(lambda: connect_unix(path), {errno.ENOENT, errno.EACCES})
    denied(lambda: connect_unix("\0hermes-time-fixture"), {errno.ECONNREFUSED})
    for family in (socket.AF_INET, socket.AF_INET6):
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            denied(lambda: socket.socket(family, kind).close(), {errno.EAFNOSUPPORT})
