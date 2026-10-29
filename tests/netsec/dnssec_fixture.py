"""VM-only authoritative answers from a zone signed by the real ldns tools."""

import socketserver
import struct
import sys
import threading

import dns.flags
import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
import dns.zone


zone = dns.zone.from_file(sys.argv[1], origin="signed.fixture.example.", relativize=False)


def answer(packet):
    request = dns.message.from_wire(packet)
    response = dns.message.make_response(request)
    response.flags |= dns.flags.AA
    for question in request.question:
        data = zone.get_rdataset(question.name, question.rdtype)
        if data is None:
            response.set_rcode(dns.rcode.NXDOMAIN)
            continue
        response.answer.append(dns.rrset.from_rdata_list(question.name, data.ttl, list(data)))
        signature = zone.get_rdataset(question.name, dns.rdatatype.RRSIG, question.rdtype)
        if signature is not None:
            response.answer.append(dns.rrset.from_rdata_list(question.name, signature.ttl, list(signature)))
    return response.to_wire()


def receive(stream, length):
    result = b""
    while len(result) < length:
        data = stream.recv(length - len(result))
        if not data:
            return b""
        result += data
    return result


class UDP(socketserver.BaseRequestHandler):
    def handle(self):
        data, stream = self.request
        stream.sendto(answer(data), self.client_address)


class TCP(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(5)
        while header := receive(self.request, 2):
            packet = receive(self.request, struct.unpack("!H", header)[0])
            if not packet:
                return
            result = answer(packet)
            self.request.sendall(struct.pack("!H", len(result)) + result)


with socketserver.ThreadingTCPServer(("127.0.0.1", 15356), TCP) as tcp:
    tcp.daemon_threads = True
    threading.Thread(target=tcp.serve_forever, daemon=True).start()
    with socketserver.ThreadingUDPServer(("127.0.0.1", 15356), UDP) as udp:
        udp.daemon_threads = True
        udp.serve_forever()
