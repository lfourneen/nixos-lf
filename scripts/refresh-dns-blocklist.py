#!/usr/bin/env python3
"""Refresh, verify and publish local-data/dns-blocklist.txt.xz.

The snapshot is a plain, one-per-line, sorted, de-duplicated set of lowercase
names. `system/config/network/dns/blocklist.nix` validates it against
local-data/dns-blocklist.manifest.json and emits the same immutable name file
for dnsmasq and the managed core.

Modes, from the repository root:

    ./scripts/refresh-dns-blocklist.py                  # fetch, validate, publish
    ./scripts/refresh-dns-blocklist.py --check          # verify committed files
    ./scripts/refresh-dns-blocklist.py --check --fetch  # compare with upstream
    ./scripts/refresh-dns-blocklist.py --offline        # rebuild manifest/header
    ./scripts/refresh-dns-blocklist.py --verify ...     # check and emit files

Exit codes: 0 consistent or successfully published, 1 drift or refused large
change (nothing was written), 2 invalid input or tool error.

Downloads use HTTPS for the initial URL and every redirect. Each source has an
explicit host allowlist, at most three redirects, a wall-clock deadline and a
byte cap. Responses are streamed in 64 KiB chunks to a private temporary file
while hashing.

Data, manifest and header are staged, fsynced and re-read before publication.
Publication holds a writer lock and a durable rollback journal. Interrupted
updates recover on the next refresh; verification refuses an unfinished journal.
The file set is not published atomically.
"""

from __future__ import annotations

import argparse
import datetime as dt
import errno
import fcntl
import hashlib
import json
import lzma
import os
import re
import shutil
import signal
import socket
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "local-data" / "dns-blocklist.txt.xz"
MANIFEST = ROOT / "local-data" / "dns-blocklist.manifest.json"
NIX = ROOT / "system" / "config" / "network" / "dns" / "blocklist.nix"

MANIFEST_SCHEMA = "lf-dns-blocklist-manifest/1"
VERIFICATION_SCHEMA = "lf-dns-blocklist-verification/1"

MAX_DOMAIN_BYTES = 253
MAX_LABEL_BYTES = 63
MAX_DECOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_COMPRESSED_BYTES = 64 * 1024 * 1024
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_NIX_BYTES = 1024 * 1024
MAX_SOURCE_LINE_BYTES = 8192
XZ_MEMORY_LIMIT = 128 * 1024 * 1024
MAX_REDIRECTS = 3
PROTECTED_DOMAINS = json.loads((ROOT / "system/config/network/dns/protected-domains.json").read_text())
READ_CHUNK = 64 * 1024
SOCKET_TIMEOUT = 30.0
DEADLINE_SECONDS = 120.0

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_INVALID = 2

HOSTS_PREFIX = re.compile(r"^(?:0\.0\.0\.0|127\.0\.0\.1|::1?|fe80::1)$")
DNSMASQ_LINE = re.compile(r"^(?:address|server)=/([^/\s#]+)/$")
DNSMASQ_OUTPUT = re.compile(r"^address=/([^/\s#]+)/$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")

GENERATED_BEGIN = "# BEGIN GENERATED"
GENERATED_END = "# END GENERATED"

IGNORED = {
    "localhost", "localhost.localdomain", "broadcasthost", "local",
    "ip6-localhost", "ip6-loopback", "0.0.0.0", "127.0.0.1", "::1",
}

SOURCES = [
    {
        "name": "stevenblack",
        "url": "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
        "format": "hosts",
        "allowed_hosts": ["raw.githubusercontent.com"],
        "note": "adware, malware and tracking",
    },
    {
        "name": "peterlowe",
        "url": "https://pgl.yoyo.org/adservers/serverlist.php"
               "?hostformat=hosts&showintro=0&mimetype=plaintext",
        "format": "hosts",
        "allowed_hosts": ["pgl.yoyo.org"],
        "note": "ad servers",
    },
    {
        "name": "adaway",
        "url": "https://adaway.org/hosts.txt",
        "format": "hosts",
        "allowed_hosts": ["adaway.org"],
        "note": "ad servers",
    },
    {
        "name": "oisd_big",
        "url": "https://big.oisd.nl/dnsmasq",
        "format": "dnsmasq",
        "allowed_hosts": ["big.oisd.nl"],
        "note": "ads, tracking and malware",
    },
    {
        "name": "1hosts_lite",
        "url": "https://raw.githubusercontent.com/badmojr/1Hosts/master/Lite/hosts.txt",
        "format": "hosts",
        "allowed_hosts": ["raw.githubusercontent.com"],
        "note": "balanced ad and tracking",
    },
    {
        "name": "notracking",
        "url": "https://raw.githubusercontent.com/notracking/hosts-blocklists/master"
               "/hostnames.txt",
        "format": "hosts",
        "allowed_hosts": ["raw.githubusercontent.com"],
        "note": "trackers and telemetry",
    },
]


class BlocklistError(Exception):
    """Base class for expected failures."""


class ValidationError(BlocklistError):
    """Committed or downloaded data does not satisfy the blocklist contract."""


class DownloadError(BlocklistError):
    """A source could not be fetched within its transport constraints."""


@dataclass(frozen=True)
class Snapshot:
    compressed: bytes
    payload: bytes
    names: tuple[str, ...]

    @property
    def count(self) -> int:
        return len(self.names)

    @property
    def sha256(self) -> str:
        return sha256_bytes(self.payload)

    @property
    def compressed_sha256(self) -> str:
        return sha256_bytes(self.compressed)

    @property
    def uncompressed_bytes(self) -> int:
        return len(self.payload)

    @property
    def compressed_bytes(self) -> int:
        return len(self.compressed)


@dataclass(frozen=True)
class ParsedSource:
    names: frozenset[str]
    invalid_count: int
    ignored_count: int
    top_level_candidates: int
    review: str | None


@dataclass(frozen=True)
class DownloadedFile:
    path: Path
    final_url: str
    size: int
    sha256: str
    content_type: str | None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def utc_today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def read_bounded(path: Path, limit: int, label: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValidationError(f"{label}: cannot stat {path}: {exc}") from exc
    if size > limit:
        raise ValidationError(f"{label}: {path} is {size} bytes, limit is {limit}")
    try:
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError as exc:
        raise ValidationError(f"{label}: cannot read {path}: {exc}") from exc
    if len(data) > limit:
        raise ValidationError(f"{label}: {path} exceeds the {limit}-byte limit")
    return data


def write_fsync(path: Path, data: bytes, mode: int = 0o644) -> None:
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, mode)


def fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError as exc:
        if exc.errno not in (errno.EINVAL, errno.ENOTSUP):
            raise
    finally:
        os.close(fd)


def stage_bytes(directory: Path, prefix: str, data: bytes, mode: int = 0o644) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb", dir=directory, prefix=prefix, delete=False
    )
    path = Path(handle.name)
    try:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.chmod(path, mode)
    except BaseException:
        handle.close()
        path.unlink(missing_ok=True)
        raise
    return path


