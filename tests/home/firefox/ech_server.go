// Isolated test origins: ephemeral trust root, ECH keys, and a cover-name trap.
package main

import (
	"bufio"
	"crypto/ecdh"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"log"
	"math/big"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

func must(err error) {
	if err != nil {
		log.Fatal(err)
	}
}
func put(root, name string, data []byte) { must(os.WriteFile(filepath.Join(root, name), data, 0644)) }
func vector(data []byte) []byte {
	return append(binary.BigEndian.AppendUint16(nil, uint16(len(data))), data...)
}
func key(public string, id byte) tls.EncryptedClientHelloKey {
	private, err := ecdh.X25519().GenerateKey(rand.Reader)
	must(err)
	contents := []byte{id, 0, 32} // DHKEM(X25519, HKDF-SHA256)
	contents = append(contents, vector(private.PublicKey().Bytes())...)
	contents = append(contents, vector([]byte{0, 1, 0, 1})...) // HKDF-SHA256 / AES-128-GCM
	contents = append(contents, 0, byte(len(public)))
	contents = append(contents, public...)
	contents = append(contents, 0, 0) // no ECH extensions
	config := append([]byte{0xfe, 0x0d}, vector(contents)...)
	return tls.EncryptedClientHelloKey{Config: config, PrivateKey: private.Bytes(), SendAsRetry: true}
}
func prepare(root string) {
	must(os.MkdirAll(root, 0755))
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	must(err)
	ca := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Local ECH test CA"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(24 * time.Hour),
		IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign}
	caDER, err := x509.CreateCertificate(rand.Reader, ca, ca, &caKey.PublicKey, caKey)
	must(err)
	put(root, "ca.pem", pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER}))
	for i, name := range []string{"origin", "wrong"} {
		private, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
		must(err)
		domains := []string{"*.ech.fixture.example"}
		if name == "wrong" {
			domains = []string{"wrong.fixture.invalid"}
		}
		cert := &x509.Certificate{SerialNumber: big.NewInt(int64(i + 2)), DNSNames: domains,
			NotBefore: ca.NotBefore, NotAfter: ca.NotAfter, KeyUsage: x509.KeyUsageDigitalSignature,
			ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
		der, err := x509.CreateCertificate(rand.Reader, cert, ca, &private.PublicKey, caKey)
		must(err)
		put(root, name+".pem", pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
		pk, err := x509.MarshalPKCS8PrivateKey(private)
		must(err)
		put(root, name+".key", pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: pk}))
	}
	keys := []tls.EncryptedClientHelloKey{}
	dns := map[string]string{}
	for i, label := range []string{"public", "retry-public", "reject-public", "untrusted-public"} {
		fresh := key(label+".ech.fixture.example", byte(i+1))
		keys = append(keys, fresh)
		advertised := fresh
		if label != "public" {
			advertised = key(label+".ech.fixture.example", byte(i+21))
		}
		value := base64.StdEncoding.EncodeToString(vector(advertised.Config))
		if label == "public" {
			for _, name := range []string{"accepted", "badcert", "connect"} {
				dns[name+".ech.fixture.example"] = value
			}
		} else {
			dns[strings.TrimSuffix(label, "-public")+".ech.fixture.example"] = value
		}
	}
	encoded, err := json.Marshal(keys)
	must(err)
	put(root, "keys.json", encoded)
	encoded, err = json.Marshal(dns)
	must(err)
	put(root, "dns.json", encoded)
}

func serve(root string) {
	data, err := os.ReadFile(filepath.Join(root, "keys.json"))
	must(err)
	var keys []tls.EncryptedClientHelloKey
	must(json.Unmarshal(data, &keys))
	cert, err := tls.LoadX509KeyPair(filepath.Join(root, "origin.pem"), filepath.Join(root, "origin.key"))
	must(err)
	wrong, err := tls.LoadX509KeyPair(filepath.Join(root, "wrong.pem"), filepath.Join(root, "wrong.key"))
	must(err)
	events, err := os.OpenFile(filepath.Join(root, "events.jsonl"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	must(err)
	var mutex sync.Mutex
	record := func(value map[string]any) {
		mutex.Lock()
		defer mutex.Unlock()
		must(json.NewEncoder(events).Encode(value))
	}
	for _, address := range []string{"203.0.113.10:443", "203.0.113.11:443"} {
		listener, err := net.Listen("tcp", address)
		must(err)
		trap := strings.HasPrefix(address, "203.0.113.11:")
		go func() {
			for {
				raw, err := listener.Accept()
				if err != nil {
					return
				}
				go func() {
					defer raw.Close()
					_ = raw.SetDeadline(time.Now().Add(12 * time.Second))
					outer := ""
					certificateSelected := false
					config := &tls.Config{MinVersion: tls.VersionTLS13, NextProtos: []string{"http/1.1"},
						GetCertificate: func(hello *tls.ClientHelloInfo) (*tls.Certificate, error) {
							certificateSelected = true
							if strings.HasPrefix(hello.ServerName, "badcert.") || strings.HasPrefix(hello.ServerName, "untrusted-public.") {
								return &wrong, nil
							}
							return &cert, nil
						}}
					if !trap {
						config.GetEncryptedClientHelloKeys = func(hello *tls.ClientHelloInfo) ([]tls.EncryptedClientHelloKey, error) {
							// Go calls this again after certificate selection with the
							// inner hello. Capture only the pre-decryption invocation.
							if !certificateSelected {
								outer = hello.ServerName
							}
							selected := keys[:1]
							if strings.HasPrefix(outer, "retry-public.") {
								selected = keys[1:2]
							}
							if strings.HasPrefix(outer, "reject-public.") {
								selected = keys[2:3]
							}
							if strings.HasPrefix(outer, "untrusted-public.") {
								selected = keys[3:4]
							}
							result := append([]tls.EncryptedClientHelloKey(nil), selected...)
							if strings.HasPrefix(outer, "reject-public.") {
								for i := range result {
									result[i].SendAsRetry = false
								}
							}
							return result, nil
						}
					}
					conn := tls.Server(raw, config)
					err := conn.Handshake()
					state := conn.ConnectionState()
					event := map[string]any{"kind": "tls", "server_name": state.ServerName, "outer": outer, "accepted": state.ECHAccepted, "trap": trap}
					if err != nil {
						event["error"] = err.Error()
					}
					record(event)
					if err != nil {
						return
					}
					request, err := http.ReadRequest(bufio.NewReader(conn))
					if err != nil {
						return
					}
					result := map[string]any{"kind": "http", "host": request.Host, "accepted": state.ECHAccepted,
						"server_name": state.ServerName, "outer": outer, "trap": trap}
					record(result)
					body, _ := json.Marshal(result)
					_, _ = fmt.Fprintf(conn, "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\nContent-Length: %d\r\n\r\n%s", len(body), body)
				}()
			}
		}()
	}
	put(root, "ready", []byte("ready\n"))
	select {}
}

func main() {
	if len(os.Args) != 3 {
		log.Fatal("usage: ech-fixture prepare|serve DIRECTORY")
	}
	switch os.Args[1] {
	case "prepare":
		prepare(os.Args[2])
	case "serve":
		serve(os.Args[2])
	default:
		log.Fatal("unknown command")
	}
}
