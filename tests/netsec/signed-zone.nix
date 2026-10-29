{ pkgs, ... }:

pkgs.runCommand "netsec-signed-fixture" { nativeBuildInputs = [ pkgs.ldns.examples ]; } ''
  mkdir -p "$out"
  cat > zone <<'EOF'
  $ORIGIN signed.fixture.example.
  $TTL 60
  @ IN SOA ns.signed.fixture.example. hostmaster.signed.fixture.example. ( 1 60 60 3600 60 )
  @ IN NS ns.signed.fixture.example.
  ns IN A 127.0.0.1
  valid IN A 203.0.113.42
  bogus IN A 192.0.2.17
  EOF
  ksk=$(ldns-keygen -a ECDSAP256SHA256 -k signed.fixture.example.)
  zsk=$(ldns-keygen -a ECDSAP256SHA256 signed.fixture.example.)
  ldns-signzone -i 20200101000000 -e 20370101000000 -f "$out/zone" zone "$ksk" "$zsk"
  cp "$ksk.key" "$out/trust.key"
  # Corrupt one RR after signing. The valid A and apex DNSKEY stay authentic.
  sed -i 's/192\.0\.2\.17/192.0.2.18/g' "$out/zone"
''
