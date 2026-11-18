"""VM-only authoritative answers from a zone signed by the real ldns tools."""

import socketserver
import socket
import os
import ssl
import struct
import sys
import threading
import time
import base64
import http.server
import json
import urllib.parse

import dns.flags
import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
import dns.zone


zone = dns.zone.from_file(sys.argv[1], origin="signed.fixture.example.", relativize=False)


def answer(packet, tcp=False, transport='dot'):
    request = dns.message.from_wire(packet)
    response = dns.message.make_response(request)
    response.flags |= dns.flags.AA
    if request.question and request.question[0].name.to_text().endswith('.egress.fixture.example.'):
        addresses = {
            'v4.egress.fixture.example.': {'A': '203.0.113.10'},
            'v6.egress.fixture.example.': {'AAAA': '2001:db8:ffff::10'},
            'tunnel.egress.fixture.example.': {'A': '9.9.9.99', 'AAAA': '2606:4700:1111::99'},
        }
        for question in request.question:
            record = dns.rdatatype.to_text(question.rdtype)
            address = addresses.get(question.name.to_text(), {}).get(record)
            if address:
                response.answer.append(dns.rrset.from_text(question.name, 1, 'IN', record, address))
        return response.to_wire()
    if request.question and request.question[0].name.to_text() in (
            'source.fixture.example.', 'source-node.fixture.example.', 'source-node-updated.fixture.example.',
            'domestic.fixture.example.', 'denied.fixture.example.'):
        for question in request.question:
            if question.rdtype == dns.rdatatype.A:
                address = '1.1.1.1' if question.name.to_text().startswith('source') else '203.0.113.10'
                response.answer.append(dns.rrset.from_text(question.name, 5, 'IN', 'A', address))
        return response.to_wire()
    if request.question and request.question[0].name.to_text() == 'unsigned.fixture.example.':
        for question in request.question:
            if question.rdtype == dns.rdatatype.A:
                address = '203.0.113.42' if transport == 'doh' else '203.0.113.66'
                response.answer.append(dns.rrset.from_text(question.name, 30, 'IN', 'A', address))
        return response.to_wire()
    if request.question and request.question[0].name.to_text().endswith('.slow.fixture.example.'):
        time.sleep(2)
        for question in request.question:
            if question.rdtype == dns.rdatatype.A:
                response.answer.append(dns.rrset.from_text(question.name, 1, 'IN', 'A', '203.0.113.45'))
        return response.to_wire()
    for question in request.question:
        data = zone.get_rdataset(question.name, question.rdtype)
        if data is None:
            # An existing signed owner with no requested type is NODATA.
            # NXDOMAIN here would contradict the signed NSEC type bitmap.
            if question.name not in zone.nodes:
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
        try:
            while header := receive(self.request, 2):
                packet = receive(self.request, struct.unpack("!H", header)[0])
                if not packet:
                    return
                result = answer(packet, tcp=True)
                self.request.sendall(struct.pack("!H", len(result)) + result)
        except OSError:
            return  # Idle/closed fixture transports are expected during faults.


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
    if os.environ.get('NETSEC_TLS_TEST_DOH') == '1':
        class DoH(http.server.BaseHTTPRequestHandler):
            def exchange(self, packet):
                request = dns.message.from_wire(packet)
                with open('/run/netsec-doh.jsonl', 'a') as log:
                    log.write(json.dumps({'name': request.question[0].name.to_text(),
                                          'client': self.client_address[0]}) + '\n')
                result = answer(packet, tcp=True, transport='doh')
                self.send_response(200)
                self.send_header('Content-Type', 'application/dns-message')
                self.send_header('Content-Length', str(len(result)))
                self.end_headers()
                self.wfile.write(result)
            def do_POST(self):
                self.exchange(self.rfile.read(int(self.headers['Content-Length'])))
            def do_GET(self):
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)['dns'][0]
                self.exchange(base64.urlsafe_b64decode(query + '=' * (-len(query) % 4)))
            def log_message(self, *args):
                pass
        class DoHServer(http.server.ThreadingHTTPServer):
            request_queue_size = 256
            def get_request(self):
                connection, address = super().get_request()
                connection.settimeout(5)
                # Handshake inside the request thread. Serial TLS accept would
                # turn the fixture into a shared bottleneck for both roles.
                return context.wrap_socket(connection, server_side=True,
                                           do_handshake_on_connect=False), address
        doh = DoHServer((os.environ['NETSEC_TLS_TEST_BIND4'], 443), DoH)
        threading.Thread(target=doh.serve_forever, daemon=True).start()
    with TLS6(("::1", 853), TCP) as v6, TLS((os.environ.get('NETSEC_TLS_TEST_BIND4', '127.0.0.2'), 853), TCP) as server:
        threading.Thread(target=v6.serve_forever, daemon=True).start()
        server.serve_forever()
    raise SystemExit

with socketserver.ThreadingTCPServer(("127.0.0.1", 15356), TCP) as tcp:
    tcp.daemon_threads = True
    threading.Thread(target=tcp.serve_forever, daemon=True).start()
    with socketserver.ThreadingUDPServer(("127.0.0.1", 15356), UDP) as udp:
        udp.daemon_threads = True
        udp.serve_forever()
