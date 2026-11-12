"""Explicit subscription reachability check; credentials stay in private pipes."""
import argparse
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 8 * 1024 * 1024
REDIRECT_LIMIT = 3
TIMEOUT = 15


class RedirectLimit(Exception):
    pass


class Downgrade(Exception):
    pass


class CrossOrigin(Exception):
    pass


def origin(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        raise ValueError('invalid URL')
    return parts.scheme, parts.hostname.lower(), parts.port or (443 if parts.scheme == 'https' else 80)


class Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        self.count = 0

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        old, new = origin(request.full_url), origin(newurl)
        if old[0] == 'https' and new[0] == 'http':
            raise Downgrade()
        if old != new:
            raise CrossOrigin()
        self.count += 1
        if self.count > REDIRECT_LIMIT:
            raise RedirectLimit()
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def profile(profiles):
    import yaml
    try:
        document = yaml.safe_load(Path(profiles).read_text())
    except yaml.YAMLError:
        raise ValueError('invalid profile index') from None
    if not isinstance(document, dict) or not isinstance(document.get('items'), list):
        raise ValueError('invalid profile index')
    for item in document['items']:
        if not isinstance(item, dict) or item.get('uid') != document.get('current'):
            continue
        if item.get('type') != 'remote' or not isinstance(item.get('url'), str):
            raise ValueError('current profile is not a remote subscription')
        origin(item['url'])
        option = item.get('option') if isinstance(item.get('option'), dict) else {}
        agent = option.get('user_agent')
        return item['url'], agent if isinstance(agent, str) and agent else None
    raise ValueError('current remote profile was not found')


def classify(error):
    if isinstance(error, urllib.error.HTTPError):
        return 'http', error.code
    if isinstance(error, urllib.error.URLError):
        return classify(error.reason) if isinstance(error.reason, Exception) else ('transport', None)
    for kind, category in ((TimeoutError, 'timeout'), (ssl.SSLError, 'tls'),
                           (RedirectLimit, 'redirect-limit'), (Downgrade, 'https-downgrade'),
                           (CrossOrigin, 'cross-origin-redirect'), (socket.gaierror, 'dns'),
                           (OSError, 'transport')):
        if isinstance(error, kind):
            return category, None
    return 'unknown', None


def fetch(url, agent, etag, proxy=None, timeout=TIMEOUT):
    redirects = Redirects()
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({} if proxy is None else {'http': proxy, 'https': proxy}), redirects)
    headers = {'User-Agent': agent} if agent else {}
    if etag:
        headers['If-None-Match'] = etag
    try:
        request = urllib.request.Request(url, headers=headers)
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            # Complete error responses and 304 need no body download.
            with error:
                return dict(status=error.code, bytes=0, error=None if error.code == 304 else 'http',
                            validator=bool(error.headers.get('ETag')), redirects=redirects.count)
        with response:
            data = response.read(MAX_BYTES + 1)
            return dict(status=response.status, bytes=len(data), too_large=len(data) > MAX_BYTES,
                        error='response-too-large' if len(data) > MAX_BYTES else None,
                        validator=bool(response.headers.get('ETag')), redirects=redirects.count)
    except Exception as error:
        category, code = classify(error)
        return dict(error=category, status=code, bytes=0, redirects=redirects.count)


def run(url, agent, etag, proxy=None, timeout=TIMEOUT):
    """Bound DNS, redirects and slow body reads with one parent-owned deadline.

    URL/headers are passed over stdin, never argv. A timed-out worker is killed
    and reaped; it creates no cache or credential files.
    """
    started = time.monotonic()
    deadline = started + timeout
    result = dict(https=urllib.parse.urlsplit(url).scheme == 'https', conditional=bool(etag), validator=False)
    attempts = []
    transports = [('direct', None)] + ([('proxy', proxy)] if proxy else [])
    for index, (transport, hop) in enumerate(transports):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        budget = min(remaining / 2, 5) if index == 0 and proxy else remaining
        payload = dict(url=url, agent=agent, etag=etag, proxy=hop, timeout=budget)
        attempt_at = time.monotonic()
        try:
            child = subprocess.run([sys.executable, __file__, '--worker'],
                                   input=json.dumps(payload).encode(), capture_output=True,
                                   timeout=budget)
            response = json.loads(child.stdout) if child.returncode == 0 else {}
            if not isinstance(response, dict) or 'error' not in response:
                response = dict(error='unknown', status=None, bytes=0)
        except subprocess.TimeoutExpired:
            response = dict(error='timeout', status=None, bytes=0)
        except (OSError, ValueError):
            response = dict(error='unknown', status=None, bytes=0)
        attempts.append(dict(transport=transport, error=response['error'], status=response.get('status'),
                             elapsed_ms=round((time.monotonic() - attempt_at) * 1000)))
        result.update(response, transport=transport)
        if response['error'] not in ('timeout', 'tls', 'dns', 'transport', 'unknown'):
            break
    result.update(elapsed_ms=round((time.monotonic() - started) * 1000), attempts=attempts)
    if not attempts:
        result.update(error='timeout', status=None, bytes=0, transport='none')
    return result


def main():
    if sys.argv[1:] == ['--worker']:
        try:
            request = json.loads(sys.stdin.buffer.read(65536))
            value = fetch(**request)
        except Exception:
            value = dict(error='unknown', status=None, bytes=0)
        print(json.dumps(value, separators=(',', ':')))
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument('profiles')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--etag')
    parser.add_argument('--proxy')
    parser.add_argument('--timeout', type=int, default=TIMEOUT, choices=range(1, 121))
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        url, agent = profile(args.profiles)
    except (OSError, ValueError, ImportError):
        print('proxy-subscription: local profile index is unavailable or invalid', file=sys.stderr)
        return 2
    result = run(url, agent, args.etag, args.proxy, args.timeout)
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print('Status: ' + str(result.get('status') or 'unknown'))
        print('Transport: ' + result['transport'])
        if result.get('error'):
            print('Error: ' + result['error'])
        print('HTTPS: ' + ('yes' if result['https'] else 'no'))
        print('Conditional: ' + ('yes' if result['conditional'] else 'no; no validator supplied'))
        print('Validator: ' + ('present' if result['validator'] else 'absent'))
        print('Redirects: ' + str(result.get('redirects', 0)))
        print('Elapsed: ' + str(result['elapsed_ms']) + 'ms')
        print('Bytes: ' + str(result.get('bytes', 0)))
    return 0 if result.get('error') in (None, 'http') else 1


if __name__ == '__main__':
    raise SystemExit(main())
