"""Offline contracts for the DNS blocklist download, validation and publication.

The fixture HTTP server exercises the downloader over a loopback socket. The
download helper accepts an initial plain-HTTP URL only when the caller passes
that scheme explicitly; redirects are always checked against HTTPS.
"""

import contextlib
import email.message
import hashlib
import http.server
import importlib.util
import io
import json
import lzma
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "refresh-dns-blocklist.py"
DATA = ROOT / "local-data" / "dns-blocklist.txt.xz"
MANIFEST = ROOT / "local-data" / "dns-blocklist.manifest.json"

spec = importlib.util.spec_from_file_location("refresh_dns_blocklist", SCRIPT)
bl = importlib.util.module_from_spec(spec)
sys.modules["refresh_dns_blocklist"] = bl
spec.loader.exec_module(bl)

OK_HOSTS = (
    b"# fixture hosts list\n"
    b"0.0.0.0 alpha.example\n"
    b"127.0.0.1 beta.example\n"
    b"::1 gamma.example\n"
)
CRLF_HOSTS = b"0.0.0.0 crlf-one.example\r\n0.0.0.0 crlf-two.example\r\n"
MANY_HOSTS = b"".join(f"0.0.0.0 m{index:04d}.example\n".encode() for index in range(700))
FEW_HOSTS = b"".join(f"0.0.0.0 f{index}.example\n".encode() for index in range(5))
BAD_HOSTS = b"".join(f"unparseable line {index}\n".encode() for index in range(200))
HTML = b"<!doctype html><html><body>moved</body></html>\n"


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_body(self, body, content_type="text/plain", status=200, headers=None,
                  content_length=True):
        self.send_response(status)
        if content_length:
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", content_type)
        self.send_header("Connection", "close")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        self.close_connection = True

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        port = self.server.server_address[1]
        if path == "/ok":
            self.send_body(OK_HOSTS)
        elif path == "/many":
            self.send_body(MANY_HOSTS)
        elif path == "/few":
            self.send_body(FEW_HOSTS)
        elif path == "/crlf":
            self.send_body(CRLF_HOSTS)
        elif path == "/bad":
            self.send_body(BAD_HOSTS)
        elif path == "/empty":
            self.send_body(b"")
        elif path == "/html":
            self.send_body(HTML, content_type="text/html")
        elif path == "/gzip":
            self.send_body(OK_HOSTS, headers={"Content-Encoding": "gzip"})
        elif path == "/oversize-length":
            self.send_body(b"x", content_length=False,
                           headers={"Content-Length": "1000000"})
        elif path == "/oversize-stream":
            self.send_body(b"x" * 100000, content_length=False)
        elif path == "/slow":
            self.send_response(200)
            self.send_header("Content-Length", "1000000")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(b"x")
                self.wfile.flush()
                for _ in range(40):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            self.close_connection = True
        elif path == "/redirect-http":
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{port}/ok")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
        elif path == "/redirect-host":
            self.send_response(302)
            self.send_header("Location", f"https://127.0.0.1:{port}/ok")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
        else:
            self.send_body(b"not found", status=404)


