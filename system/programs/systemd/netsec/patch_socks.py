"""Use integer offsets when decoding SOCKS domain addresses.

Upstream: https://github.com/MetaCubeX/mihomo/pull/3359
Exact source matching requires review when the parser changes.
"""
from pathlib import Path
import sys


BUGGY = '''\tcase socks5.AtypDomainName:
\t\t// trim for FQDN
\t\tmetadata.Host = strings.TrimRight(string(target[2:2+target[1]]), ".")
\t\tmetadata.DstPort = uint16((int(target[2+target[1]]) << 8) | int(target[2+target[1]+1]))
'''

FIXED = '''\tcase socks5.AtypDomainName:
\t\t// Convert before arithmetic so long domain lengths cannot overflow a byte.
\t\tdomainEnd := 2 + int(target[1])
\t\t// trim for FQDN
\t\tmetadata.Host = strings.TrimRight(string(target[2:domainEnd]), ".")
\t\tmetadata.DstPort = uint16(target[domainEnd])<<8 | uint16(target[domainEnd+1])
'''


def patch(root):
    path = root / "adapter/inbound/util.go"
    source = path.read_text()
    if source.count(BUGGY) != 1:
        raise SystemExit("SOCKS parser anchor changed or fix already upstreamed: " + str(path))
    path.write_text(source.replace(BUGGY, FIXED, 1))
    tests = Path(__file__).with_name("socks_addr_test.go")
    (root / "adapter/inbound/lf_socks_addr_test.go").write_text(tests.read_text())


if __name__ == "__main__":
    patch(Path(sys.argv[1]))
