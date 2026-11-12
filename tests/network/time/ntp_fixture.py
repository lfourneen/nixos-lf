"""VM-only process-scoped NTP egress check against the local peer server."""

import os
import socket
import struct
import sys
import time

os.setuid(int(sys.argv[1]))
stamp = struct.pack('!II', int(time.time()) + 2208988800, 0x12345678)
packet = b'\x23' + b'\0' * 39 + stamp
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
    stream.settimeout(2)
    stream.sendto(packet, ('203.0.113.10', 123))
    response = stream.recv(4096)
    assert len(response) >= 48 and response[0] & 7 == 4 and response[24:32] == stamp