class Fixture:
    def __init__(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.base = f"http://localhost:{self.port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = Fixture()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blocklist-transport-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def fetch(self, path, **kwargs):
        options = {
            "max_bytes": 1024 * 1024,
            "deadline_seconds": 5.0,
            "initial_schemes": ("http",),
        }
        options.update(kwargs)
        return bl.download_source(
            self.fixture.base + path,
            {"localhost"},
            self.directory / "source",
            **options,
        )

    def test_streaming_download_hashes_what_it_writes(self):
        result = self.fetch("/ok")
        body = result.path.read_bytes()
        self.assertEqual(body, OK_HOSTS)
        self.assertEqual(result.size, len(OK_HOSTS))
        self.assertEqual(result.sha256, hashlib.sha256(body).hexdigest())
        self.assertEqual(result.final_url, self.fixture.base + "/ok")

    def test_oversized_content_length_is_rejected_before_body(self):
        with self.assertRaises(bl.DownloadError) as caught:
            self.fetch("/oversize-length", max_bytes=1024)
        self.assertIn("Content-Length", str(caught.exception))

    def test_stream_without_length_is_capped(self):
        with self.assertRaises(bl.DownloadError) as caught:
            self.fetch("/oversize-stream", max_bytes=4096)
        self.assertIn("size limit", str(caught.exception))

    def test_slow_response_hits_the_wall_clock_deadline(self):
        start = time.monotonic()
        with self.assertRaises(bl.DownloadError) as caught:
            self.fetch("/slow", deadline_seconds=0.4, max_bytes=2 * 1024 * 1024)
        elapsed = time.monotonic() - start
        self.assertIn("deadline", str(caught.exception))
        self.assertLess(elapsed, 3.0)

    def test_redirect_to_plain_http_is_rejected(self):
        with self.assertRaises(bl.DownloadError) as caught:
            self.fetch("/redirect-http")
        self.assertIn("https", str(caught.exception))

    def test_redirect_to_other_host_is_rejected(self):
        with self.assertRaises(bl.DownloadError) as caught:
            self.fetch("/redirect-host")
        self.assertIn("allowlisted", str(caught.exception))

    def test_content_encoding_is_rejected(self):
        with self.assertRaises(bl.DownloadError) as caught:
            self.fetch("/gzip")
        self.assertIn("Content-Encoding", str(caught.exception))

    def test_html_content_type_is_rejected(self):
        with self.assertRaises(bl.DownloadError) as caught:
            self.fetch("/html")
        self.assertIn("blocklist", str(caught.exception))

    def test_empty_body_is_rejected(self):
        with self.assertRaises(bl.DownloadError) as caught:
            self.fetch("/empty")
        self.assertIn("empty", str(caught.exception))

    def test_url_contract(self):
        hosts = {"localhost"}
        self.assertEqual(
            bl.validate_url("https://localhost/x", hosts, ("https",)),
            "localhost",
        )
        for url, schemes, message in [
            ("http://localhost/x", ("https",), "scheme"),
            ("https://user@localhost/x", ("https",), "userinfo"),
            ("https://127.0.0.1/x", ("https",), "allowlisted"),
            ("https://localhost:99999/x", ("https",), "port"),
            ("https://localhost/x\n", ("https",), "control"),
            ("https://localhost\\x", ("https",), "backslash"),
        ]:
            with self.subTest(url=url), self.assertRaises(bl.DownloadError) as caught:
                bl.validate_url(url, hosts, schemes)
            self.assertIn(message, str(caught.exception))

    def test_redirect_policy_counts_hops_and_checks_each_target(self):
        policy = bl.RedirectPolicy({"localhost"})
        request = urllib.request.Request("https://localhost/start")
        headers = email.message.Message()
        for index in range(3):
            followed = policy.redirect_request(
                request, None, 302, "Found", headers,
                f"https://localhost/next{index}",
            )
            self.assertEqual(followed.full_url, f"https://localhost/next{index}")
        with self.assertRaises(bl.DownloadError) as caught:
            policy.redirect_request(
                request, None, 302, "Found", headers, "https://localhost/too-many"
            )
        self.assertIn("redirect", str(caught.exception))
        other = bl.RedirectPolicy({"localhost"})
        with self.assertRaises(bl.DownloadError):
            other.redirect_request(
                request, None, 302, "Found", headers, "https://127.0.0.1/x"
            )


class ParserTests(unittest.TestCase):
    def test_hosts_domain_and_dnsmasq_formats(self):
        hosts = bl.parse_source(
            "0.0.0.0 A.Example.\n127.0.0.1 b.example # comment\n::1 c.example\n",
            "hosts",
            "hosts",
        )
        self.assertEqual(hosts.names, frozenset({"a.example", "b.example", "c.example"}))
        domains = bl.parse_source("*.d.example\ne.example\n", "domain", "domain")
        self.assertEqual(domains.names, frozenset({"d.example", "e.example"}))
        dnsmasq = bl.parse_source(
            "address=/f.example/\nserver=/g.example/\n", "dnsmasq", "dnsmasq"
        )
        self.assertEqual(dnsmasq.names, frozenset({"f.example", "g.example"}))

    def test_comments_only_and_top_level_only_sources_are_empty(self):
        for text in ("# only a comment\n", "address=/com/\naddress=/net/\n"):
            with self.subTest(text=text), self.assertRaises(bl.ValidationError) as caught:
                bl.parse_source(text, "dnsmasq" if text.startswith("address") else "hosts")
            self.assertIn("no valid domains", str(caught.exception))

    def test_invalid_domains_are_not_accepted(self):
        bad = [
            "-leading.example", "trailing-.example", "a..b.example",
            "a/b.example", "a b.example", "a#b.example",
            "a" * 64 + ".example", "a" * 254, "single",
        ]
        for candidate in bad:
            with self.subTest(candidate=candidate):
                normalized, _reason = bl.normalize_candidate(candidate)
                if normalized is not None:
                    self.fail(f"accepted {candidate!r}")
        good = bl.parse_source(
            "0.0.0.0 good.example\n" + "".join(
                f"0.0.0.0 {candidate}\n" for candidate in bad
            ),
            "hosts",
            "mixed",
        )
        self.assertNotIn("single", good.names)

    def test_html_body_with_plain_content_type_is_rejected(self):
        with self.assertRaises(bl.ValidationError) as caught:
            bl.parse_source(HTML.decode(), "hosts", "html")
        self.assertIn("HTML", str(caught.exception))

    def test_format_drift_fails_the_source(self):
        text = "0.0.0.0 good.example\n" + BAD_HOSTS.decode()
        with self.assertRaises(bl.ValidationError) as caught:
            bl.parse_source(text, "hosts", "drift")
        self.assertIn("format drift", str(caught.exception))

    def test_crlf_source_is_normalized(self):
        parsed = bl.parse_source(CRLF_HOSTS.decode(), "hosts", "crlf")
        self.assertEqual(parsed.names, frozenset({"crlf-one.example", "crlf-two.example"}))
        self.assertNotIn(b"\r", bl.canonical_payload(sorted(parsed.names)))

    def test_dnsmasq_rules_are_exactly_address_slash_domain_slash(self):
        names = ("alpha.example", "beta.example")
        self.assertEqual(
            bl.render_dnsmasq(names),
            b"address=/alpha.example/\naddress=/beta.example/\n",
        )
        for line in ("address=/alpha.example/extra", "address=/alpha/beta/",
                     "server=/alpha.example/#", "address = /alpha.example/"):
            with self.subTest(line=line):
                self.assertIsNone(bl.DNSMASQ_LINE.match(line))
        for name in ("bad/domain", "bad domain", "bad#domain", "UPPER.example"):
            with self.subTest(name=name), self.assertRaises(bl.ValidationError):
                bl.render_dnsmasq((name,))

    def test_snapshot_validation_accepts_canonical_only(self):
        max_domain = ("a" * 63 + "." + "b" * 63 + "." + "c" * 63 + "." + "d" * 61)
        self.assertEqual(len(max_domain), 253)
        self.assertEqual(bl.validate_snapshot_bytes((max_domain + "\n").encode()),
                         (max_domain,))
        too_long = ("a" * 63 + "." + "b" * 63 + "." + "c" * 63 + "." + "d" * 62)
        self.assertEqual(len(too_long), 254)
        invalid = [
            b"", b"a.example", b"a.example\r\n", b"a.example\n\n",
            b"a.example\nA.example\n", b"a..example\n", b"-a.example\n",
            b"a-.example\n", b"single\n", b"a/b.example\n", b"a b.example\n",
            b"#a.example\n", b"a.example\n#comment\n", b"\x00.example\n",
            b"b.example\na.example\n", b"a.example\na.example\n",
            b"a" * 64 + b".example\n", (too_long + "\n").encode(),
        ]
        for payload in invalid:
            with self.subTest(payload=payload[:40]), self.assertRaises(
                bl.ValidationError
            ):
                bl.validate_snapshot_bytes(payload)

    def test_xz_size_and_memory_limits(self):
        payload = bl.canonical_payload([f"d{index:04d}.example" for index in range(100)])
        compressed = lzma.compress(payload, preset=9 | lzma.PRESET_EXTREME)
        with self.assertRaises(bl.ValidationError) as caught:
            bl.decompress_snapshot(compressed, max_bytes=1024)
        self.assertIn("limit", str(caught.exception))
        with self.assertRaises(bl.ValidationError) as caught:
            bl.decompress_snapshot(compressed, memlimit=1024 * 1024)
        self.assertIn("memory", str(caught.exception))
        with self.assertRaises(bl.ValidationError) as caught:
            bl.decompress_snapshot(compressed + b"trailing")
        self.assertIn("trailing", str(caught.exception))

    def test_canonical_payload_roundtrip(self):
        names = ["b.example", "a.example"]
        payload = bl.canonical_payload(names)
        self.assertEqual(payload, b"a.example\nb.example\n")
        self.assertEqual(bl.validate_snapshot_bytes(payload), ("a.example", "b.example"))


def make_header(sources, total, date="2026-11-12", digest=None):
    lines = [bl.GENERATED_BEGIN]
    for source in sources:
        lines.append(f"#   {source['name']:12s} {source['count']:7d}  {source['url']}")
    lines.append(f"#   {'total':12s} {total:7d}  merged and de-duplicated")
    lines.append(f"# Snapshot: {date}")
    if digest is not None:
        lines.append(f"# Manifest: sha256 {digest}")
    lines.append(bl.GENERATED_END)
    return "\n".join(lines)


class TempState:
    def __init__(self, root, count=700, url="https://fixture.invalid/many"):
        self.root = Path(root)
        (self.root / "local-data").mkdir(parents=True, exist_ok=True)
        (self.root / "dns").mkdir(parents=True, exist_ok=True)
        self.data = self.root / "local-data" / "dns-blocklist.txt.xz"
        self.manifest = self.root / "local-data" / "dns-blocklist.manifest.json"
        self.nix = self.root / "dns" / "blocklist.nix"
        self.sources = self.root / "sources.json"
        self.url = url
        self.names = [f"d{index:04d}.example" for index in range(count)]
        self.data.write_bytes(lzma.compress(
            bl.canonical_payload(self.names), preset=9 | lzma.PRESET_EXTREME
        ))
        source = {"name": "fixture", "url": url, "format": "hosts",
                  "allowed_hosts": ["fixture.invalid"]}
        self.sources.write_text(json.dumps([source]))
        header = make_header([{"name": "fixture", "count": count, "url": url}], count)
        self.nix.write_text("{\n  # fixture module\n}\n\n" + header + "\n\n# trailer\n")

    @property
    def args(self):
        return [
            "--data", str(self.data),
            "--manifest", str(self.manifest),
            "--nix", str(self.nix),
            "--sources-json", str(self.sources),
        ]

    def run(self, *extra):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *extra, *self.args],
            capture_output=True, text=True, timeout=60,
        )

    def offline(self):
        result = self.run("--offline", "--json")
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)
        return result

    def check(self):
        return self.run("--check", "--json")

    def bytes(self):
        return tuple(path.read_bytes() for path in (self.data, self.manifest, self.nix))

    def replace_data(self, names):
        self.data.write_bytes(lzma.compress(
            bl.canonical_payload(names), preset=9 | lzma.PRESET_EXTREME
        ))


