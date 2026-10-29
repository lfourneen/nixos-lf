"""Separate encrypted-transport health from a DNSSEC verdict.

Unbound answers SERVFAIL locally when it cannot reach its DoT upstream, so a
local-only probe cannot tell "the validator rejected a signature" from "the
encrypted path is down".  The local query therefore gets its own verdict and,
when that verdict is inconclusive, a direct TLS/DNS query to the configured
upstreams names the layer that actually failed.  A failed probe must never
claim a signature error, and it must never claim health either.
"""

import json
import os
import secrets
import socket
import ssl
import struct
import sys
import time

DEFAULT_TIMEOUT = 8.0
DEFAULT_TRANSPORT_TIMEOUT = 5.0


def skip_name(data, position):
    while position < len(data):
        size = data[position]
        position += 1
        if size == 0:
            return position
        if size & 0xC0 == 0xC0:
            if position >= len(data):
                break
            return position + 1
        if size > 63 or position + size > len(data):
            break
        position += size
    raise ValueError("truncated DNS name")


def classify(data, identifier):
    """Return one verdict for a DNS message. Transport is never a signature."""
    if len(data) < 12:
        raise ValueError("short DNS header")
    ident, flags, questions, answers, authority, additional = struct.unpack("!6H", data[:12])
    if ident != identifier or not flags & 0x8000 or flags & 0x7800 or questions != 1:
        raise ValueError("unexpected DNS response")
    position = skip_name(data, 12) + 4
    codes = []
    signed = False
    for _ in range(answers + authority + additional):
        position = skip_name(data, position)
        rrtype, _, _, length = struct.unpack("!HHIH", data[position:position + 10])
        position += 10
        signed = signed or rrtype == 46
        end = position + length
        if end > len(data):
            raise ValueError("truncated DNS record")
        if rrtype == 41:
            while position < end:
                option, size = struct.unpack("!HH", data[position:position + 4])
                position += 4
                if position + size > end:
                    raise ValueError("truncated EDNS option")
                if option == 15 and size >= 2:
                    codes.append(struct.unpack("!H", data[position:position + 2])[0])
                position += size
        position = end
    rcode = flags & 15
    authenticated = bool(flags & 0x20)
    result = {"rcode": rcode, "ede": codes, "authenticated": authenticated,
              "answers": answers, "signed": signed}
    if any(5 <= code <= 12 for code in codes):
        # A validator on the answering side rejected the data. This is the only
        # signature verdict, and it always wins over a transport hint.
        result.update(status="validation-error", detail="dnssec-ede")
    elif rcode in (0, 3) and authenticated and (answers or rcode == 3):
        # A validated denial of existence is as much proof of the chain as a
        # validated answer, and it is what a fresh random name returns.
        result.update(status="secure",
                      detail="authenticated" if answers else "authenticated-nonexistence")
    elif rcode == 2 and codes and all(code in (22, 23) for code in codes):
        result.update(status="unavailable", detail="transport-ede")
    elif rcode == 2:
        # Unbound converts an unreachable upstream into a bare SERVFAIL.
        result.update(status="indeterminate", detail="servfail-no-ede")
    elif rcode == 0 and not authenticated:
        result.update(status="indeterminate", detail="ad-missing")
    elif rcode == 0 and not answers:
        result.update(status="indeterminate", detail="empty-answer")
    else:
        result.update(status="indeterminate", detail="unclassified-response")
    return result


def receive(stream, size):
    data = b""
    while len(data) < size:
        part = stream.recv(size - len(data))
        if not part:
            raise OSError("closed DNS connection")
        data += part
    return data


def fresh_name():
    """A name no cache can answer: the root NSEC denial must be validated live."""
    return "probe-" + secrets.token_hex(8) + "."


def encode_name(name):
    labels = name.rstrip(".").split(".") if name != "." else []
    return b"".join(bytes([len(label)]) + label.encode("ascii") for label in labels) + b"\0"


def probe(port, name=".", rrtype=48, timeout=DEFAULT_TIMEOUT):
    """Ask the local forwarder. Returns a verdict dict, never raises on transport."""
    identifier = secrets.randbelow(65536)
    packet = struct.pack("!6H", identifier, 0x120, 1, 0, 0, 1) + encode_name(name) \
        + struct.pack("!HH", rrtype, 1)
    packet += b"\0" + struct.pack("!HHIH", 41, 1232, 0x8000, 0)
    deadline = time.monotonic() + timeout
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
            stream.settimeout(timeout)
            stream.connect(("127.0.0.1", port))
            stream.send(packet)
            response = stream.recv(65535)
        if len(response) >= 4 and response[2] & 2:
            remaining = max(0.01, deadline - time.monotonic())
            with socket.create_connection(("127.0.0.1", port), timeout=remaining) as stream:
                stream.settimeout(remaining)
                stream.sendall(struct.pack("!H", len(packet)) + packet)
                response = receive(stream, struct.unpack("!H", receive(stream, 2))[0])
        return classify(response, identifier)
    except socket.timeout:
        return {"status": "indeterminate", "rcode": None, "ede": [], "authenticated": False,
                "answers": 0, "detail": "local-timeout"}
    except (ValueError, struct.error, UnicodeError):
        return {"status": "indeterminate", "rcode": None, "ede": [], "authenticated": False,
                "answers": 0, "detail": "malformed-response"}
    except OSError as error:
        return {"status": "unavailable", "rcode": None, "ede": [], "authenticated": False,
                "answers": 0, "detail": type(error).__name__}


