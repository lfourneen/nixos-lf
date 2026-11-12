"""Apply DNS policy to the current managed Wi-Fi profile; never read its secrets."""

import json
import fcntl
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

PROPERTIES = ("ipv4.ignore-auto-dns", "ipv6.ignore-auto-dns", "ipv4.dns", "ipv6.dns",
              "ipv4.dns-search", "ipv6.dns-search")
MANAGED = ["yes", "yes", "", "", "", ""]


def record_path(settings, uuid):
    if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', uuid):
        raise ValueError('invalid profile UUID')
    directory = Path(settings['state'])
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('unsafe DNS backup directory')
    return directory / (uuid + '.json')


def load_record(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > 32768:
            raise ValueError('unsafe DNS backup file')
        value = json.load(stream)
    if set(value) != {'original', 'managed'} or value['managed'] != MANAGED or \
            not isinstance(value['original'], list) or len(value['original']) != len(PROPERTIES) or \
            not all(isinstance(v, str) and len(v) <= 4096 and not any(c in v for c in '\r\n\0')
                    for v in value['original']):
        raise ValueError('invalid DNS backup')
    return value


def snapshot(command, settings, uuid):
    text = command([settings['nmcli'], '--escape', 'no', '-g', ','.join(PROPERTIES),
                    'connection', 'show', 'uuid', uuid])
    values = text.split('\n')
    values += [''] * (len(PROPERTIES) - len(values))
    if len(values) != len(PROPERTIES):
        raise ValueError('invalid DNS property response')
    values[:2] = [{'true': 'yes', 'false': 'no'}.get(v, v) for v in values[:2]]
    return values


def modify(command, settings, uuid, values):
    args = [settings['nmcli'], '--wait', '5', 'connection', 'modify', 'uuid', uuid]
    for key, value in zip(PROPERTIES, values):
        args += [key, value]
    command(args)


def profile(settings, uuid, action, command):
    path = record_path(settings, uuid)
    # Dispatcher and operator restoration share one lock and the same whitelist.
    with (path.parent / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = snapshot(command, settings, uuid)
        record = load_record(path)
        if action == 'plan':
            return {'backup': record is not None, 'managed': current == MANAGED,
                    'restorable': bool(record and current in (MANAGED, record['original'])),
                    'fields': list(PROPERTIES)}
        if action == 'restore':
            if record is None:
                raise ValueError('no DNS backup for this profile')
            if current not in (MANAGED, record['original']):
                raise ValueError('DNS properties changed manually; refusing to overwrite')
            if current != record['original']:
                modify(command, settings, uuid, record['original'])
            return
        if current == MANAGED:
            return
        if record and current != record['original']:
            raise ValueError('DNS properties changed manually; review with proxy-dns-profile plan')
        if record is None:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump({'original': current, 'managed': MANAGED}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        modify(command, settings, uuid, MANAGED)


def apply(settings, interface, action, uuid, run=subprocess.run):
    if interface != settings["interface"]:
        return
    if action not in {"pre-up", "pre-down", "down", "up", "dhcp4-change", "dhcp6-change"}:
        return

    def command(argv, required=True):
        result = run(argv, text=True, capture_output=True, timeout=8, check=False)
        if required and result.returncode:
            raise RuntimeError("DNS policy command failed: " + argv[0].rsplit("/", 1)[-1])
        return result.stdout.strip()

    if settings['exceptions'] and action in {'pre-up', 'pre-down', 'down'}:
        command([settings['systemctl'], 'start', 'dns-split-revoke.service'])
    if action in {'pre-down', 'down'} or not uuid:
        return

    exempt = uuid in settings["exceptions"]
    if 'state' in settings:
        if exempt:
            if load_record(record_path(settings, uuid)):
                profile(settings, uuid, 'restore', command)
        else:
            profile(settings, uuid, 'apply', command)
    routing_domains = ["~."]
    if exempt:
        # Preserve explicit profile data for dnsmasq's conditional forwarding,
        # but route resolved itself through the local gateway in both families.
        search = command([settings["nmcli"], "-g", "ipv4.dns-search,ipv6.dns-search", "connection", "show", "uuid", uuid])
        routing_domains += [value for value in re.split(r"[,\s]+", search)
                            if re.fullmatch(r"~?[a-zA-Z0-9][a-zA-Z0-9.-]*[a-zA-Z0-9]", value)]
    properties = ["ipv4.ignore-auto-dns", "ipv6.ignore-auto-dns", "ipv4.dns", "ipv6.dns",
                  "ipv4.dns-search", "ipv6.dns-search"]
    current = command([settings["nmcli"], "-g", ",".join(properties), "connection", "show", "uuid", uuid])
    values = current.split("\n")
    compliant = len(values) >= 2 and all(v in {"yes", "true"} for v in values[:2]) \
        and not any(v for v in values[2:])
    if not exempt and not compliant and 'state' not in settings:
        argv = [settings["nmcli"], "--wait", "5", "connection", "modify", "uuid", uuid]
        for key in properties:
            argv += [key, "yes" if key.endswith("ignore-auto-dns") else ""]
        command(argv)
    # pre-up can be too early for Device.Reapply; up always reapplies the now
    # compliant profile, including one modified at pre-up in this activation.
    if action != "pre-up":
        command([settings["nmcli"], "--wait", "5", "device", "reapply", interface])
    command([settings["resolvectl"], "dns", interface, settings["resolver"]])
    command([settings["resolvectl"], "domain", interface, *routing_domains])
    command([settings["resolvectl"], "llmnr", interface, "no"])


def main():
    with open(sys.argv[1]) as stream:
        settings = json.load(stream)
    try:
        if sys.argv[2] in {'plan', 'restore'}:
            if os.geteuid() != 0:
                raise ValueError('DNS backup management requires root')
            def command(argv):
                result = subprocess.run(argv, text=True, capture_output=True, timeout=8, check=False)
                if result.returncode:
                    raise RuntimeError('NetworkManager DNS operation failed')
                return result.stdout.rstrip('\n')
            result = profile(settings, sys.argv[3], sys.argv[2], command)
            print(json.dumps(result or {'restored': True}))
            return 0
        apply(settings, sys.argv[2], sys.argv[3], os.environ.get("CONNECTION_UUID", ""))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"nm-dns-policy: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
