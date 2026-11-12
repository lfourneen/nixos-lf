"""Read-only subscription check against a local HTTP fixture."""

import http.server
import json
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import threading
import time
import unittest

SCRIPT = Path(sys.argv.pop(1)) if len(sys.argv) > 1 else (
    Path(__file__).resolve().parents[3] / 'system/config/network/diagnostics/subscription_check.py')


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith('http://'):
            self.path = '/ok'  # isolated forward-proxy fixture, no upstream dial
        if self.path in ('/redirect', '/cross-origin'):
            self.send_response(302)
            target = '/ok' if self.path == '/redirect' else f'http://127.0.0.1:{self.server.other_port}/ok'
            self.send_header('Location', target)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/trickle':
            self.send_response(200)
            self.send_header('Content-Length', '20')
            self.end_headers()
            try:
                for _ in range(20):
                    self.wfile.write(b'x'); self.wfile.flush(); time.sleep(.1)
            except OSError:
                pass
            return
        if self.path == '/ok':
            body = b'x' * 128
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == '/etag':
            if self.headers.get('If-None-Match') == '"v1"':
                self.send_response(304)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('ETag', '"v1"')
            self.send_header('Content-Length', '0')
            self.end_headers()
        elif self.path == '/forbidden':
            self.send_response(403)
            self.send_header('Content-Length', '0')
            self.end_headers()
        elif self.path == '/slow':
            time.sleep(2)
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.end_headers()
        elif self.path == '/loop':
            self.send_response(302)
            self.send_header('Location', '/loop')
            self.send_header('Content-Length', '0')
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()


class CheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.other = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.other_port = cls.other.server_port
        cls.other.daemon_threads = True
        threading.Thread(target=cls.other.serve_forever, daemon=True).start()
        cls.port = cls.server.server_address[1]
        cls.temp = tempfile.TemporaryDirectory()
        cls.profiles = Path(cls.temp.name) / 'profiles.yaml'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.other.shutdown()
        cls.other.server_close()
        cls.temp.cleanup()

    def url(self, path):
        return f'http://127.0.0.1:{self.port}{path}'

    def profiles_for(self, url):
        self.profiles.write_text(
            'current: fixture\nitems:\n- uid: fixture\n  type: remote\n'
            f'  url: {url}\n  option: {{user_agent: fixture-agent}}\n')
        return self.profiles

    def run_check(self, url, *args):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(self.profiles_for(url)), '--json', *args],
            capture_output=True, text=True, timeout=20)
        return result, (json.loads(result.stdout) if result.stdout.strip() else None)

    def test_plain_get_reports_bounded_result(self):
        self.profiles_for(self.url('/ok'))
        before = sorted(Path(self.temp.name).iterdir())
        result, value = self.run_check(self.url('/ok'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((value['status'], value['bytes'], value['https']), (200, 128, False))
        self.assertFalse(value['conditional'])
        self.assertEqual(before, sorted(Path(self.temp.name).iterdir()), 'check must not write files')

    def test_conditional_get_reports_not_modified(self):
        result, value = self.run_check(self.url('/etag'), '--etag', '"v1"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((value['status'], value['conditional']), (304, True))

    def test_complete_error_response_is_a_result(self):
        result, value = self.run_check(self.url('/forbidden'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((value['status'], value['error']), (403, 'http'))

    def test_timeout_and_redirect_limit_are_transport_failures(self):
        result, value = self.run_check(self.url('/slow'), '--timeout', '1')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(value['error'], 'timeout')
        result, value = self.run_check(self.url('/loop'))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(value['error'], 'redirect-limit')

    def test_invalid_profile_never_echoes_the_url(self):
        self.profiles.write_text('current: fixture\nitems:\n- uid: fixture\n  type: local\n')
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.profiles)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('http', result.stdout + result.stderr)

    def test_output_never_contains_the_url_token(self):
        token = 'secret-token-value'
        result, value = self.run_check(self.url('/ok') + '?token=' + token)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(token, result.stdout + result.stderr)
        self.assertNotIn(str(self.port), result.stdout)

    def test_malformed_yaml_never_exposes_source_snippets(self):
        self.profiles.write_text('url: "https://x.invalid/?k=TESTSECRET\n')
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.profiles), '--check'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('TESTSECRET', result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_deadline_applies_to_trickling_body_and_all_attempts(self):
        started = time.monotonic()
        result, value = self.run_check(self.url('/trickle'), '--timeout', '1', '--check')
        self.assertEqual((result.returncode, value['error']), (1, 'timeout'))
        self.assertLess(time.monotonic() - started, 1.8)

    def test_same_origin_redirect_allowed_and_cross_origin_refused(self):
        result, value = self.run_check(self.url('/redirect'), '--etag', '"secret-validator"')
        self.assertEqual((result.returncode, value['status'], value['redirects']), (0, 200, 1))
        result, value = self.run_check(self.url('/cross-origin'), '--etag', '"secret-validator"')
        self.assertEqual((result.returncode, value['error']), (1, 'cross-origin-redirect'))
        self.assertNotIn('secret-validator', result.stdout + result.stderr)

    def test_proxy_fallback_requires_transport_failure(self):
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            port = reserved.getsockname()[1]
            result, value = self.run_check(f'http://127.0.0.1:{port}/ok', '--proxy', self.url(''))
        self.assertEqual((result.returncode, value['status'], value['transport']), (0, 200, 'proxy'))
        self.assertEqual([item['transport'] for item in value['attempts']], ['direct', 'proxy'])
        result, value = self.run_check(self.url('/forbidden'), '--proxy', self.url(''))
        self.assertEqual((value['status'], value['transport']), (403, 'direct'))
        self.assertEqual(len(value['attempts']), 1)


if __name__ == '__main__':
    unittest.main()