def run_main(argv):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = bl.main(argv)
    return code, out.getvalue(), err.getvalue()


def fake_fetcher(body):
    def fetch(source, workdir, **kwargs):
        path = Path(workdir) / "fixture.source"
        path.write_bytes(body)
        return bl.DownloadedFile(
            path=path,
            final_url=source["url"],
            size=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            content_type="text/plain",
        )
    return fetch


@contextlib.contextmanager
def patched_fetcher(body):
    original = bl.fetch_source
    bl.fetch_source = fake_fetcher(body)
    try:
        yield
    finally:
        bl.fetch_source = original


def extract_embedded_validator():
    text = (ROOT / "system" / "config" / "network" / "dns" / "blocklist.nix").read_text()
    marker = 'validator = pkgs.writeText "dns-blocklist-validate.py" \'\''
    start = text.index(marker) + len(marker)
    end = text.index("'';", start)
    body = textwrap.dedent(text[start:end])
    return body.replace('${builtins.toJSON (builtins.fromJSON (builtins.readFile ./protected-domains.json))}', json.dumps(bl.PROTECTED_DOMAINS))


class DeployedValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blocklist-validator-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.validator = self.root / "validate.py"
        self.validator.write_text(extract_embedded_validator())
        self.names = self.root / "names.txt"
        self.rules = self.root / "rules" / "blocklist.conf"

    def run_validator(self, manifest=MANIFEST, data=DATA, header=None):
        if header is None:
            header = ROOT / "system" / "config" / "network" / "dns" / "blocklist.nix"
        return subprocess.run(
            [sys.executable, str(self.validator), str(data), str(manifest),
             str(header), str(self.names), str(self.rules)],
            capture_output=True, text=True, timeout=60,
        )

    def test_embedded_validator_accepts_the_snapshot(self):
        result = self.run_validator()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        names = bl.validate_snapshot_bytes(lzma.decompress(DATA.read_bytes()))
        self.assertEqual(self.names.read_bytes(), lzma.decompress(DATA.read_bytes()))
        self.assertEqual(self.rules.read_bytes(), bl.render_dnsmasq(names))

    def test_embedded_validator_rejects_crlf_snapshot(self):
        payload = b"good.example\r\n"
        compressed = lzma.compress(payload, preset=9 | lzma.PRESET_EXTREME)
        digest = hashlib.sha256
        source = {
            "name": "fixture", "url": "https://fixture.invalid/x",
            "final_url": "https://fixture.invalid/x", "format": "hosts",
            "bytes": None, "sha256": None, "count": 1,
        }
        manifest = {
            "schema": "lf-dns-blocklist-manifest/1",
            "generated_at": "2026-11-12",
            "sources": [source],
            "merged": {
                "count": 1,
                "sha256": digest(payload).hexdigest(),
                "uncompressed_bytes": len(payload),
                "compressed_sha256": digest(compressed).hexdigest(),
                "compressed_bytes": len(compressed),
            },
        }
        manifest_raw = (json.dumps(manifest, indent=2) + "\n").encode()
        block = "\n".join([
            "# BEGIN GENERATED",
            f"#   {source['name']:12s} {source['count']:7d}  {source['url']}",
            f"#   {'total':12s} {manifest['merged']['count']:7d}  merged and de-duplicated",
            "# Snapshot: 2026-11-12",
            "# Manifest: sha256 " + digest(manifest_raw).hexdigest(),
            "# END GENERATED",
        ])
        manifest_path = self.root / "manifest.json"
        data_path = self.root / "data.xz"
        header_path = self.root / "header.nix"
        manifest_path.write_bytes(manifest_raw)
        data_path.write_bytes(compressed)
        header_path.write_text(block + "\n")
        result = self.run_validator(manifest_path, data_path, header_path)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("CR", result.stderr)

    def test_embedded_validator_rejects_manifest_mismatch(self):
        manifest = json.loads(MANIFEST.read_text())
        manifest["merged"]["count"] += 1
        broken = self.root / "manifest.json"
        broken.write_text(json.dumps(manifest, indent=2) + "\n")
        result = self.run_validator(broken)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("invalid:", result.stderr)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blocklist-manifest-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_real_repository_is_consistent(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--check", "--json"],
            cwd=ROOT, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "consistent")
        self.assertTrue(report["ok"])
        manifest = json.loads(MANIFEST.read_text())
        payload = lzma.decompress(DATA.read_bytes())
        names = bl.validate_snapshot_bytes(payload)
        merged = manifest["merged"]
        self.assertEqual(merged["count"], len(names))
        self.assertEqual(merged["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(merged["uncompressed_bytes"], len(payload))
        self.assertEqual(merged["compressed_sha256"],
                         hashlib.sha256(DATA.read_bytes()).hexdigest())
        self.assertEqual(merged["compressed_bytes"], DATA.stat().st_size)
        for source in manifest["sources"]:
            for field in ("name", "url", "final_url", "format", "bytes",
                          "sha256", "count"):
                self.assertIn(field, source)

    def test_offline_generation_is_deterministic(self):
        state = TempState(self.root)
        state.offline()
        first = state.bytes()
        state.offline()
        self.assertEqual(state.bytes(), first)
        manifest_bytes = state.manifest.read_bytes()
        header = bl.parse_header(state.nix.read_text())
        self.assertEqual(header["manifest_sha256"],
                         hashlib.sha256(manifest_bytes).hexdigest())
        self.assertEqual(header["total"], 700)

    def test_check_exit_codes(self):
        state = TempState(self.root)
        state.offline()
        good = state.check()
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)

        text = state.nix.read_text()
        state.nix.write_text(re.sub(r"(#\s+total\s+)\d+", r"\g<1>701", text, count=1))
        drift = state.check()
        self.assertEqual(drift.returncode, 1)
        self.assertEqual(json.loads(drift.stdout)["status"], "drift")
        self.assertIn("header", drift.stdout)

        state.nix.write_text(text)
        manifest = json.loads(state.manifest.read_text())
        manifest["schema"] = "unknown"
        state.manifest.write_text(json.dumps(manifest))
        invalid = state.check()
        self.assertEqual(invalid.returncode, 2, invalid.stdout + invalid.stderr)
        self.assertEqual(json.loads(invalid.stdout)["status"], "invalid")

        state.manifest.write_text(json.dumps(manifest).replace(
            "unknown", bl.MANIFEST_SCHEMA
        ))
        state.data.write_bytes(lzma.compress(b"crlf.example\r\n"))
        broken = state.check()
        self.assertEqual(broken.returncode, 2, broken.stdout + broken.stderr)

    def test_manifest_merged_mismatch_is_drift(self):
        state = TempState(self.root)
        state.offline()
        manifest = json.loads(state.manifest.read_text())
        manifest["merged"]["count"] += 1
        state.manifest.write_text(json.dumps(manifest, indent=2) + "\n")
        result = state.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("manifest merged.count", result.stdout)

    def test_data_only_replacement_reports_recovery(self):
        state = TempState(self.root)
        state.offline()
        before_manifest = state.manifest.read_bytes()
        state.replace_data(["only.example"])
        result = state.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertIn("recovery", report)
        self.assertIn("Git", report["recovery"])
        self.assertEqual(state.manifest.read_bytes(), before_manifest)

    def test_check_fetch_reports_upstream_drift(self):
        state = TempState(self.root)
        state.offline()
        before = state.bytes()
        with patched_fetcher(FEW_HOSTS):
            code, stdout, _stderr = run_main(["--check", "--fetch", "--json", *state.args])
        self.assertEqual(code, 1)
        report = json.loads(stdout)
        self.assertEqual(report["status"], "drift")
        self.assertIn("upstream", " ".join(report["drift"]))
        self.assertEqual(state.bytes(), before)

    def test_check_fetch_matches_identical_upstream(self):
        state = TempState(self.root)
        state.offline()
        body = b"".join(
            f"0.0.0.0 d{index:04d}.example\n".encode() for index in range(700)
        )
        with patched_fetcher(body):
            code, stdout, stderr = run_main(["--check", "--fetch", "--json", *state.args])
        self.assertEqual(code, 0, stdout + stderr)

    def test_empty_source_fails_without_publishing(self):
        state = TempState(self.root)
        state.offline()
        before = state.bytes()
        with patched_fetcher(b""):
            code, stdout, _stderr = run_main(["--json", *state.args])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stdout)["status"], "invalid")
        self.assertEqual(state.bytes(), before)

    def test_large_change_requires_explicit_acceptance(self):
        state = TempState(self.root)
        state.offline()
        before = state.bytes()
        with patched_fetcher(FEW_HOSTS):
            code, stdout, _stderr = run_main(["--json", *state.args])
            self.assertEqual(code, 1)
            report = json.loads(stdout)
            self.assertEqual(report["status"], "drift")
            self.assertTrue(report["large_changes"])
            self.assertEqual(state.bytes(), before)

            code, stdout, stderr = run_main(
                ["--accept-large-change", "--json", *state.args]
            )
            self.assertEqual(code, 0, stdout + stderr)
            report = json.loads(stdout)
            self.assertTrue(report["accept_large_change"])
            self.assertTrue(report["baseline_updated"])
        manifest = json.loads(state.manifest.read_text())
        self.assertEqual(manifest["merged"]["count"], 5)
        self.assertEqual(manifest["sources"][0]["count"], 5)
        self.assertIsNotNone(manifest["sources"][0]["sha256"])
        self.assertEqual(manifest["sources"][0]["origin"], "network")
        self.assertEqual(bl.parse_header(state.nix.read_text())["total"], 5)
        self.assertEqual(len(bl.snapshot_from_path(state.data).names), 5)
        check = state.check()
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_large_change_thresholds_cover_sources_merged_and_size(self):
        source = {
            "name": "fixture",
            "url": "https://fixture.invalid/many",
            "format": "hosts",
            "allowed_hosts": ["fixture.invalid"],
        }
        baseline = {
            "sources": [{
                "name": "fixture", "url": source["url"], "count": 1000,
                "bytes": 4 * 1024 * 1024,
            }],
            "merged": {
                "count": 1000,
                "uncompressed_bytes": 2 * 1024 * 1024,
                "compressed_bytes": 2 * 1024 * 1024,
            },
        }
        parsed = {
            "fixture": bl.ParsedSource(
                names=frozenset({"a.example", "b.example"}),
                invalid_count=0,
                ignored_count=0,
                top_level_candidates=0,
                review=None,
            )
        }
        snapshot = bl.new_snapshot(["a.example", "b.example"])
        downloaded = {
            "fixture": bl.DownloadedFile(
                path=Path("unused"),
                final_url=source["url"],
                size=100,
                sha256="0" * 64,
                content_type="text/plain",
            )
        }
        changes = bl.large_changes(baseline, snapshot, [source], parsed, downloaded)
        self.assertTrue(any("count changed" in change for change in changes))
        self.assertTrue(any("merged count" in change for change in changes))
        self.assertTrue(any("decompressed size" in change for change in changes))
        self.assertTrue(any("compressed size" in change for change in changes))
        self.assertTrue(any("size changed from" in change for change in changes))

    def test_offline_large_data_change_requires_acceptance(self):
        state = TempState(self.root)
        state.offline()
        state.replace_data(["only.example"])
        before = state.bytes()
        refused = state.run("--offline", "--json")
        self.assertEqual(refused.returncode, 1, refused.stdout + refused.stderr)
        self.assertEqual(state.bytes(), before)
        accepted = state.run("--accept-large-change", "--offline", "--json")
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        manifest = json.loads(state.manifest.read_text())
        self.assertEqual(manifest["merged"]["count"], 1)
        self.assertIsNone(manifest["sources"][0]["sha256"])
        check = state.check()
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_check_reports_missing_manifest(self):
        state = TempState(self.root)
        state.offline()
        state.manifest.unlink()
        result = state.check()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertIn("manifest is missing", " ".join(report["drift"]))

    def test_verify_emits_the_validated_name_and_rule_files(self):
        with tempfile.TemporaryDirectory(prefix="blocklist-emit-") as directory:
            names = Path(directory) / "names.txt"
            rules = Path(directory) / "rules" / "blocklist.conf"
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--verify", "--json",
                 "--emit-names", str(names), "--emit-dnsmasq", str(rules)],
                cwd=ROOT, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(names.read_bytes(), lzma.decompress(DATA.read_bytes()))
            lines = rules.read_text().splitlines()
            self.assertEqual(lines[0], "address=/0--foodwarez.da.ru/")
            self.assertTrue(all(
                re.fullmatch(r"address=/[a-z0-9_.-]+/", line) for line in lines
            ))
            self.assertEqual(len(lines), len(bl.validate_snapshot_bytes(
                lzma.decompress(DATA.read_bytes())
            )))

    def test_publication_rolls_back_when_a_rename_fails(self):
        targets = [self.root / f"target-{index}" for index in range(3)]
        originals = [f"old-{index}".encode() for index in range(3)]
        for target, original in zip(targets, originals):
            target.write_bytes(original)
        staged = []
        for index in range(3):
            path = self.root / f"staged-{index}"
            path.write_bytes(b"new")
            staged.append((path, targets[index]))
        original_replace = bl.os.replace
        calls = {"count": 0}

        def flaky(source, destination):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("simulated interruption")
            return original_replace(source, destination)

        bl.os.replace = flaky
        try:
            with self.assertRaises(bl.BlocklistError):
                bl.publish(staged)
        finally:
            bl.os.replace = original_replace
        self.assertEqual([target.read_bytes() for target in targets], originals)

    def test_interrupted_publication_recovers_from_durable_journal(self):
        target = self.root / "target"
        target.write_bytes(b"partial update")
        journal = self.root / ".dns-blocklist-transaction"
        journal.mkdir()
        (journal / "0").write_bytes(b"previous snapshot")
        (journal / "transaction.json").write_text(json.dumps([
            {"target": str(target), "existed": True}]))
        bl.recover_publication(journal, {target})
        self.assertEqual(target.read_bytes(), b"previous snapshot")
        self.assertFalse(journal.exists())

    def test_protected_domains_cannot_be_blocked_by_snapshot(self):
        for domain in ("github.com", "nixos.org", "cache.nixos.org"):
            with self.assertRaises(bl.ValidationError):
                bl.validate_snapshot_bytes((domain + "\n").encode())
        self.assertEqual(bl.validate_snapshot_bytes(b"telemetry.github.com\n"),
                         ("telemetry.github.com",))

    def test_header_render_and_parse_roundtrip(self):
        manifest = {
            "generated_at": "2026-11-12",
            "sources": [{"name": "one", "count": 7, "url": "https://one.invalid/x"}],
            "merged": {"count": 7},
        }
        digest = "1" * 64
        block = bl.render_header(manifest, digest)
        parsed = bl.parse_header("head\n" + block + "\ntail\n")
        self.assertEqual(parsed["block"], block)
        self.assertEqual(parsed["total"], 7)
        self.assertEqual(parsed["manifest_sha256"], digest)
        self.assertEqual(parsed["sources"][0]["name"], "one")


if __name__ == "__main__":
    unittest.main(verbosity=2)
