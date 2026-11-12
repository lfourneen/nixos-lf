package http

import (
	"bufio"
	"io"
	"strings"
	"testing"
)

func TestLFConnectIPv6(t *testing.T) {
	for input, expected := range map[string]string{
		"2001:db8::1:443":          "[2001:db8::1]:443",
		"240e:97d:4:2001:29:::443": "[240e:97d:4:2001:29::]:443",
		"[2001:db8::1]:443":        "[2001:db8::1]:443",
		"127.0.0.1:443":            "127.0.0.1:443",
		"example.com:443":          "example.com:443",
	} {
		reader, request, err := lfReadRequest(bufio.NewReader(strings.NewReader(
			"CONNECT " + input + " HTTP/1.1\r\nHost: " + input + "\r\nProxy-Authorization: Basic Zm9vOmJhcg==\r\n\r\nearly-payload")))
		if err != nil {
			t.Fatalf("%s: %v", input, err)
		}
		if request.Host != expected || request.Method != "CONNECT" || request.Header.Get("Proxy-Authorization") != "Basic Zm9vOmJhcg==" {
			t.Fatalf("%s: unexpected parsed request: %#v", input, request)
		}
		payload, err := io.ReadAll(reader)
		if err != nil || string(payload) != "early-payload" {
			t.Fatalf("%s: lost buffered tunnel payload: %q %v", input, payload, err)
		}
	}
}

func TestLFHTTPFraming(t *testing.T) {
	reader, request, err := lfReadRequest(bufio.NewReader(strings.NewReader(
		"POST http://example.com/upload HTTP/1.1\r\nHost: example.com\r\nContent-Length: 4\r\n\r\ndataNEXT")))
	if err != nil {
		t.Fatal(err)
	}
	body, err := io.ReadAll(request.Body)
	if err != nil || string(body) != "data" || request.URL.String() != "http://example.com/upload" {
		t.Fatalf("HTTP framing changed: %q %v", body, err)
	}
	tail, _ := io.ReadAll(reader)
	if string(tail) != "NEXT" {
		t.Fatalf("lost following bytes: %q", tail)
	}
	for _, target := range []string{"2001:db8::1:not-a-port", "2001:db8::1:99999", "bad:address:443"} {
		_, _, err := lfReadRequest(bufio.NewReader(strings.NewReader("CONNECT " + target + " HTTP/1.1\r\n\r\n")))
		if err == nil {
			t.Fatalf("accepted malformed target %s", target)
		}
	}
	_, _, err = lfReadRequest(bufio.NewReader(strings.NewReader("CONNECT " + strings.Repeat("a", 8192))))
	if err == nil {
		t.Fatal("accepted an oversized CONNECT request line")
	}
}
