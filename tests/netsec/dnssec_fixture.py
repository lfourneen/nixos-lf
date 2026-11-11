"""VM-only authoritative answers from a zone signed by the real ldns tools."""

import socketserver
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


def answer(packet):
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


if len(sys.argv) > 2:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(sys.argv[2], sys.argv[3])
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
    with TLS(("127.0.0.2", 853), TCP) as server:
        server.serve_forever()
    raise SystemExit

with socketserver.ThreadingTCPServer(("127.0.0.1", 15356), TCP) as tcp:
    tcp.daemon_threads = True
    threading.Thread(target=tcp.serve_forever, daemon=True).start()
    with socketserver.ThreadingUDPServer(("127.0.0.1", 15356), UDP) as udp:
        udp.daemon_threads = True
        udp.serve_forever()
