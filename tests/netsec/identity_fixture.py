"""VM-only DHCP identity observations from real Ethernet/Wi-Fi packets."""
import json
from pathlib import Path
import socket
import struct
import sys


def options(data, wide=False):
    result = {}
    while data:
        if wide:
            if len(data) < 4:
                break
            kind, length = struct.unpack('!HH', data[:4])
            data = data[4:]
        else:
            kind, data = data[0], data[1:]
            if kind == 255:
                break
            if kind == 0:
                continue
            if not data:
                break
            length, data = data[0], data[1:]
        if len(data) < length:
            break
        result[kind], data = data[:length], data[length:]
    return result


def capture(interface, path):
    stream = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    stream.bind((interface, 0))
    path = Path(path)
    path.write_text('')
    path.with_suffix('.ready').touch()
    while True:
        data = stream.recv(65535)
        if len(data) < 54:
            continue
        kind = int.from_bytes(data[12:14], 'big')
        record = {'mac': ':'.join(f'{byte:02x}' for byte in data[6:12])}
        if kind == 0x0800 and data[23] == 17:
            udp = 14 + (data[14] & 15) * 4
            if data[udp:udp + 4] != struct.pack('!HH', 68, 67):
                continue
            bootp = data[udp + 8:]
            if len(bootp) < 240 or bootp[236:240] != b'\x63\x82\x53\x63':
                continue
            fields = options(bootp[240:])
            record.update(family=4, clientid=fields.get(61, b'').hex(),
                          hostname=bool(fields.get(12) or fields.get(81)),
                          message=int.from_bytes(fields.get(53, b'\0'), 'big'))
        elif kind == 0x86dd and data[20] == 17:
            udp = 54
            if data[udp:udp + 4] != struct.pack('!HH', 546, 547):
                continue
            message = data[udp + 8:]
            fields = options(message[4:], wide=True)
            record.update(family=6, clientid=fields.get(1, b'').hex(), hostname=39 in fields,
                          iaid=fields.get(3, b'')[:4].hex(),
                          source_ip=socket.inet_ntop(socket.AF_INET6, data[22:38]),
                          message=message[0])
        else:
            continue
        with path.open('a') as output:
            output.write(json.dumps(record) + '\n')


if __name__ == '__main__':
    capture(*sys.argv[1:])
