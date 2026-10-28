"""Normalize legacy bare IPv6 CONNECT targets without replacing HTTP parsing."""
from pathlib import Path
import sys

root = Path(sys.argv[1]) / 'vendor/github.com/go-gost/x/handler/http'
path = root / 'handler.go'
source = path.read_text()
old = '\tbr := bufio.NewReader(conn)\n\treq, err := http.ReadRequest(br)'
if source.count(old) != 1:
    raise SystemExit('Gost HTTP request anchor changed; review IPv6 compatibility patch')
path.chmod(0o644)
path.write_text(source.replace(old, '\tbr, req, err := lfReadRequest(bufio.NewReader(conn))', 1))
for name in ['gost_http.go', 'gost_http_test.go']:
    (root / ('lf_' + name)).write_text((Path(__file__).parent / name).read_text())