def validate_domain(name: str) -> None:
    if not name:
        raise ValidationError("empty domain")
    if not name.isascii():
        raise ValidationError(f"domain is not ASCII: {name!r}")
    size = len(name.encode("ascii"))
    if size > MAX_DOMAIN_BYTES:
        raise ValidationError(f"domain is {size} bytes, limit is {MAX_DOMAIN_BYTES}: {name!r}")
    if name != name.lower():
        raise ValidationError(f"domain is not lowercase: {name!r}")
    if name.startswith(".") or name.endswith(".") or ".." in name:
        raise ValidationError(f"domain has an empty label: {name!r}")
    labels = name.split(".")
    if len(labels) < 2:
        raise ValidationError(f"domain has fewer than two labels: {name!r}")
    for label in labels:
        if not label:
            raise ValidationError(f"domain has an empty label: {name!r}")
        label_size = len(label.encode("ascii"))
        if label_size > MAX_LABEL_BYTES:
            raise ValidationError(
                f"domain label is {label_size} bytes, limit is {MAX_LABEL_BYTES}: {name!r}"
            )
        if label[0] == "-" or label[-1] == "-":
            raise ValidationError(f"domain label starts or ends with a hyphen: {name!r}")
        for char in label:
            if not ("a" <= char <= "z" or "0" <= char <= "9" or char in "-_"):
                raise ValidationError(f"domain contains invalid character {char!r}: {name!r}")


def normalize_candidate(raw: str) -> tuple[str | None, str | None]:
    value = raw.strip()
    if value.startswith("*."):
        value = value[2:]
    value = value.strip(".").lower()
    if not value:
        return None, "empty"
    if value in IGNORED:
        return None, "ignored-local"
    if "." not in value:
        return None, "top-level-label"
    try:
        validate_domain(value)
    except ValidationError as exc:
        return None, str(exc)
    return value, None


def parse_source(text: str, fmt: str, name: str = "source") -> ParsedSource:
    if "\x00" in text:
        raise ValidationError(f"{name}: source contains a NUL byte")
    head = text[:4096].lstrip().lower()
    if head.startswith("<") or "<html" in head or "<!doctype html" in head:
        raise ValidationError(f"{name}: HTML response is not a blocklist")
    if fmt not in {"hosts", "domain", "dnsmasq"}:
        raise ValidationError(f"{name}: unsupported source format {fmt!r}")

    names: set[str] = set()
    invalid = 0
    ignored = 0
    top_level = 0
    data_lines = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in "#!":
            continue
        if len(stripped.encode("utf-8", "replace")) > MAX_SOURCE_LINE_BYTES:
            invalid += 1
            continue
        data_lines += 1
        candidate: str | None = None
        if fmt == "hosts":
            parts = stripped.split()
            if len(parts) >= 2 and HOSTS_PREFIX.match(parts[0]):
                extra = parts[2:]
                if not extra or extra[0].startswith("#"):
                    candidate = parts[1]
            if candidate is None:
                invalid += 1
                continue
        elif fmt == "dnsmasq":
            match = DNSMASQ_LINE.match(stripped)
            if not match:
                invalid += 1
                continue
            candidate = match.group(1)
        else:
            parts = stripped.split()
            if parts and (len(parts) == 1 or parts[1].startswith("#")):
                candidate = parts[0]
            if candidate is None:
                invalid += 1
                continue
        normalized, reason = normalize_candidate(candidate)
        if reason is None and normalized is not None:
            names.add(normalized)
        elif reason == "top-level-label":
            top_level += 1
        elif reason == "ignored-local":
            ignored += 1
        else:
            invalid += 1

    if not names:
        raise ValidationError(
            f"{name}: source produced no valid domains "
            f"({data_lines} data lines, {invalid} invalid, {top_level} top-level labels)"
        )
    if invalid > max(100, 2 * len(names)):
        raise ValidationError(
            f"{name}: format drift, {invalid} invalid lines for {len(names)} domains"
        )
    review = None
    if top_level > max(5, len(names) // 50):
        review = "top-level-labels"
    return ParsedSource(
        names=frozenset(names),
        invalid_count=invalid,
        ignored_count=ignored,
        top_level_candidates=top_level,
        review=review,
    )


def validate_snapshot_bytes(payload: bytes) -> tuple[str, ...]:
    if not payload:
        raise ValidationError("snapshot is empty")
    if len(payload) > MAX_DECOMPRESSED_BYTES:
        raise ValidationError(
            f"decompressed snapshot is {len(payload)} bytes, "
            f"limit is {MAX_DECOMPRESSED_BYTES}"
        )
    if b"\x00" in payload:
        raise ValidationError("snapshot contains a NUL byte")
    if b"\r" in payload:
        raise ValidationError("snapshot contains CR; CRLF is not canonical")
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValidationError(f"snapshot is not ASCII: {exc}") from exc
    if not text.endswith("\n"):
        raise ValidationError("snapshot does not end with a newline")
    lines = text[:-1].split("\n")
    previous: str | None = None
    names: list[str] = []
    for number, line in enumerate(lines, 1):
        if not line:
            raise ValidationError(f"snapshot line {number} is empty")
        if len(line.encode("ascii")) > MAX_DOMAIN_BYTES:
            raise ValidationError(
                f"snapshot line {number} is longer than {MAX_DOMAIN_BYTES} bytes"
            )
        try:
            validate_domain(line)
            if any(name == line or name.endswith("." + line) for name in PROTECTED_DOMAINS):
                raise ValidationError("protected domain would be blocked")
        except ValidationError as exc:
            raise ValidationError(f"snapshot line {number}: {exc}") from exc
        if previous is not None:
            if line == previous:
                raise ValidationError(f"snapshot line {number} duplicates {line!r}")
            if line < previous:
                raise ValidationError(
                    f"snapshot line {number} is not sorted: {line!r} after {previous!r}"
                )
        previous = line
        names.append(line)
    if not names:
        raise ValidationError("snapshot contains no domains")
    return tuple(names)


def decompress_snapshot(
    compressed: bytes,
    max_bytes: int = MAX_DECOMPRESSED_BYTES,
    memlimit: int = XZ_MEMORY_LIMIT,
) -> bytes:
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise ValidationError(
            f"compressed snapshot is {len(compressed)} bytes, "
            f"limit is {MAX_COMPRESSED_BYTES}"
        )
    decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=memlimit)
    try:
        payload = decompressor.decompress(compressed, max_length=max_bytes + 1)
    except lzma.LZMAError as exc:
        raise ValidationError(
            f"xz memory limit exceeded or corrupt stream: {exc}"
        ) from exc
    if len(payload) > max_bytes:
        raise ValidationError(
            f"decompressed snapshot exceeds the {max_bytes}-byte limit"
        )
    if not decompressor.eof:
        raise ValidationError("xz stream is truncated or exceeds the decompressed limit")
    if decompressor.unused_data:
        raise ValidationError("xz stream has trailing data")
    return payload


def snapshot_from_bytes(compressed: bytes, memlimit: int = XZ_MEMORY_LIMIT) -> Snapshot:
    payload = decompress_snapshot(compressed, memlimit=memlimit)
    names = validate_snapshot_bytes(payload)
    return Snapshot(compressed=compressed, payload=payload, names=names)


def snapshot_from_path(path: Path, memlimit: int = XZ_MEMORY_LIMIT) -> Snapshot:
    compressed = read_bounded(path, MAX_COMPRESSED_BYTES, "snapshot")
    return snapshot_from_bytes(compressed, memlimit=memlimit)


def canonical_payload(names: list[str] | tuple[str, ...]) -> bytes:
    return ("\n".join(sorted(names)) + "\n").encode("ascii")


