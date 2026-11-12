"""Explicit, reversible Wi-Fi identity migration; never reconnect or read secrets."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
MAC = re.compile(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}')
PROPERTIES = (
    '802-11-wireless.cloned-mac-address', 'connection.stable-id',
    'ipv4.dhcp-client-id', 'ipv4.dhcp-iaid', 'ipv6.dhcp-duid', 'ipv6.dhcp-iaid',
    'ipv6.addr-gen-mode', 'ipv6.ip6-privacy',
    'ipv4.dhcp-send-hostname-deprecated', 'ipv6.dhcp-send-hostname-deprecated',
    'ipv4.dhcp-send-hostname', 'ipv6.dhcp-send-hostname',
    'ipv4.dhcp-hostname', 'ipv4.dhcp-fqdn', 'ipv6.dhcp-hostname',
    'ipv4.dhcp-vendor-class-identifier',
)


def unicast(address):
    return bool(MAC.fullmatch(address) and int(address[:2], 16) & 1 == 0
                and address.lower() != '00:00:00:00:00:00')


def validate(settings):
    if not re.fullmatch('[a-zA-Z0-9_.:-]{1,15}', settings['interface']):
        raise ValueError('invalid managed Wi-Fi interface')
    for name, choices in [('wifiDefault', {'preserve', 'permanent', 'random', 'stable', 'stable-ssid'}),
                          ('wired', {'permanent', 'random'})]:
        if settings[name] not in choices and not unicast(settings[name]):
            raise ValueError(f'{name}: expected a unicast MAC or supported policy')
    for uuid, profile in settings['profiles'].items():
        if not UUID.fullmatch(uuid) or set(profile) != {'mode', 'address', 'epoch'}:
            raise ValueError('identity profiles require canonical UUIDs and mode/address/epoch')
        if profile['mode'] not in {'stable', 'random', 'permanent', 'fixed'}:
            raise ValueError('invalid profile identity mode')
        if not re.fullmatch('[a-zA-Z0-9_.-]{1,64}', profile['epoch']):
            raise ValueError('invalid identity epoch')
        if profile['mode'] == 'fixed':
            if not isinstance(profile['address'], str) or not unicast(profile['address']):
                raise ValueError('fixed identities require a nonzero unicast MAC')
        elif profile['address'] is not None:
            raise ValueError('address is only meaningful for fixed mode')


def desired(uuid, profile):
    mode = profile['mode']
    address = (profile['address'] or '').lower()
    seed = f'nixos-lf:link-v1:{uuid}:{mode}:{address}:{profile["epoch"]}:${{DEVICE}}'
    if mode == 'random':
        seed += ':${RANDOM}'
    values = dict.fromkeys(PROPERTIES, '')
    values.update({
        '802-11-wireless.cloned-mac-address': address if mode == 'fixed' else mode,
        'connection.stable-id': seed,
        'ipv4.dhcp-client-id': 'stable', 'ipv4.dhcp-iaid': 'stable',
        'ipv6.dhcp-duid': 'stable-uuid', 'ipv6.dhcp-iaid': 'stable',
        'ipv6.addr-gen-mode': 'stable-privacy', 'ipv6.ip6-privacy': '2',
        'ipv4.dhcp-send-hostname-deprecated': 'no', 'ipv6.dhcp-send-hostname-deprecated': 'no',
        'ipv4.dhcp-send-hostname': '0', 'ipv6.dhcp-send-hostname': '0',
    })
    return values


def canonical(key, value):
    value = value.removesuffix('\n')
    if len(value) > 4096 or any(c in value for c in '\r\n\0'):
        raise ValueError('unsupported multiline or oversized identity property')
    if key in {'ipv6.ip6-privacy', 'ipv4.dhcp-send-hostname', 'ipv6.dhcp-send-hostname'}:
        token = value.split(' ', 1)[0]
        return {'false': '0', 'true': '1', 'default': '-1'}.get(token, token)
    if key == 'ipv6.addr-gen-mode':
        return {'0': 'eui64', '1': 'stable-privacy', '2': 'default-or-eui64',
                '3': 'default'}.get(value.split(' ', 1)[0], value)
    return value


class NetworkManager:
    def __init__(self, settings, run=subprocess.run):
        self.settings, self.run = settings, run

    def command(self, args):
        result = self.run([self.settings['nmcli'], '--colors', 'no', '--wait', '10', *args],
                          text=True, capture_output=True, timeout=15, check=False)
        if result.returncode:
            # Never print nmcli stderr: only the selected identity fields are
            # part of the public result, not arbitrary profile data.
            raise RuntimeError('NetworkManager identity operation failed')
        return result.stdout

    def get(self, uuid, key):
        return canonical(key, self.command(['--escape', 'no', '-g', key,
                                           'connection', 'show', 'uuid', uuid]))

    def inspect(self, uuid):
        if self.get(uuid, 'connection.type') != '802-11-wireless':
            raise ValueError('identity migration requires a Wi-Fi profile')
        if self.get(uuid, 'connection.interface-name') not in {'', self.settings['interface']}:
            raise ValueError('profile is bound to a different interface')
        if self.get(uuid, '802-11-wireless.mode') not in {'', 'infrastructure'}:
            raise ValueError('identity migration only supports Wi-Fi client profiles')
        active = uuid in self.command(['-g', 'UUID', 'connection', 'show', '--active']).splitlines()
        return {'active': active, 'autoconnect': self.get(uuid, 'connection.autoconnect'),
                'properties': {key: self.get(uuid, key) for key in PROPERTIES}}

    def modify(self, uuid, values):
        args = ['connection', 'modify', 'uuid', uuid]
        for key in PROPERTIES:
            args += [key, values[key]]
        # One NetworkManager update includes MAC, DHCP and IPv6 settings.
        self.command(args)


def private_file(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_size > 131072:
            raise ValueError('unsafe identity backup permissions or type')
        value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError('invalid identity backup record')
        return value


def write_backup(path, record):
    if path.exists() or path.is_symlink():
        private_file(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.identity-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(record, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def migration_lock(directory):
    directory.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('identity backup directory must be private and owned by the caller')
    descriptor = os.open(directory / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('unsafe identity lock')
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def change(settings, uuid, restore=False, nm=None):
    if not UUID.fullmatch(uuid):
        raise ValueError('expected a canonical profile UUID')
    nm = nm or NetworkManager(settings)
    directory = Path(settings['stateDirectory'])
    with migration_lock(directory):
        current = nm.inspect(uuid)
        if current['active'] or current['autoconnect'] != 'no':
            raise ValueError('disable autoconnect and disconnect this profile before migration')
        path = directory / (uuid + '.json')
        if restore:
            record = private_file(path)
            if record.get('schema') != 1 or record.get('uuid') != uuid:
                raise ValueError('backup belongs to a different identity')
            for field in ('before', 'applied'):
                if set(record[field]) != set(PROPERTIES) or not all(isinstance(v, str) and len(v) <= 4096
                        and not any(c in v for c in '\r\n\0') for v in record[field].values()):
                    raise ValueError('invalid identity backup fields')
            values = record['before']
            if current['properties'] == values:
                return {'uuid': uuid, 'status': 'already-restored'}
            if current['properties'] != record['applied']:
                raise ValueError('identity fields changed since migration; refusing to overwrite edits')
        else:
            if uuid not in settings['profiles']:
                raise ValueError('profile has no declared identity target')
            values = desired(uuid, settings['profiles'][uuid])
            if current['properties'] == values:
                return {'uuid': uuid, 'status': 'unchanged'}
            write_backup(path, {'schema': 1, 'uuid': uuid, 'before': current['properties'], 'applied': values})
        nm.modify(uuid, values)
        after = nm.inspect(uuid)
        if after['properties'] != values:
            raise RuntimeError('saved identity did not match the requested transaction')
        if after['active']:
            raise RuntimeError('profile activated concurrently; disconnect before reconnecting with the new identity')
        return {'uuid': uuid, 'status': 'restored' if restore else 'applied', 'reconnectRequired': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('settings', type=Path)
    parser.add_argument('command', choices=('check-config', 'plan', 'apply', 'restore'))
    parser.add_argument('uuid', nargs='?')
    args = parser.parse_args()
    try:
        settings = json.loads(args.settings.read_text())
        validate(settings)
        if args.command == 'check-config':
            print('Network identity policies: valid')
            return 0
        if not args.uuid or not UUID.fullmatch(args.uuid):
            raise ValueError('supply a canonical profile UUID')
        if args.command == 'plan':
            if args.uuid not in settings['profiles']:
                raise ValueError('profile has no declared identity target')
            current = NetworkManager(settings).inspect(args.uuid)
            result = {'uuid': args.uuid, 'current': current,
                      'target': desired(args.uuid, settings['profiles'][args.uuid]),
                      'requiresDisconnectedProfile': True, 'requiresAutoconnectDisabled': True}
        else:
            if os.geteuid() != 0:
                raise PermissionError('use sudo network-identity apply|restore UUID')
            result = change(settings, args.uuid, restore=args.command == 'restore')
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'network-identity: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
