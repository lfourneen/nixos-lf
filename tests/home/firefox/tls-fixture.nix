{ pkgs }:

pkgs.runCommand "browser-test-certificates" { nativeBuildInputs = [ pkgs.openssl ]; } ''
  mkdir -p "$out"
  cd "$out"
  openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.pem \
    -days 3650 -subj '/CN=Local browser test CA' -addext 'basicConstraints=critical,CA:TRUE' \
    -addext 'keyUsage=critical,keyCertSign,cRLSign' -addext 'subjectKeyIdentifier=hash' \
    -addext 'authorityKeyIdentifier=keyid:always'
  openssl req -new -newkey rsa:2048 -nodes -keyout trusted.key -out trusted.csr \
    -subj '/CN=privacy-fixture.invalid'
  cat > server.ext <<EOF
  basicConstraints=critical,CA:FALSE
  keyUsage=critical,digitalSignature,keyEncipherment
  subjectKeyIdentifier=hash
  authorityKeyIdentifier=keyid,issuer
  extendedKeyUsage=serverAuth
  subjectAltName=DNS:privacy-fixture.invalid,DNS:media.example.invalid,DNS:trusted.sandbox.fixture.test,DNS:expired.sandbox.fixture.test
  EOF
  openssl x509 -req -in trusted.csr -CA ca.pem -CAkey ca.key -CAcreateserial \
    -out trusted.pem -days 3650 -extfile server.ext
  touch index.txt
  echo 1000 > serial
  cat > ca.cnf <<EOF
  [ca]
  default_ca=fixture
  [fixture]
  database=index.txt
  serial=serial
  new_certs_dir=.
  certificate=ca.pem
  private_key=ca.key
  default_md=sha256
  policy=policy
  x509_extensions=server
  [policy]
  commonName=supplied
  [server]
  EOF
  cat server.ext >> ca.cnf
  openssl ca -batch -config ca.cnf -startdate 20000101000000Z -enddate 20000102000000Z \
    -in trusted.csr -out expired.pem
  cp trusted.key expired.key
  openssl req -x509 -newkey rsa:2048 -nodes -keyout untrusted.key -out untrusted.pem \
    -days 3650 -subj '/CN=untrusted.sandbox.fixture.test' \
    -addext 'subjectAltName=DNS:untrusted.sandbox.fixture.test'
  rm ca.key ca.srl trusted.csr server.ext ca.cnf index.txt* serial* 1000.pem
''
