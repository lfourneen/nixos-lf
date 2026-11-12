"""VM-only authoritative answers from a zone signed by the real ldns tools."""

import socketserver
import socket
import os
import ssl
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


def answer(packet, tcp=False):
    request = dns.message.from_wire(packet)
    response = dns.message.make_response(request)
    response.flags |= dns.flags.AA
    for question in request.question:
        data = zone.get_rdataset(question.name, question.rdtype)
        if data is None:
            response.set_rcode(dns.rcode.NXDOMAIN)
            # Real ldns signatures over the SOA and complete NSEC chain. Unbound
            # validates and caches these, then synthesizes unseen NXDOMAINs.
            for owner, node in zone.nodes.items():
                for dataset in node.rdatasets:
                    if dataset.rdtype in (dns.rdatatype.SOA, dns.rdatatype.NSEC) or (
                        dataset.rdtype == dns.rdatatype.RRSIG and
                        dataset.covers in (dns.rdatatype.SOA, dns.rdatatype.NSEC)
                    ):
                        response.authority.append(dns.rrset.from_rdata_list(owner, dataset.ttl, list(dataset)))
            continue
        response.answer.append(dns.rrset.from_rdata_list(question.name, data.ttl, list(data)))
        signature = zone.get_rdataset(question.name, dns.rdatatype.RRSIG, question.rdtype)
        if signature is not None:
            response.answer.append(dns.rrset.from_rdata_list(question.name, signature.ttl, list(signature)))
    # EDNS limits UDP size, not the two-byte-framed TCP/TLS response. A signed
    # denial chain may need truncation and a real TCP retry on the UDP path.
    return response.to_wire(max_size=65535 if tcp else max(512, request.payload),
                            prefer_truncation=not tcp)


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
            result = answer(packet, tcp=True)
            self.request.sendall(struct.pack("!H", len(result)) + result)


if len(sys.argv) > 2:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(os.environ.get('NETSEC_TLS_TEST_CERT', sys.argv[2]), os.environ.get('NETSEC_TLS_TEST_KEY', sys.argv[3]))
    class TLS(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(5)
            try:
                return context.wrap_socket(connection, server_side=True), address
            except Exception:
                connection.close()
                raise
    class TLS6(TLS):
        address_family = socket.AF_INET6
    with TLS6(("::1", 853), TCP) as v6, TLS(("127.0.0.2", 853), TCP) as server:
        threading.Thread(target=v6.serve_forever, daemon=True).start()
        server.serve_forever()
    raise SystemExit

with socketserver.ThreadingTCPServer(("127.0.0.1", 15356), TCP) as tcp:
    tcp.daemon_threads = True
    threading.Thread(target=tcp.serve_forever, daemon=True).start()
    with socketserver.ThreadingUDPServer(("127.0.0.1", 15356), UDP) as udp:
        udp.daemon_threads = True
        udp.serve_forever()
