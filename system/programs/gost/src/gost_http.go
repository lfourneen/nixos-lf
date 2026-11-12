package http

import (
	"bufio"
	"bytes"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"strconv"
	"strings"
)

// Some local clients send CONNECT IPv6:port without brackets. Normalize only a
// numeric IPv6 address plus a decimal port; leave all HTTP framing to net/http.
// Return the replacement reader too so an early tunnel payload is never lost.
func lfReadRequest(br *bufio.Reader) (*bufio.Reader, *http.Request, error) {
	prefix, _ := br.Peek(len("CONNECT "))
	if string(prefix) != "CONNECT " {
		req, err := http.ReadRequest(br)
		return br, req, err
	}
	line, err := br.ReadSlice('\n')
	if err != nil {
		return br, nil, err
	}
	parts := strings.Split(strings.TrimSuffix(strings.TrimSuffix(string(line), "\n"), "\r"), " ")
	if len(parts) == 3 && !strings.ContainsAny(parts[1], "[]%") {
		if index := strings.LastIndexByte(parts[1], ':'); index > 0 {
			host, port := parts[1][:index], parts[1][index+1:]
			address, parseErr := netip.ParseAddr(host)
			number, portErr := strconv.ParseUint(port, 10, 16)
			digits := port != "" && strings.IndexFunc(port, func(r rune) bool { return r < '0' || r > '9' }) < 0
			if parseErr == nil && address.Is6() && digits && portErr == nil && number > 0 {
				parts[1] = net.JoinHostPort(host, port)
				line = []byte(strings.Join(parts, " ") + "\r\n")
			} else if parseErr == nil && address.Is6() {
				return br, nil, fmt.Errorf("invalid CONNECT port")
			}
		}
	}
	// Copy the prefix before br is read again: ReadSlice aliases its buffer.
	reader := bufio.NewReader(io.MultiReader(bytes.NewReader(bytes.Clone(line)), br))
	req, err := http.ReadRequest(reader)
	return reader, req, err
}