def render_dnsmasq(names: tuple[str, ...] | list[str]) -> bytes:
    for name in names:
        validate_domain(name)
    lines = [f"address=/{name}/" for name in names]
    for name, line in zip(names, lines):
        match = DNSMASQ_OUTPUT.match(line)
        if match is None or match.group(1) != name:
            raise ValidationError(f"generated dnsmasq rule does not match {name!r}")
    return ("\n".join(lines) + "\n").encode("ascii") if lines else b""


def validate_url(url: str, allowed_hosts: set[str] | frozenset[str], schemes: tuple[str, ...]) -> str:
    if not url or any(ord(char) < 0x20 or ord(char) == 0x7F for char in url):
        raise DownloadError("URL contains a control character")
    if "\\" in url:
        raise DownloadError("URL contains a backslash")
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise DownloadError(f"invalid URL: {exc}") from exc
    if parsed.scheme.lower() not in schemes:
        raise DownloadError(
            f"URL scheme {parsed.scheme!r} is not allowed; expected {', '.join(schemes)}"
        )
    if parsed.username is not None or parsed.password is not None:
        raise DownloadError("URL must not contain userinfo")
    if not parsed.hostname:
        raise DownloadError("URL has no host")
    host = parsed.hostname.lower()
    if host not in allowed_hosts:
        raise DownloadError(f"host {host!r} is not allowlisted")
    try:
        port = parsed.port
    except ValueError as exc:
        raise DownloadError(f"invalid port: {exc}") from exc
    if port is not None and not 1 <= port <= 65535:
        raise DownloadError(f"port {port} is out of range")
    if not host.isascii():
        raise DownloadError("host is not ASCII")
    return host


class RedirectPolicy(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: set[str] | frozenset[str],
                 max_redirects: int = MAX_REDIRECTS) -> None:
        super().__init__()
        self.allowed_hosts = frozenset(allowed_hosts)
        self.max_redirects = max_redirects
        self.hops = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self.hops >= self.max_redirects:
            raise DownloadError(f"more than {self.max_redirects} redirects")
        self.hops += 1
        target = urllib.parse.urljoin(req.full_url, newurl)
        validate_url(target, self.allowed_hosts, ("https",))
        return super().redirect_request(req, fp, code, msg, headers, target)


def make_opener(
    allowed_hosts: set[str] | frozenset[str],
) -> tuple[urllib.request.OpenerDirector, RedirectPolicy]:
    policy = RedirectPolicy(allowed_hosts)
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
        policy,
    )
    return opener, policy


def download_source(
    url: str,
    allowed_hosts: set[str] | frozenset[str],
    destination: Path,
    *,
    max_bytes: int = MAX_SOURCE_BYTES,
    deadline_seconds: float = DEADLINE_SECONDS,
    socket_timeout: float = SOCKET_TIMEOUT,
    initial_schemes: tuple[str, ...] = ("https",),
) -> DownloadedFile:
    allowed = frozenset(host.lower() for host in allowed_hosts)
    validate_url(url, allowed, initial_schemes)
    deadline = time.monotonic() + deadline_seconds
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "lf-dns-blocklist/1",
            "Accept": "text/plain, */*;q=0",
            "Accept-Encoding": "identity",
        },
        method="GET",
    )
    opener, _policy = make_opener(allowed)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb", dir=destination.parent, prefix=".source-", delete=False
    )
    temp_path = Path(handle.name)
    digest = hashlib.sha256()
    total = 0
    content_type: str | None = None
    final_url = url
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DownloadError("download deadline exceeded before request")
        try:
            response = opener.open(request, timeout=min(socket_timeout, remaining))
        except (urllib.error.URLError, TimeoutError, socket.timeout, ssl.SSLError) as exc:
            raise DownloadError(f"cannot fetch source: {exc}") from exc
        with response:
            if response.status != 200:
                raise DownloadError(f"source returned HTTP {response.status}")
            final_url = response.geturl()
            validate_url(final_url, allowed, initial_schemes)
            encoding = (response.headers.get("Content-Encoding") or "identity").strip().lower()
            if encoding not in {"", "identity"}:
                raise DownloadError(f"source used Content-Encoding {encoding!r}")
            content_type = response.headers.get("Content-Type")
            if content_type and ("html" in content_type.lower() or "xml" in content_type.lower()):
                raise DownloadError(f"source returned {content_type!r}, not a blocklist")
            length_header = response.headers.get("Content-Length")
            expected: int | None = None
            if length_header is not None:
                stripped = length_header.strip()
                if not stripped.isdigit():
                    raise DownloadError(f"invalid Content-Length {length_header!r}")
                expected = int(stripped)
                if expected > max_bytes:
                    raise DownloadError(
                        f"Content-Length {expected} exceeds the {max_bytes}-byte limit"
                    )
            while True:
                if time.monotonic() > deadline:
                    raise DownloadError("download deadline exceeded")
                try:
                    chunk = response.read1(READ_CHUNK)
                except (TimeoutError, socket.timeout) as exc:
                    raise DownloadError("download deadline exceeded while reading") from exc
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise DownloadError(
                        f"source exceeds the {max_bytes}-byte size limit"
                    )
                handle.write(chunk)
                digest.update(chunk)
            if expected is not None and total != expected:
                raise DownloadError(
                    f"Content-Length {expected} does not match {total} received bytes"
                )
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
    except BaseException:
        handle.close()
        temp_path.unlink(missing_ok=True)
        raise
    if total == 0:
        temp_path.unlink(missing_ok=True)
        raise DownloadError("source returned an empty body")
    return DownloadedFile(
        path=temp_path,
        final_url=final_url,
        size=total,
        sha256=digest.hexdigest(),
        content_type=content_type,
    )


def fetch_source(
    source: dict,
    workdir: Path,
    *,
    max_bytes: int = MAX_SOURCE_BYTES,
    deadline_seconds: float = DEADLINE_SECONDS,
    socket_timeout: float = SOCKET_TIMEOUT,
) -> DownloadedFile:
    allowed = source.get("allowed_hosts") or [urllib.parse.urlsplit(source["url"]).hostname]
    if not allowed:
        raise DownloadError(f"{source['name']}: source has no allowed hosts")
    host = urllib.parse.urlsplit(source["url"]).hostname
    if host is None or host.lower() not in {item.lower() for item in allowed}:
        raise DownloadError(f"{source['name']}: source URL host is not allowlisted")
    return download_source(
        source["url"],
        allowed,
        workdir / f"{source['name']}.source",
        max_bytes=max_bytes,
        deadline_seconds=deadline_seconds,
        socket_timeout=socket_timeout,
    )


def load_sources(path: Path | None) -> list[dict]:
    if path is None:
        return [dict(source) for source in SOURCES]
    raw = read_bounded(path, MAX_MANIFEST_BYTES, "sources")
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"sources file is not valid JSON: {exc}") from exc
    if not isinstance(entries, list) or not entries:
        raise ValidationError("sources file must contain a non-empty list")
    result: list[dict] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValidationError("each source must be an object")
        name = entry.get("name")
        url = entry.get("url")
        fmt = entry.get("format")
        allowed = entry.get("allowed_hosts")
        if not isinstance(name, str) or not name or name in seen:
            raise ValidationError(f"invalid or duplicate source name {name!r}")
        seen.add(name)
        if not isinstance(url, str):
            raise ValidationError(f"{name}: url must be a string")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.lower() != "https":
            raise ValidationError(f"{name}: source URL must use https")
        if fmt not in {"hosts", "domain", "dnsmasq"}:
            raise ValidationError(f"{name}: unsupported format {fmt!r}")
        if allowed is None:
            allowed = [parsed.hostname]
        if not isinstance(allowed, list) or not allowed or not all(
            isinstance(item, str) and item for item in allowed
        ):
            raise ValidationError(f"{name}: allowed_hosts must be a non-empty string list")
        hosts = [item.lower() for item in allowed]
        if not parsed.hostname or parsed.hostname.lower() not in hosts:
            raise ValidationError(f"{name}: source URL host is not allowlisted")
        result.append({
            "name": name,
            "url": url,
            "format": fmt,
            "allowed_hosts": hosts,
            "note": entry.get("note", ""),
        })
    return result


