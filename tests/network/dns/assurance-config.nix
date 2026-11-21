{ sourceTls }:
{ config, lib, pkgs, ... }:

let
  signed = import ./signed-zone.nix { inherit pkgs; };
  python = pkgs.python3.withPackages (p: [ p.pyyaml p.dnspython ]);

  testConfig = role: pkgs.runCommand "dns-${role}-fixture.conf" { } ''
    cat ${config.my.proxy.coreValidatorConfigs.${role}} > "$out"
    cat >> "$out" <<'EOF'
    server:
      do-not-query-localhost: no
      trust-anchor-file: ${signed}/trust.key
      domain-insecure: slow.fixture.example.
      domain-insecure: unsigned.fixture.example.
      domain-insecure: dns-path.fixture.example.
      max-reuse-tcp-queries: 1
      local-zone: "node.fixture.example." static
      local-data: "node.fixture.example. 60 IN A 203.0.113.10"
      local-zone: "tun.fixture.example." static
      local-data: "tun.fixture.example. 60 IN A 203.0.113.10"
      ${lib.optionalString config.my.proxy.runtimeRouting ''
      domain-insecure: source.fixture.example.
      domain-insecure: source-node.fixture.example.
      domain-insecure: source-node-updated.fixture.example.
      domain-insecure: domestic.fixture.example.
      domain-insecure: denied.fixture.example.
      domain-insecure: egress.fixture.example.
      ''}
    EOF
  '';

in
{
  my.proxy.localDnssec = true;
  my.proxy.authorizationLeaseSeconds = 30;
  my.machine.dotUpstreams = [ "203.0.113.53" ];
  my.machine.dotAuthName = "dot.fixture.test";
  my.proxy.dnsQueryBudget = {
    application = 32;
    infrastructure = 8;
  };

  services.unbound.settings.server = {
    do-not-query-localhost = false;
    trust-anchor-file = "${signed}/trust.key";
    domain-insecure = [ "unsigned.fixture.example." "dns-path.fixture.example." ];
  };


  services.unbound.settings.forward-zone = lib.mkAfter [{
    name = "signed.fixture.example.";
    forward-addr = [ "203.0.113.53@853#dot.fixture.test" ];
    forward-tls-upstream = true;
    forward-first = false;
  }];

  systemd.services = lib.genAttrs [ "clash-dns-application" "clash-dns-infrastructure" ] (name:
    let role = lib.removePrefix "clash-dns-" name;
    in {
      serviceConfig.ExecStart = lib.mkForce "${config.services.unbound.package}/bin/unbound -d -c ${testConfig role}";
      preStart = lib.mkAfter "${config.services.unbound.package}/bin/unbound-checkconf ${testConfig role}";
    }) // {
    dns-upstream.environment.NETSEC_DNSSEC_PROBE_NAME = "signed.fixture.example.";
    netsec-assurance-dns = {
      environment.NETSEC_TLS_TEST_BIND4 = "203.0.113.53";
      environment.NETSEC_TLS_TEST_DOH = "1";
      serviceConfig = {
        NetworkNamespacePath = "/run/netns/outside";
        ExecStart = "${python}/bin/python3 ${./dnssec_fixture.py} ${signed}/zone ${sourceTls}/trusted.pem ${sourceTls}/trusted.key";
      };
    };

    clash-verge = {
      requires = [ "netsec-assurance-dns.service" ];
      after = [ "netsec-assurance-dns.service" ];
    };
  };

  environment.etc."netsec-assurance.py".source = ./assurance_fixture.py;
  environment.systemPackages = [ python ];
}