def parse_upstream(text):
    """Accept ADDRESS[@PORT][#TLS-NAME]; unbound uses the same syntax."""
    address, _, authname = text.partition("#")
    host, _, port = address.partition("@")
    return host, int(port or 853), authname


def query_upstream(text, timeout=DEFAULT_TRANSPORT_TIMEOUT, name=".", rrtype=48):
    """One TLS/DNS exchange with an encrypted upstream: reachability, not trust."""
    host, port, authname = parse_upstream(text)
    identifier = secrets.randbelow(65536)
    packet = struct.pack("!6H", identifier, 0x100, 1, 0, 0, 1) + encode_name(name) \
        + struct.pack("!HH", rrtype, 1)
    packet += b"\0" + struct.pack("!HHIH", 41, 1232, 0x8000, 0)
    context = ssl.create_default_context()
    if not authname:
        # Without a declared TLS name there is nothing to verify against; the
        # reachability answer is still useful, the identity is not claimed.
        context.check_hostname = False
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=authname or None) as stream:
                stream.settimeout(timeout)
                stream.sendall(struct.pack("!H", len(packet)) + packet)
                response = receive(stream, struct.unpack("!H", receive(stream, 2))[0])
        answer = classify(response, identifier)
    except socket.timeout:
        return {"upstream": text, "status": "unavailable", "detail": "tls-timeout"}
    except ssl.SSLCertVerificationError:
        return {"upstream": text, "status": "unavailable", "detail": "tls-certificate"}
    except ssl.SSLError:
        return {"upstream": text, "status": "unavailable", "detail": "tls-error"}
    except (OSError, ValueError, struct.error, UnicodeError) as error:
        return {"upstream": text, "status": "unavailable", "detail": type(error).__name__}
    # Reaching a validating upstream is proof of transport, not of a signature.
    if answer["status"] in ("secure", "indeterminate") and answer["answers"]:
        return {"upstream": text, "status": "reachable", "detail": answer["detail"],
                "rcode": answer["rcode"], "ede": answer["ede"], "signed": answer["signed"]}
    return {"upstream": text, "status": answer["status"], "detail": answer["detail"],
            "rcode": answer["rcode"], "ede": answer["ede"], "signed": answer["signed"]}


def probe_transport(upstreams, timeout=DEFAULT_TRANSPORT_TIMEOUT):
    """Aggregate the encrypted upstreams: usable if any answers with records.

    A cold TLS handshake to a distant resolver can take seconds, so the
    per-attempt budget has to stay generous: mistaking a slow upstream for a
    dead one downgrades a working resolver to plaintext. Keep probing only
    while nothing has answered, so a dead entry cannot hide a live one behind
    its own timeout.
    """
    results = []
    for text in upstreams:
        value = query_upstream(text, timeout)
        results.append(value)
        if value["status"] == "reachable":
            break
    usable = [value for value in results if value["status"] == "reachable"]
    if usable:
        return {"status": "reachable", "detail": usable[0]["detail"], "results": results}
    for value in results:
        if value["status"] == "validation-error":
            return {"status": "validation-error", "detail": value["detail"], "results": results}
    detail = results[0]["detail"] if results else "no-upstream"
    return {"status": "unavailable", "detail": detail, "results": results}


def verdict(local, transport=None):
    """Attribute an inconclusive local verdict to the layer that failed."""
    result = dict(local)
    result.setdefault("authenticated", False)
    result.setdefault("answers", 0)
    result["local"] = {"status": local.get("status"), "detail": local.get("detail"),
                       "rcode": local.get("rcode"), "ede": local.get("ede", []),
                       "authenticated": bool(local.get("authenticated", False)),
                       "answers": int(local.get("answers", 0) or 0),
                       "signed": bool(local.get("signed", False))}
    if transport is not None:
        result["transport"] = transport
    if transport is None or local["status"] in ("secure", "validation-error", "unavailable"):
        return result
    # The local validator produced no verdict. Ask the encrypted path directly:
    # a working transport points at the validator, a broken one at the network.
    if transport["status"] == "reachable":
        return result
    if transport["status"] == "validation-error":
        result.update(status="validation-error", detail="upstream-" + str(transport["detail"]))
        return result
    result.update(status="unavailable", detail="upstream-" + str(transport["detail"]))
    return result


def main(argv):
    port = int(argv[1])
    timeout = float(os.environ.get("NETSEC_PROBE_TIMEOUT", DEFAULT_TIMEOUT))
    override = os.environ.get("NETSEC_DNSSEC_PROBE_NAME", "")
    name = override or fresh_name()
    # The fresh production name must not exist, so its type is irrelevant; a
    # fixture name keeps the historical DNSKEY default.
    rrtype = int(os.environ.get("NETSEC_DNSSEC_PROBE_TYPE", "48" if override else "1"))
    local = probe(port, name=name, rrtype=rrtype, timeout=timeout)
    transport = None
    # The transport stage is only meaningful for the production probe and only
    # when the local verdict is inconclusive.
    upstreams = os.environ.get("NETSEC_DOT_PROBE_UPSTREAMS", "").split()
    if upstreams and not override and local["status"] == "indeterminate":
        transport = probe_transport(
            upstreams, float(os.environ.get("NETSEC_PROBE_TRANSPORT_TIMEOUT",
                                           DEFAULT_TRANSPORT_TIMEOUT)))
    result = verdict(local, transport)
    result["probe_name"] = name
    print(json.dumps(result))
    return {"secure": 0, "unavailable": 2, "validation-error": 3}.get(result["status"], 4)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