def source_inventory(sources: list[dict]) -> list[tuple[str, str]]:
    return [(source["name"], source["url"]) for source in sources]


def manifest_bytes(manifest: dict) -> bytes:
    return (json.dumps(manifest, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def load_manifest(path: Path) -> tuple[dict, bytes]:
    raw = read_bounded(path, MAX_MANIFEST_BYTES, "manifest")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"manifest is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError("manifest must be an object")
    if data.get("schema") != MANIFEST_SCHEMA:
        raise ValidationError(f"manifest schema must be {MANIFEST_SCHEMA!r}")
    generated = data.get("generated_at")
    if not isinstance(generated, str) or not DATE.match(generated):
        raise ValidationError("manifest generated_at must be YYYY-MM-DD")
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValidationError("manifest sources must be a non-empty list")
    seen: set[str] = set()
    for entry in sources:
        if not isinstance(entry, dict):
            raise ValidationError("manifest source must be an object")
        name = entry.get("name")
        url = entry.get("url")
        fmt = entry.get("format")
        count = entry.get("count")
        if not isinstance(name, str) or not name or name in seen:
            raise ValidationError(f"invalid or duplicate manifest source {name!r}")
        seen.add(name)
        if not isinstance(url, str) or urllib.parse.urlsplit(url).scheme != "https":
            raise ValidationError(f"{name}: manifest source URL must use https")
        if fmt not in {"hosts", "domain", "dnsmasq"}:
            raise ValidationError(f"{name}: invalid source format {fmt!r}")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValidationError(f"{name}: invalid source count")
        if "bytes" not in entry or "sha256" not in entry:
            raise ValidationError(f"{name}: manifest source lacks bytes/sha256 fields")
        if entry["bytes"] is not None and (
            not isinstance(entry["bytes"], int)
            or isinstance(entry["bytes"], bool)
            or entry["bytes"] < 0
        ):
            raise ValidationError(f"{name}: invalid source byte count")
        if entry["sha256"] is not None and (
            not isinstance(entry["sha256"], str) or not SHA256.match(entry["sha256"])
        ):
            raise ValidationError(f"{name}: invalid source sha256")
        if "final_url" not in entry:
            raise ValidationError(f"{name}: manifest source lacks final_url")
        if not isinstance(entry["final_url"], str) or urllib.parse.urlsplit(
            entry["final_url"]
        ).scheme != "https":
            raise ValidationError(f"{name}: invalid final_url")
    merged = data.get("merged")
    if not isinstance(merged, dict):
        raise ValidationError("manifest merged must be an object")
    for field in ("count", "uncompressed_bytes", "compressed_bytes"):
        value = merged.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValidationError(f"manifest merged.{field} must be positive")
    for field in ("sha256", "compressed_sha256"):
        value = merged.get(field)
        if not isinstance(value, str) or not SHA256.match(value):
            raise ValidationError(f"manifest merged.{field} must be sha256")
    return data, raw


def manifest_digest(raw: bytes) -> str:
    return sha256_bytes(raw)


def render_header(manifest: dict, digest: str) -> str:
    lines = [GENERATED_BEGIN]
    for source in manifest["sources"]:
        lines.append(f"#   {source['name']:12s} {source['count']:7d}  {source['url']}")
    lines.append(
        f"#   {'total':12s} {manifest['merged']['count']:7d}  merged and de-duplicated"
    )
    lines.append(f"# Snapshot: {manifest['generated_at']}")
    lines.append(f"# Manifest: sha256 {digest}")
    lines.append(GENERATED_END)
    return "\n".join(lines)


def parse_header(text: str) -> dict:
    begin = text.find(GENERATED_BEGIN)
    end = text.find(GENERATED_END, begin + 1)
    if begin < 0 or end < 0:
        raise ValidationError("blocklist.nix has no generated header markers")
    if text.find(GENERATED_BEGIN, begin + 1) >= 0 or text.find(GENERATED_END, end + 1) >= 0:
        raise ValidationError("blocklist.nix has duplicate generated header markers")
    block = text[begin:end + len(GENERATED_END)]
    sources: list[dict] = []
    total: int | None = None
    snapshot: str | None = None
    digest: str | None = None
    for line in block.splitlines():
        stripped = line.strip()
        if stripped in (GENERATED_BEGIN, GENERATED_END):
            continue
        if stripped.startswith("#   total"):
            match = re.match(r"^#\s+total\s+(\d+)\s+merged and de-duplicated$", stripped)
            if match is None or total is not None:
                raise ValidationError("blocklist.nix total header line is malformed")
            total = int(match.group(1))
            continue
        match = re.match(r"^#\s+(\S+)\s+(\d+)\s+(\S+)$", stripped)
        if match is not None and match.group(1) != "total":
            sources.append({
                "name": match.group(1),
                "count": int(match.group(2)),
                "url": match.group(3),
            })
            continue
        match = re.match(r"^#\s+Snapshot:\s+(\d{4}-\d{2}-\d{2})$", stripped)
        if match is not None and snapshot is None:
            snapshot = match.group(1)
            continue
        match = re.match(r"^#\s+Manifest:\s+sha256\s+([0-9a-f]{64})$", stripped)
        if match is not None and digest is None:
            digest = match.group(1)
            continue
        raise ValidationError(f"unrecognised generated header line: {stripped!r}")
    if total is None or snapshot is None:
        raise ValidationError("blocklist.nix header lacks total or snapshot lines")
    if not sources:
        raise ValidationError("blocklist.nix header lacks per-source counts")
    return {
        "block": block,
        "sources": sources,
        "total": total,
        "snapshot": snapshot,
        "manifest_sha256": digest,
    }


def replace_header(text: str, block: str) -> str:
    begin = text.find(GENERATED_BEGIN)
    end = text.find(GENERATED_END, begin + 1)
    if begin < 0 or end < 0:
        raise ValidationError("blocklist.nix has no generated header markers")
    if text.find(GENERATED_BEGIN, begin + 1) >= 0 or text.find(GENERATED_END, end + 1) >= 0:
        raise ValidationError("blocklist.nix has duplicate generated header markers")
    end += len(GENERATED_END)
    return text[:begin] + block + text[end:]


def manifest_source_entry(source: dict, parsed: ParsedSource,
                          downloaded: DownloadedFile | None = None) -> dict:
    if downloaded is None:
        return {
            "name": source["name"],
            "url": source["url"],
            "final_url": source["url"],
            "format": source["format"],
            "bytes": None,
            "sha256": None,
            "count": len(parsed.names),
            "content_type": None,
            "origin": "snapshot",
            "invalid_lines": parsed.invalid_count,
            "top_level_candidates": parsed.top_level_candidates,
            "review": parsed.review,
        }
    return {
        "name": source["name"],
        "url": source["url"],
        "final_url": downloaded.final_url,
        "format": source["format"],
        "bytes": downloaded.size,
        "sha256": downloaded.sha256,
        "count": len(parsed.names),
        "content_type": downloaded.content_type,
        "origin": "network",
        "invalid_lines": parsed.invalid_count,
        "top_level_candidates": parsed.top_level_candidates,
        "review": parsed.review,
    }


def make_manifest(sources: list[dict], parsed: dict[str, ParsedSource],
                  snapshot: Snapshot, generated_at: str, provenance: dict,
                  downloaded: dict[str, DownloadedFile] | None = None) -> dict:
    return {
        "schema": MANIFEST_SCHEMA,
        "generated_at": generated_at,
        "generator": "scripts/refresh-dns-blocklist.py",
        "limits": {
            "max_domain_bytes": MAX_DOMAIN_BYTES,
            "max_decompressed_bytes": MAX_DECOMPRESSED_BYTES,
            "xz_memory_limit_bytes": XZ_MEMORY_LIMIT,
        },
        "provenance": provenance,
        "sources": [
            manifest_source_entry(
                source,
                parsed[source["name"]],
                downloaded.get(source["name"]) if downloaded else None,
            )
            for source in sources
        ],
        "merged": {
            "count": snapshot.count,
            "sha256": snapshot.sha256,
            "uncompressed_bytes": snapshot.uncompressed_bytes,
            "compressed_sha256": snapshot.compressed_sha256,
            "compressed_bytes": snapshot.compressed_bytes,
        },
    }


def large_changes(baseline: dict | None, snapshot: Snapshot,
                  sources: list[dict],
                  parsed: dict[str, ParsedSource] | None = None,
                  downloaded: dict[str, DownloadedFile] | None = None) -> list[str]:
    if baseline is None:
        return []
    changes: list[str] = []
    old_sources = {entry["name"]: entry for entry in baseline["sources"]}
    new_sources = {source["name"]: source for source in sources}
    if parsed is not None and old_sources:
        for name in sorted(set(old_sources) | set(new_sources)):
            old = old_sources.get(name)
            if old is None:
                changes.append(f"source {name!r} was added")
                continue
            if name not in new_sources:
                changes.append(f"source {name!r} was removed")
                continue
            old_count = old["count"]
            new_count = len(parsed[name].names)
            if abs(new_count - old_count) > max(500, old_count // 10):
                changes.append(
                    f"source {name!r} count changed from {old_count} to {new_count}"
                )
            if old["url"] != new_sources[name]["url"]:
                changes.append(f"source {name!r} URL changed")
            old_raw = old.get("bytes")
            new_raw = downloaded[name].size if downloaded and name in downloaded else None
            if isinstance(old_raw, int) and new_raw is not None:
                if abs(new_raw - old_raw) > max(1024 * 1024, old_raw // 4):
                    changes.append(
                        f"source {name!r} size changed from {old_raw} to {new_raw}"
                    )
    merged = baseline["merged"]
    if abs(snapshot.count - merged["count"]) > max(100, merged["count"] // 50):
        changes.append(
            f"merged count changed from {merged['count']} to {snapshot.count}"
        )
    old_bytes = merged.get("uncompressed_bytes", 0)
    if abs(snapshot.uncompressed_bytes - old_bytes) > max(1024 * 1024, old_bytes // 10):
        changes.append(
            f"decompressed size changed from {old_bytes} to {snapshot.uncompressed_bytes}"
        )
    old_compressed = merged.get("compressed_bytes", 0)
    if abs(snapshot.compressed_bytes - old_compressed) > max(
        1024 * 1024, old_compressed // 4
    ):
        changes.append(
            f"compressed size changed from {old_compressed} to {snapshot.compressed_bytes}"
        )
    return changes


def recover_publication(journal: Path, allowed: set[Path]) -> None:
    if not journal.exists():
        return
    entries = json.loads((journal / "transaction.json").read_text())
    targets = {Path(entry["target"]) for entry in entries}
    if not targets <= allowed:
        raise BlocklistError("publication journal contains unexpected paths")
    for index, entry in enumerate(entries):
        target = Path(entry["target"])
        if entry["existed"]:
            temp = stage_bytes(target.parent, f".{target.name}.restore-", (journal / str(index)).read_bytes())
            os.replace(temp, target)
        else:
            target.unlink(missing_ok=True)
        fsync_directory(target.parent)
    shutil.rmtree(journal)
    fsync_directory(journal.parent)


def publish(staged: list[tuple[Path, Path]], old_limits: dict[Path, int] | None = None) -> None:
    journal = staged[0][1].parent / ".dns-blocklist-transaction"
    targets = {target.resolve() for _, target in staged}
    recover_publication(journal, targets)
    pending = Path(tempfile.mkdtemp(prefix=".dns-blocklist-backup-", dir=journal.parent))
    entries = []
    try:
        for index, (_, target) in enumerate(staged):
            target = target.resolve()
            entries.append({"target": str(target), "existed": target.exists()})
            if target.exists():
                limit = (old_limits or {}).get(target, MAX_COMPRESSED_BYTES)
                write_fsync(pending / str(index), read_bounded(target, limit, "published file"))
        write_fsync(pending / "transaction.json", json.dumps(entries).encode())
        fsync_directory(pending)
        os.replace(pending, journal)
        fsync_directory(journal.parent)
        try:
            for temp, target in staged:
                os.replace(temp, target)
                fsync_directory(target.parent)
        except BaseException as exc:
            recover_publication(journal, targets)
            raise BlocklistError(f"publication failed: {exc}; restored previous files") from exc
        shutil.rmtree(journal)
        fsync_directory(journal.parent)
    finally:
        if pending.exists():
            shutil.rmtree(pending)


def verify_staged(staged_data: Path, staged_manifest: Path, staged_nix: Path,
                  expected: Snapshot, expected_manifest: bytes) -> None:
    verified = snapshot_from_path(staged_data)
    if verified.payload != expected.payload:
        raise ValidationError("staged data does not round-trip to the expected domains")
    if verified.compressed != expected.compressed:
        raise ValidationError("staged data does not round-trip byte for byte")
    if read_bounded(staged_manifest, MAX_MANIFEST_BYTES, "staged manifest") != expected_manifest:
        raise ValidationError("staged manifest does not round-trip byte for byte")
    text = read_bounded(staged_nix, MAX_NIX_BYTES, "staged header").decode("utf-8")
    parsed = parse_header(text)
    manifest = json.loads(expected_manifest)
    canonical = render_header(manifest, manifest_digest(expected_manifest))
    if parsed["block"] != canonical:
        raise ValidationError("staged header does not match the manifest")
    if parsed["total"] != expected.count or parsed["snapshot"] != manifest["generated_at"]:
        raise ValidationError("staged header does not match the snapshot")


def check_committed(data_path: Path, manifest_path: Path, nix_path: Path,
                    sources: list[dict]) -> dict:
    result = {
        "schema": VERIFICATION_SCHEMA,
        "mode": "check",
        "status": "consistent",
        "ok": True,
        "exit_code": EXIT_OK,
        "drift": [],
        "invalid": [],
    }
    manifest: dict | None = None
    raw_manifest: bytes | None = None
    if not manifest_path.exists():
        result["drift"].append(
            "manifest is missing; run --offline to derive it from the snapshot"
        )
    else:
        try:
            manifest, raw_manifest = load_manifest(manifest_path)
        except ValidationError as exc:
            result["invalid"].append(str(exc))
            return finish_check(result)
    try:
        snapshot = snapshot_from_path(data_path)
    except ValidationError as exc:
        result["invalid"].append(str(exc))
        return finish_check(result)

    result["data"] = {
        "count": snapshot.count,
        "sha256": snapshot.sha256,
        "compressed_sha256": snapshot.compressed_sha256,
        "uncompressed_bytes": snapshot.uncompressed_bytes,
        "compressed_bytes": snapshot.compressed_bytes,
    }
    if manifest is None:
        return finish_check(result)
    merged = manifest["merged"]
    checks = {
        "count": (snapshot.count, merged["count"]),
        "sha256": (snapshot.sha256, merged["sha256"]),
        "uncompressed_bytes": (snapshot.uncompressed_bytes, merged.get("uncompressed_bytes")),
        "compressed_sha256": (snapshot.compressed_sha256, merged["compressed_sha256"]),
        "compressed_bytes": (snapshot.compressed_bytes, merged.get("compressed_bytes")),
    }
    for field, (actual, expected) in checks.items():
        if expected is not None and actual != expected:
            result["drift"].append(
                f"manifest merged.{field} is {expected!r}, snapshot has {actual!r}"
            )
    if source_inventory(sources) != [(entry["name"], entry["url"])
                                     for entry in manifest["sources"]]:
        result["drift"].append("manifest source inventory differs from the configured sources")

    result["manifest"] = {
        "generated_at": manifest["generated_at"],
        "sha256": manifest_digest(raw_manifest),
        "merged": merged,
    }
    try:
        text = read_bounded(nix_path, MAX_NIX_BYTES, "header").decode("utf-8")
        header = parse_header(text)
    except (OSError, UnicodeDecodeError, ValidationError) as exc:
        result["invalid"].append(f"header: {exc}")
        return finish_check(result)

    canonical = render_header(manifest, manifest_digest(raw_manifest))
    if header["block"] != canonical:
        result["drift"].append("generated header does not match the manifest")
    if header["manifest_sha256"] != manifest_digest(raw_manifest):
        result["drift"].append("header manifest digest does not match the manifest")
    if header["total"] != merged["count"]:
        result["drift"].append(
            f"header total is {header['total']}, manifest count is {merged['count']}"
        )
    if header["snapshot"] != manifest["generated_at"]:
        result["drift"].append("header snapshot date does not match the manifest")
    if [(entry["name"], entry["count"], entry["url"]) for entry in header["sources"]] != [
        (entry["name"], entry["count"], entry["url"]) for entry in manifest["sources"]
    ]:
        result["drift"].append("header source lines do not match the manifest")
    result["header"] = {
        "snapshot": header["snapshot"],
        "total": header["total"],
        "manifest_sha256": header["manifest_sha256"],
    }
    return finish_check(result)


def finish_check(result: dict) -> dict:
    if result["invalid"]:
        result["status"] = "invalid"
        result["ok"] = False
        result["exit_code"] = EXIT_INVALID
        return result
    if result["drift"]:
        result["status"] = "drift"
        result["ok"] = False
        result["exit_code"] = EXIT_DRIFT
        result["recovery"] = (
            "restore the data, manifest and header together from Git, or rerun "
            "--offline to rebuild the manifest and header from the snapshot after "
            "reviewing the data"
        )
        return result
    return result


def collect_sources(sources: list[dict], workdir: Path, *, deadline_seconds: float,
                    max_source_bytes: int) -> tuple[dict[str, ParsedSource],
                                                     dict[str, DownloadedFile]]:
    parsed: dict[str, ParsedSource] = {}
    downloaded: dict[str, DownloadedFile] = {}
    for source in sources:
        file = fetch_source(
            source, workdir, max_bytes=max_source_bytes,
            deadline_seconds=deadline_seconds,
        )
        try:
            text = read_bounded(
                file.path, max_source_bytes, f"source {source['name']}"
            ).decode("utf-8", "replace")
        except OSError as exc:
            raise DownloadError(f"{source['name']}: cannot read downloaded source: {exc}") from exc
        result = parse_source(text, source["format"], source["name"])
        if result.review is not None:
            print(f"!! {source['name']}: {result.review}; review the source",
                  file=sys.stderr)
        parsed[source["name"]] = result
        downloaded[source["name"]] = file
        file.path.unlink(missing_ok=True)
    return parsed, downloaded


def merge_names(parsed: dict[str, ParsedSource]) -> list[str]:
    names: set[str] = set()
    for result in parsed.values():
        names |= result.names
    return sorted(names)


def new_snapshot(names: list[str]) -> Snapshot:
    payload = canonical_payload(names)
    compressed = lzma.compress(
        payload, format=lzma.FORMAT_XZ, preset=9 | lzma.PRESET_EXTREME
    )
    return snapshot_from_bytes(compressed)


def run_check(args, sources: list[dict]) -> dict:
    result = check_committed(args.data, args.manifest, args.nix, sources)
    if result["status"] == "consistent":
        emit_files(result, args)
    if not args.fetch:
        return result
    if result["status"] == "invalid":
        return result
    workdir = Path(tempfile.mkdtemp(prefix="dns-blocklist-check-"))
    try:
        parsed, downloaded = collect_sources(
            sources, workdir,
            deadline_seconds=args.deadline,
            max_source_bytes=args.max_source_bytes,
        )
        fetched = new_snapshot(merge_names(parsed))
    except (BlocklistError, OSError) as exc:
        result["invalid"].append(f"upstream: {exc}")
        return finish_check(result)
    finally:
        remove_tree(workdir)

    result["fetched"] = {
        "count": fetched.count,
        "sha256": fetched.sha256,
        "sources": [
            {"name": source["name"], "count": len(parsed[source["name"]].names),
             "bytes": downloaded[source["name"]].size,
             "sha256": downloaded[source["name"]].sha256}
            for source in sources
        ],
    }
    if fetched.count != result["data"]["count"]:
        result["drift"].append(
            f"upstream count is {fetched.count}, committed is {result['data']['count']}"
        )
    if fetched.sha256 != result["data"]["sha256"]:
        result["drift"].append("upstream domain set differs from the committed snapshot")
    try:
        committed_names = set(snapshot_from_path(args.data).names)
    except BlocklistError:
        committed_names = set()
    upstream_names = set(merge_names(parsed))
    added = sorted(upstream_names - committed_names)
    removed = sorted(committed_names - upstream_names)
    result["diff"] = {
        "added": added[:20],
        "removed": removed[:20],
        "added_count": len(added),
        "removed_count": len(removed),
    }
    if added or removed:
        if not result["drift"]:
            result["drift"].append("upstream domain set differs from the committed snapshot")
    return finish_check(result)


def run_fetch(args, sources: list[dict]) -> dict:
    nix_text = read_bounded(args.nix, MAX_NIX_BYTES, "header").decode("utf-8")
    parse_header(nix_text)
    manifest_path = args.manifest
    baseline: dict | None = None
    if manifest_path.exists():
        baseline, _raw = load_manifest(manifest_path)
    elif args.data.exists():
        snapshot = snapshot_from_path(args.data)
        baseline = {
            "sources": [],
            "merged": {
                "count": snapshot.count,
                "sha256": snapshot.sha256,
                "uncompressed_bytes": snapshot.uncompressed_bytes,
                "compressed_bytes": snapshot.compressed_bytes,
            },
        }

    workdir = Path(tempfile.mkdtemp(prefix="dns-blocklist-fetch-"))
    try:
        parsed, downloaded = collect_sources(
            sources, workdir,
            deadline_seconds=args.deadline,
            max_source_bytes=args.max_source_bytes,
        )
        names = merge_names(parsed)
        snapshot = new_snapshot(names)
        changes = large_changes(baseline, snapshot, sources, parsed, downloaded)
        result = {
            "schema": VERIFICATION_SCHEMA,
            "mode": "fetch",
            "status": "ok",
            "ok": True,
            "exit_code": EXIT_OK,
            "sources": [
                {"name": source["name"], "count": len(parsed[source["name"]].names),
                 "bytes": downloaded[source["name"]].size,
                 "sha256": downloaded[source["name"]].sha256,
                 "review": parsed[source["name"]].review}
                for source in sources
            ],
            "merged": {
                "count": snapshot.count,
                "sha256": snapshot.sha256,
                "uncompressed_bytes": snapshot.uncompressed_bytes,
                "compressed_sha256": snapshot.compressed_sha256,
                "compressed_bytes": snapshot.compressed_bytes,
            },
            "large_changes": changes,
        }
        if not args.accept_large_change and changes:
            result["status"] = "drift"
            result["ok"] = False
            result["exit_code"] = EXIT_DRIFT
            result["error"] = "large change requires --accept-large-change"
            return result
        manifest = make_manifest(
            sources, parsed, snapshot, utc_today(),
            {"mode": "network", "raw_source_evidence": True},
            downloaded=downloaded,
        )
        manifest_blob = manifest_bytes(manifest)
        digest = manifest_digest(manifest_blob)
        header = render_header(manifest, digest)
        new_nix = replace_header(nix_text, header)

        stage = Path(tempfile.mkdtemp(prefix=".dns-blocklist-stage-",
                                      dir=args.data.parent))
        try:
            staged_data = stage / "dns-blocklist.txt.xz"
            staged_manifest = stage / "dns-blocklist.manifest.json"
            staged_nix = stage / "blocklist.nix"
            write_fsync(staged_data, snapshot.compressed)
            write_fsync(staged_manifest, manifest_blob)
            write_fsync(staged_nix, new_nix.encode("utf-8"))
            verify_staged(staged_data, staged_manifest, staged_nix, snapshot, manifest_blob)
            publish(
                [
                    (staged_data, args.data),
                    (staged_manifest, args.manifest),
                    (staged_nix, args.nix),
                ],
                {
                    args.data: MAX_COMPRESSED_BYTES,
                    args.manifest: MAX_MANIFEST_BYTES,
                    args.nix: MAX_NIX_BYTES,
                },
            )
        finally:
            remove_tree(stage)
        result["written"] = [
            str(args.data), str(args.manifest), str(args.nix),
        ]
        result["accept_large_change"] = bool(args.accept_large_change and changes)
        result["baseline_updated"] = True
        return result
    finally:
        remove_tree(workdir)


def run_offline(args, sources: list[dict]) -> dict:
    snapshot = snapshot_from_path(args.data, memlimit=args.xz_memory_limit)
    header_error: ValidationError | None = None
    try:
        nix_text = read_bounded(args.nix, MAX_NIX_BYTES, "header").decode("utf-8")
        header = parse_header(nix_text)
    except (OSError, UnicodeDecodeError, ValidationError) as exc:
        header_error = ValidationError(f"header: {exc}")
        nix_text = None
        header = None

    result = {
        "schema": VERIFICATION_SCHEMA,
        "mode": "offline",
        "status": "ok",
        "ok": True,
        "exit_code": EXIT_OK,
        "merged": {
            "count": snapshot.count,
            "sha256": snapshot.sha256,
            "uncompressed_bytes": snapshot.uncompressed_bytes,
            "compressed_sha256": snapshot.compressed_sha256,
            "compressed_bytes": snapshot.compressed_bytes,
        },
    }
    manifest_path = args.manifest
    if manifest_path.exists():
        manifest, _raw = load_manifest(manifest_path)
        if source_inventory(sources) != [(entry["name"], entry["url"])
                                         for entry in manifest["sources"]]:
            result["status"] = "drift"
            result["ok"] = False
            result["exit_code"] = EXIT_DRIFT
            result["error"] = "source inventory changed; run an online refresh"
            return result
        merged = manifest["merged"]
        data_changed = (
            merged["count"] != snapshot.count
            or merged["sha256"] != snapshot.sha256
            or merged.get("compressed_sha256") != snapshot.compressed_sha256
        )
        if data_changed:
            changes = large_changes(manifest, snapshot, sources)
            if not args.accept_large_change and changes:
                result["status"] = "drift"
                result["ok"] = False
                result["exit_code"] = EXIT_DRIFT
                result["large_changes"] = changes
                result["error"] = "large change requires --accept-large-change"
                return result
            for entry in manifest["sources"]:
                entry["bytes"] = None
                entry["sha256"] = None
                entry["content_type"] = None
                entry["origin"] = "snapshot"
            manifest["provenance"] = {
                "mode": "snapshot",
                "raw_source_evidence": False,
                "note": "raw source bytes and hashes are not present in the merged snapshot",
            }
            manifest["generated_at"] = utc_today()
            manifest["merged"] = {
                "count": snapshot.count,
                "sha256": snapshot.sha256,
                "uncompressed_bytes": snapshot.uncompressed_bytes,
                "compressed_sha256": snapshot.compressed_sha256,
                "compressed_bytes": snapshot.compressed_bytes,
            }
            result["provenance"] = "snapshot"
        else:
            manifest["merged"] = {
                "count": snapshot.count,
                "sha256": snapshot.sha256,
                "uncompressed_bytes": snapshot.uncompressed_bytes,
                "compressed_sha256": snapshot.compressed_sha256,
                "compressed_bytes": snapshot.compressed_bytes,
            }
            result["provenance"] = manifest.get("provenance", {}).get("mode", "unknown")
    else:
        if header is None:
            raise header_error or ValidationError("blocklist.nix has no generated header")
        if source_inventory(sources) != [(entry["name"], entry["url"])
                                         for entry in header["sources"]]:
            result["status"] = "drift"
            result["ok"] = False
            result["exit_code"] = EXIT_DRIFT
            result["error"] = "header source inventory differs from the configured sources"
            return result
        if header["total"] != snapshot.count:
            raise ValidationError(
                f"header total is {header['total']}, snapshot has {snapshot.count} domains"
            )
        if sum(entry["count"] for entry in header["sources"]) < snapshot.count:
            raise ValidationError("header source counts sum to less than the snapshot count")
        parsed = {
            entry["name"]: ParsedSource(
                names=frozenset(),
                invalid_count=0,
                ignored_count=0,
                top_level_candidates=0,
                review=None,
            )
            for entry in header["sources"]
        }
        manifest = make_manifest(
            sources, parsed, snapshot, header["snapshot"],
            {
                "mode": "snapshot",
                "raw_source_evidence": False,
                "note": "raw source bytes and hashes are not present in the merged snapshot",
            },
        )
        for entry, header_entry in zip(manifest["sources"], header["sources"]):
            entry["count"] = header_entry["count"]
        result["provenance"] = "snapshot"

    manifest_blob = manifest_bytes(manifest)
    digest = manifest_digest(manifest_blob)
    header_block = render_header(manifest, digest)
    if nix_text is None:
        raise header_error or ValidationError("blocklist.nix header cannot be read")
    new_nix = replace_header(nix_text, header_block)
    stage = Path(tempfile.mkdtemp(prefix=".dns-blocklist-stage-",
                                  dir=args.manifest.parent))
    try:
        staged_manifest = stage / "dns-blocklist.manifest.json"
        staged_nix = stage / "blocklist.nix"
        write_fsync(staged_manifest, manifest_blob)
        write_fsync(staged_nix, new_nix.encode("utf-8"))
        verified_manifest = load_manifest(staged_manifest)
        if manifest_bytes(verified_manifest[0]) != manifest_blob:
            raise ValidationError("staged manifest does not round-trip byte for byte")
        staged_text = read_bounded(staged_nix, MAX_NIX_BYTES, "staged header").decode("utf-8")
        parsed_header = parse_header(staged_text)
        if parsed_header["block"] != header_block:
            raise ValidationError("staged header does not match the manifest")
        publish(
            [(staged_manifest, args.manifest), (staged_nix, args.nix)],
            {args.manifest: MAX_MANIFEST_BYTES, args.nix: MAX_NIX_BYTES},
        )
    finally:
        remove_tree(stage)
    result["written"] = [str(args.manifest), str(args.nix)]
    result["manifest_sha256"] = digest
    return result


def emit_files(result: dict, args) -> None:
    if not args.emit_names and not args.emit_dnsmasq:
        return
    if result["status"] != "consistent":
        raise ValidationError("cannot emit files: committed blocklist is not consistent")
    snapshot = snapshot_from_path(args.data, memlimit=args.xz_memory_limit)
    if args.emit_names:
        args.emit_names.parent.mkdir(parents=True, exist_ok=True)
        write_fsync(args.emit_names, snapshot.payload)
    if args.emit_dnsmasq:
        args.emit_dnsmasq.parent.mkdir(parents=True, exist_ok=True)
        write_fsync(args.emit_dnsmasq, render_dnsmasq(snapshot.names))


def remove_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(path.rglob("*"), reverse=True):
        try:
            if child.is_dir() and not child.is_symlink():
                child.rmdir()
            else:
                child.unlink()
        except OSError:
            pass
    try:
        path.rmdir()
    except OSError:
        pass


def report(result: dict, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(result, indent=2, sort_keys=True))
        for message in result.get("invalid", []):
            print(f"invalid: {message}", file=sys.stderr)
        for message in result.get("drift", []):
            print(f"drift: {message}", file=sys.stderr)
        if result.get("error"):
            print(f"error: {result['error']}", file=sys.stderr)
        return
    status = result.get("status", "invalid")
    if status == "consistent":
        print("ok: committed data, manifest and header are consistent")
    for message in result.get("invalid", []):
        print(f"invalid: {message}", file=sys.stderr)
    for message in result.get("drift", []):
        print(f"drift: {message}", file=sys.stderr)
    if result.get("error"):
        print(f"error: {result['error']}", file=sys.stderr)
    if result.get("recovery") and status == "drift":
        print(f"recovery: {result['recovery']}", file=sys.stderr)
    if status == "ok" or status == "consistent":
        if result.get("written"):
            print("wrote: " + ", ".join(result["written"]))
        if result.get("merged"):
            print(f"merged: {result['merged']['count']} domains")
        for source in result.get("sources", []):
            print(f"  {source['name']:12s} {source['count']:7d}")
    if result.get("diff") and (result["diff"]["added"] or result["diff"]["removed"]):
        print(f"upstream diff: +{result['diff']['added_count']} "
              f"-{result['diff']['removed_count']}")
        for name in result["diff"]["added"]:
            print(f"  + {name}")
        for name in result["diff"]["removed"]:
            print(f"  - {name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch, verify and publish the DNS blocklist snapshot.",
        epilog=(
            "Exit codes: 0 consistent or published, 1 drift or refused large "
            "change, 2 invalid input."
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="verify committed data, manifest and header; no writes")
    mode.add_argument("--verify", action="store_true",
                      help="same contract as --check, for build-time verification")
    parser.add_argument("--fetch", action="store_true",
                        help="with --check, also compare against upstream sources")
    parser.add_argument("--offline", action="store_true",
                        help="rebuild manifest and header from the committed snapshot")
    parser.add_argument("--accept-large-change", action="store_true",
                        help="publish a change above the review thresholds")
    parser.add_argument("--json", action="store_true", help="print JSON on stdout")
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--nix", type=Path, default=NIX)
    parser.add_argument("--sources-json", type=Path, default=None,
                        help="source inventory override (name, url, format, allowed_hosts)")
    parser.add_argument("--deadline", type=float, default=DEADLINE_SECONDS,
                        help="wall-clock seconds per source")
    parser.add_argument("--max-source-bytes", type=int, default=MAX_SOURCE_BYTES)
    parser.add_argument("--xz-memory-limit", type=int, default=XZ_MEMORY_LIMIT)
    parser.add_argument("--emit-names", type=Path, default=None,
                        help="write the validated canonical name file (--check/--verify)")
    parser.add_argument("--emit-dnsmasq", type=Path, default=None,
                        help="write address=/<domain>/ rules (--check/--verify)")
    args = parser.parse_args(argv)

    check_mode = args.check or args.verify
    if check_mode and args.offline:
        parser.error("--offline cannot be combined with --check or --verify")
    if args.fetch and not check_mode:
        parser.error("--fetch requires --check or --verify")
    if args.emit_names or args.emit_dnsmasq:
        if not check_mode:
            parser.error("--emit-names and --emit-dnsmasq require --check or --verify")
    if args.accept_large_change and check_mode:
        parser.error("--accept-large-change applies only to refresh modes")

    mode_name = "check" if check_mode else ("offline" if args.offline else "fetch")
    try:
        sources = load_sources(args.sources_json)
    except ValidationError as exc:
        result = {
            "schema": VERIFICATION_SCHEMA,
            "mode": mode_name,
            "status": "invalid",
            "ok": False,
            "exit_code": EXIT_INVALID,
            "invalid": [str(exc)],
        }
        report(result, args.json)
        return EXIT_INVALID

    try:
        if check_mode:
            if (args.data.parent / ".dns-blocklist-transaction").exists():
                raise BlocklistError("unfinished publication; run refresh to recover it")
            result = run_check(args, sources)
        else:
            with (args.data.parent / ".dns-blocklist.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                for journal_parent in {args.data.parent, args.manifest.parent, args.nix.parent}:
                    recover_publication(journal_parent / ".dns-blocklist-transaction",
                                        {args.data.resolve(), args.manifest.resolve(), args.nix.resolve()})
                previous_handler = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(InterruptedError("terminated")))
                try:
                    result = run_offline(args, sources) if args.offline else run_fetch(args, sources)
                finally:
                    signal.signal(signal.SIGTERM, previous_handler)
    except (BlocklistError, OSError, UnicodeDecodeError) as exc:
        result = {
            "schema": VERIFICATION_SCHEMA,
            "mode": mode_name,
            "status": "invalid",
            "ok": False,
            "exit_code": EXIT_INVALID,
            "invalid": [str(exc)],
        }
        report(result, args.json)
        return EXIT_INVALID
    report(result, args.json)
    return int(result.get("exit_code", EXIT_OK))


if __name__ == "__main__":
    sys.exit(main())
