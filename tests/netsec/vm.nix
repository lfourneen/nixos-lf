{ pkgs, core, ... }:

let
  signedZone = import ./signed-zone.nix { inherit pkgs; };
  dnsPython = pkgs.python3.withPackages (p: [ p.dnspython ]);

in
pkgs.testers.runNixOSTest {
  name = "proxy-policy-recovery";
  node.pkgs = pkgs.lib.mkForce (pkgs.extend (_final: prev: { unstable = prev; }));
  nodes.machine = { config, lib, pkgs, ... }: {
    imports = [
      ../../system/config/machine.nix
      ../../system/config/hardening-options.nix
      ../../system/config/proxy-options.nix
      ({ config, lib, ... }:
        let profile = import ../../system/config/proxy-profile.nix { inherit config lib; };
        in profile // {
          # Replace the test module's default without making the production
          # read-only option overridable or adding a second definition.
          options.my.proxy = profile.options.my.proxy // {
            managedDns = profile.options.my.proxy.managedDns // {
              default = profile.options.my.proxy.managedDns.default // {
                nameserver = [ "udp://100.67.0.1:15354" ];
                default-nameserver = [ "100.67.0.1" ];
                proxy-server-nameserver = [ "udp://100.67.0.1:15354" ];
                nameserver-policy = { "+.ts.net" = [ "udp://100.67.0.1:15354#DIRECT" ]; };
                respect-rules = false;
              };
            };
          };
        })
      ../../system/config/network.nix
      ../../system/programs/systemd/proxy-mode.nix
      ../../system/programs/systemd/dns-upstream.nix
      ../../system/programs/systemd/gost-relay.nix
      ../../system/programs/systemd/nftables-verify.nix
      ../../system/programs/systemd/netsec-alert.nix
      ../../system/programs/systemd/network-bootstrap.nix
    ];
    users.users.lfour = { isNormalUser = true; uid = 1000; };
    my.proxy.nodeProbeUrls = [ "http://node.fixture.example:8080/" ];
    my.proxy.blockedDomains = [ "ads.fixture.example" "blocked.fixture.test" ];
    my.proxy.blockedDomainExceptions = [ "ok.blocked.fixture.test" ];
    my.proxy.tunProbeName = "tun.fixture.example";
    services.tailscale.enable = lib.mkForce false;
    services.avahi.enable = lib.mkForce false;
    my.machine.ntsUpstreams = [ "nts.fixture.test" ];
    networking.extraHosts = "203.0.113.10 nts.fixture.test";
    services.chrony.extraConfig = ''
      ntstrustedcerts /run/netsec-nts/server.crt
    '';
    services.unbound.settings.server = {
      local-zone = [ ''"fixture.test." static'' ];
      local-data = [
        ''"fixture.test. 0 IN A 192.0.2.42"''
        ''"ok.blocked.fixture.test. 0 IN A 192.0.2.43"''
        ''"a.ok.blocked.fixture.test. 0 IN A 192.0.2.44"''
      ];
      # TLS is selected per forward zone here so this one isolated fixture can
      # use plaintext loopback. The production root forwarder still uses TLS.
      tls-upstream = lib.mkForce false;
      do-not-query-localhost = false;
      private-domain = [ "allowed.rebind.fixture.example." ];
      domain-insecure = [ "rebind.fixture.example." "fixture.test." ];
      trust-anchor-file = "${signedZone}/trust.key";
    };
    services.unbound.settings.forward-zone = lib.mkForce [
      {
        name = ".";
        forward-addr = map (ip: "${ip}@${toString config.my.machine.ports.dot}#${config.my.machine.dotAuthName}")
          config.my.machine.dotUpstreams;
        forward-tls-upstream = true;
      }
      {
        name = "rebind.fixture.example.";
        forward-addr = [ "127.0.0.1@15355" ];
        forward-first = false;
      }
      {
        name = "signed.fixture.example.";
        forward-addr = [ "127.0.0.1@15356" ];
        forward-first = false;
      }
    ];
    systemd.services.netsec-rebind-fixture = {
      wantedBy = [ "multi-user.target" ];
      serviceConfig.ExecStart = "${pkgs.python3}/bin/python3 ${./rebind_fixture.py}";
    };
    systemd.services.netsec-dnssec-fixture = {
      wantedBy = [ "multi-user.target" ];
      serviceConfig.ExecStart = "${dnsPython}/bin/python3 ${./dnssec_fixture.py} ${signedZone}/zone";
    };
    systemd.services.dns-upstream.environment.NETSEC_DNSSEC_PROBE_NAME = "signed.fixture.example.";
    environment.etc."netsec-dnssec-probe.py".source = ../../system/programs/systemd/netsec/dnssec_probe.py;
    environment.etc."netsec-nts-server.conf".text = ''
      bindaddress 203.0.113.10
      local stratum 10
      allow 100.67.0.0/24
      pidfile /run/netsec-nts/server.pid
      bindcmdaddress /run/netsec-nts/server.sock
      cmdport 0
      ntsservercert /run/netsec-nts/server.crt
      ntsserverkey /run/netsec-nts/server.key
      ntsprocesses 0
    '';
    environment.etc."netsec-nts-untrusted.conf".text = ''
      server nts.fixture.test nts iburst
      authselectmode require
      nocerttimecheck 0
      port 0
      cmdport 0
      pidfile /run/netsec-nts/untrusted.pid
      bindcmdaddress /run/netsec-nts/untrusted.sock
    '';
    my.proxy.transparentGuestInterfaces = [ "virbr0" ];
    networking.networkmanager.unmanaged = [
      "interface-name:eth*" "interface-name:guest*"
      "interface-name:virbr0" "interface-name:wlo1"
      "interface-name:tailscale0"
    ];
    # Keep the real unit dependency graph without needing a DHCP server. All
    # packet tests use namespaces and explicitly assigned addresses inside VM.
    systemd.services.dhcpcd.serviceConfig = {
      Type = lib.mkForce "simple";
      ExecStart = lib.mkForce "${pkgs.writeShellScript "test-dhcpcd" ''exec ${pkgs.coreutils}/bin/sleep infinity''} --config ${pkgs.writeText "test-dhcpcd.conf" ""}";
      # sleep.target's network hook reloads this mocked service. A real reload
      # command would start a host dhcpcd behind the sleeping placeholder.
      ExecReload = lib.mkForce "${pkgs.coreutils}/bin/true";
      PIDFile = lib.mkForce "";
    };
    systemd.services.nftables.serviceConfig.ExecStart = lib.mkForce [
      (pkgs.writeShellScript "first-load-fault" ''
        test -e /run/allow-full-policy || exit 1
        exec ${config.my.proxy.modeScript} --reload
      '')
    ];
    environment.etc."netsec-mode-b.nft".text = config.my.proxy.proxyModeRules;
    environment.etc."netsec-mode-c.nft".text = config.my.proxy.blockedModeRules;
    environment.etc."netsec-mode-script".source = config.my.proxy.modeScript;
    environment.etc."netsec-guest-services.py".source = ./guest_services_fixture.py;
    environment.etc."netsec-ntp.py".source = ./ntp_fixture.py;
    environment.etc."netsec-private-rules.nft".text = (import ../../system/config/network.nix {
      inherit pkgs lib;
      config = config // {
        my = config.my // {
          hardening = config.my.hardening // {
            guestPrivateEgress = false;
            guestPrivateExceptions = [ { interface = "virbr0"; cidr = "100.67.0.1/32"; tcpPorts = [ 8080 ]; udpPorts = [ ]; } ];
          };
        };
      };
    }).networking.nftables.ruleset;
    environment.etc."netsec-kernel-sockets.py".text = ''
      import errno
      from pathlib import Path
      import socket

      for family, kind in ((socket.AF_RDS, socket.SOCK_SEQPACKET), (socket.AF_TIPC, socket.SOCK_RDM)):
          try:
              stream = socket.socket(family, kind)
          except OSError as error:
              assert error.errno in (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT), error
          else:
              stream.close()
              raise AssertionError("disabled cluster socket family is available")
      for module in ("rds", "tipc"):
          assert not (Path("/sys/module") / module).exists(), module
    '';
    environment.etc."netsec-tproxy.py".source = ./tproxy_fixture.py;
    environment.etc."netsec-dns.py".source = ./dns_fixture.py;
    environment.etc."netsec-core.py".source = ./core_fixture.py;
    environment.etc."netsec-integration.py".source = ./integration_fixture.py;
    environment.etc."netsec-integration-policy.json".text = builtins.toJSON config.my.proxy.managedProfile;
    environment.etc."netsec-tun-check".source = config.my.proxy.tunCheck;
    environment.etc."netsec-health.json".source = config.my.proxy.healthSettings;
    environment.etc."netsec-health.py".source = ../../system/programs/systemd/netsec/health.py;
    environment.etc."netsec-resolved-policy.py".source = ../../system/programs/systemd/netsec/resolved_policy.py;
    environment.etc."netsec-tailnet.json".text = builtins.toJSON {
      ip = "${pkgs.iproute2}/bin/ip";
      resolvectl = "${pkgs.systemd}/bin/resolvectl";
      tun = "Mihomo";
      tailnet = {
        enabled = true;
        interface = "tailscale0";
        resolver = "100.100.100.100";
        domain_file = "/run/netsec-tailnet/domain";
        cli = pkgs.writeShellScript "fixture-tailnet-status" ''cat /run/netsec-tailnet-status.json'';
      };
    };
    environment.etc."netsec-events.py".source = ../../system/programs/systemd/netsec/events.py;
    environment.etc."netsec-events-test.py".source = ./events_fixture.py;
    environment.etc."netsec-listeners-test.py".source = ./listeners_fixture.py;
    environment.etc."netsec-listener-check".source = config.my.proxy.listenerCheck;
    systemd.services.clash-verge.serviceConfig.ExecStart = pkgs.writeShellScript "test-core-listeners" ''
      if test -e /run/netsec-real-profile.json; then
        exec ${pkgs.runCommand "vm-managed-core" { } ''
          mkdir -p "$out/bin"
          ln -s ${core}/bin/mihomo "$out/bin/verge-mihomo"
        ''}/bin/verge-mihomo -d /run/netsec-real-core -f /run/netsec-real-profile.json \
          -ext-ctl-unix /run/user/1000/clash-verge-rev/verge-mihomo.sock
      fi
      exec ${pkgs.python3}/bin/python3 /etc/netsec-listeners-test.py serve "$(cat /run/listener-good-protocol)" --mixed
    '';
    systemd.services.clash-verge.environment = {
      LF_MANAGED_PROXY_POLICY = pkgs.writeText "vm-managed-policy.json" (builtins.toJSON config.my.proxy.managedProfile);
      SKIP_SYSTEM_IPV6_CHECK = "1";
    };
    systemd.services.netsec-listener-impostor.serviceConfig = {
      User = "lfour";
      ExecStart = pkgs.writeShellScript "test-impostor-listeners" ''
        exec ${pkgs.python3}/bin/python3 /etc/netsec-listeners-test.py serve "$(cat /run/listener-bad-protocol)"
      '';
    };
    environment.etc."netsec-split-test.py".source = ./split_fixture.py;
    environment.etc."netsec-policy.py".source = ../../system/programs/systemd/netsec/dns_policy.py;
    environment.etc."netsec-rules-check".source = config.my.proxy.rulesCheck;
    environment.etc."netsec-expected".source = config.my.proxy.expectedRules;
    environment.etc."netsec-split.json".text = builtins.toJSON {
      nft = "${pkgs.nftables}/bin/nft";
      nmcli = pkgs.writeShellScript "test-split-nmcli" ''
        case "$*" in
          *GENERAL.CON-UUID*)
            printf 'approved\n/org/freedesktop/NetworkManager/ActiveConnection/%s\n' "$(cat /run/split-active)"
            ;;
          *) printf '10.1.2.53\n\ncorp.test\n\n' ;;
        esac
      '';
      wireless = "wlo1";
      split_profiles = [ "approved" ];
      split_cidrs = [ "10.0.0.0/8" ];
      runtime = "/run/dns-upstream";
    };
    environment.etc."test-dns-sleep.json".text = builtins.toJSON {
      nft = pkgs.writeShellScript "fault-inject-sleep-nft" ''
        test ! -e /run/fail-sleep-nft || exit 1
        exec ${pkgs.nftables}/bin/nft "$@"
      '';
      systemctl = "${pkgs.systemd}/bin/systemctl";
    };
    systemd.services.dns-policy-sleep.serviceConfig.ExecStart = lib.mkForce
      "${pkgs.python3}/bin/python3 ${../../system/programs/systemd/netsec/dns_sleep.py} /etc/test-dns-sleep.json before";
    systemd.services.dns-policy-resume.serviceConfig.ExecStart = lib.mkForce
      "${pkgs.python3}/bin/python3 ${../../system/programs/systemd/netsec/dns_sleep.py} /etc/test-dns-sleep.json resume";
    # Hold sleep.target without invoking suspend or freezing the test driver.
    systemd.services.netsec-test-sleep-hold = {
      requires = [ "sleep.target" ];
      after = [ "sleep.target" ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; ExecStart = "${pkgs.coreutils}/bin/true"; };
    };
    environment.etc."guest-dhcpcd.conf".text = ''
      nohook resolv.conf
      nohook ntp.conf
      nohook hostname
      noipv4ll
    '';
    environment.systemPackages = [ pkgs.curl pkgs.python3 pkgs.jq pkgs.iproute2 pkgs.nftables
      pkgs.dnsmasq pkgs.dhcpcd pkgs.dnsutils pkgs.util-linux pkgs.openssl pkgs.netcat ];
    virtualisation.memorySize = 1536;
  };
  testScript = ''
    import ipaddress
    import time

    def dot_guard_drops():
        # The guard sits at priority -196, ahead of sing-tun's auto-redirect, so
        # this counter is the proof that the guard refused the flow rather than
        # a proxy path simply being unreachable.
        for line in machine.succeed("nft list chain inet lf_filter lf_dns_egress_guard").splitlines():
            if "drop" in line and "packets" in line:
                return int(line.split("packets ")[1].split()[0])
        return 0

    def wait_proxy(timeout=120):
        try:
            machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = proxy", timeout=timeout)
        except Exception:
            print(machine.execute("cat /run/proxy-mode/state.json /run/proxy-mode/health.json; /etc/netsec-tun-check; python3 /etc/netsec-health.py /etc/netsec-health.json --fast; ps -eo pid,cgroup,args; journalctl -u proxy-mode -u clash-verge --no-pager -n 60; cat /tmp/netsec-node.log"))
            raise

    start_all()
    machine.wait_for_unit("network-firewall-bootstrap.service")
    machine.fail("modprobe rds")
    machine.fail("modprobe tipc")
    machine.succeed("setpriv --reuid=1000 --clear-groups python3 /etc/netsec-kernel-sockets.py")
    machine.wait_until_fails("systemctl is-active --quiet nftables.service")
    machine.succeed("nft list table inet lf_bootstrap")
    machine.fail("nft list table inet lf_filter")

    # The peer is outside the host network stack, despite living in the VM.
    machine.succeed("ip netns add outside; ip link add ens1 type veth peer name peer0; ip link set peer0 netns outside")
    machine.succeed("ip addr add 100.67.0.2/24 dev ens1; ip link set ens1 up")
    # Both hotplug defaults and reapplication to an existing link must work.
    machine.succeed("ip link add hardening0 type dummy; udevadm settle")
    for interface in ("lo", "ens1", "hardening0"):
        for family in ("ipv4", "ipv6"):
            for setting in ("accept_redirects", "accept_source_route"):
                machine.wait_until_succeeds(f"test $(sysctl -n net.{family}.conf.{interface}.{setting}) = 0")
        machine.wait_until_succeeds(f"test $(sysctl -n net.ipv4.conf.{interface}.send_redirects) = 0")
    machine.succeed("sysctl -w net.ipv4.conf.hardening0.send_redirects=1; ${pkgs.systemd}/lib/systemd/systemd-sysctl --prefix=/net/ipv4/conf/hardening0")
    machine.succeed("test $(sysctl -n net.ipv4.conf.hardening0.send_redirects) = 0; test $(sysctl -n net.ipv4.ip_forward) = 1; test $(sysctl -n net.ipv4.conf.all.rp_filter) = 2; ip link del hardening0")
    machine.succeed("ip -n outside addr add 100.67.0.1/24 dev peer0; ip -n outside addr add 203.0.113.10/32 dev peer0; ip -n outside link set peer0 up; ip -n outside link set lo up")
    machine.succeed("ip route add 203.0.113.10/32 via 100.67.0.1 dev ens1")
    machine.succeed("python3 -m http.server 8080 --bind 0.0.0.0 >/tmp/host-http.log 2>&1 &")
    machine.fail("ip netns exec outside curl --noproxy '*' -fsS --connect-timeout 1 http://100.67.0.2:8080")

    machine.succeed("touch /run/allow-full-policy; systemctl reset-failed nftables.service; systemctl start nftables.service; systemctl start proxy-mode.service")
    machine.wait_for_unit("proxy-mode.service")
    machine.fail("nft list table inet lf_bootstrap")
    machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = direct")
    # A real NTS server in the peer namespace, trusted only by this VM client.
    machine.succeed("mkdir -p /run/netsec-nts; openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=nts.fixture.test -addext subjectAltName=DNS:nts.fixture.test -keyout /run/netsec-nts/server.key -out /run/netsec-nts/server.crt >/tmp/netsec-nts-cert.log 2>&1")
    machine.succeed("systemd-run --unit=netsec-nts-server ip netns exec outside ${pkgs.chrony}/bin/chronyd -n -x -u root -f /etc/netsec-nts-server.conf")
    machine.wait_until_succeeds("ip netns exec outside ss -ltn | grep -q ':4460 '")
    machine.succeed("systemctl reset-failed chronyd; systemctl restart chronyd")
    machine.wait_until_succeeds("chronyc -n sources | grep -Eq '^\\^\\*[[:space:]]+203.0.113.10'", timeout=90)
    machine.succeed("chronyc -n authdata | grep -q NTS; chronyc tracking | grep -q 'Leap status.*Normal'")
    # Chrony consumes and removes saved cookies when it loads them at startup.
    machine.succeed("systemctl stop chronyd && find /var/lib/chrony -name '*.nts' | grep -q . && systemctl start chronyd")
    machine.wait_until_succeeds("chronyc -n sources | grep -Eq '^\\^\\*[[:space:]]+203.0.113.10'", timeout=90)
    machine.fail("${pkgs.chrony}/bin/chronyd -Q -t 5 -u root -f /etc/netsec-nts-untrusted.conf >/tmp/netsec-nts-untrusted.log 2>&1")
    machine.succeed("grep -Ei 'certificate|Could not synchronise' /tmp/netsec-nts-untrusted.log")
    machine.succeed("nft add rule inet lf_filter input tcp dport 9999 accept")
    machine.wait_until_fails("nft list chain inet lf_filter input | grep -q 9999", timeout=90)
    machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = direct")

    # Freeze the supervisor only inside the VM to test packet policy fragments.
    machine.succeed("systemctl stop nftables-verify-pending.path nftables-verify.timer nftables-verify.service proxy-mode.service dns-upstream.service; systemctl mask --runtime proxy-mode.service")
    machine.succeed("python3 /etc/netsec-events-test.py /etc/netsec-events.py")
    machine.succeed("python3 /etc/netsec-listeners-test.py check")
    machine.succeed("/etc/netsec-listener-check dns")
    # Existing dedicated gateway listeners survive; resolved has no wildcard
    # port 53. These checks observe both transports, not just the TCP half.
    machine.fail("ss -Hlntu 'sport = :53' | grep -Eq '0.0.0.0:53|\\[::\\]:53|\\*:53'")
    machine.succeed("printf 'server=127.0.0.1#1055\\n' >/run/dns-upstream/servers.conf; systemctl reload dnsmasq.service")
    # Inspect real processes, then exercise both sides of the hardened DNS path.
    for unit in ("dnsmasq", "unbound"):
        pid = machine.succeed(f"systemctl show --value -p MainPID {unit}").strip()
        status = dict(line.split(":", 1) for line in machine.succeed(f"cat /proc/{pid}/status").splitlines() if ":" in line)
        assert status["NoNewPrivs"].strip() == "1", (unit, status)
        assert int(status["CapBnd"], 16) & ((1 << 16) | (1 << 19) | (1 << 21)) == 0, unit
        if unit == "unbound":
            for field in ("CapPrm", "CapEff", "CapBnd", "CapAmb"):
                assert int(status[field], 16) == 0, (field, status[field])
    machine.wait_for_unit("netsec-rebind-fixture.service")
    machine.wait_for_unit("netsec-dnssec-fixture.service")
    machine.succeed("NETSEC_DNSSEC_PROBE_NAME=signed.fixture.example. python3 /etc/netsec-dnssec-probe.py 1055 | jq -e '.status == \"secure\"'")
    for transport in ("", "+tcp"):
        response = machine.succeed(f"dig {transport} +dnssec +adflag +time=2 +tries=1 @127.0.0.1 -p 1055 valid.signed.fixture.example A")
        assert "status: NOERROR" in response and " ad" in response, response
        machine.succeed(f"dig {transport} +short +time=2 +tries=1 @127.0.0.1 -p 1054 valid.signed.fixture.example A | grep -qx 203.0.113.42")
        # A blocked name is answered NXDOMAIN at the front door, before either
        # upstream is consulted; dnsmasq's suffix match covers subdomains too.
        for blocked in ("ads.fixture.example", "pixel.ads.fixture.example",
                        "blocked.fixture.test", "sub.blocked.fixture.test"):
            response = machine.succeed(f"dig {transport} +time=2 +tries=1 @127.0.0.1 -p 1054 {blocked} A")
            assert "status: NXDOMAIN" in response, (blocked, response)
        # An excepted name inside a blocked domain is forwarded to the standard
        # servers instead of the blocklist, and its subdomains follow it.
        response = machine.succeed(f"dig {transport} +time=2 +tries=1 @127.0.0.1 -p 1054 ok.blocked.fixture.test A")
        assert "status: NOERROR" in response, response
        for name, address in (("ok.blocked.fixture.test", "192.0.2.43"),
                              ("a.ok.blocked.fixture.test", "192.0.2.44")):
            machine.succeed(
                f"dig {transport} +short +time=2 +tries=1 @127.0.0.1 -p 1054 {name} A | grep -qx {address}"
            )
        for checking in ("", "+cdflag"):
            response = machine.succeed(f"dig {transport} {checking} +time=2 +tries=1 @127.0.0.1 -p 1054 bogus.signed.fixture.example A")
            assert "status: SERVFAIL" in response, response
    # Only the validating resolver may open an encrypted DNS channel of its
    # own: uid 0 for diagnostics and unbound's uid get through, everything else
    # is dropped so no app can step outside the resolver policy over 853. The
    # listener sits in the peer namespace, so this is a real public-range
    # destination rather than one of the private ranges the output chain
    # accepts unconditionally.
    machine.succeed("systemd-run --unit=netsec-dot-fixture --working-directory=/tmp ip netns exec outside ${pkgs.python3}/bin/python3 -m http.server 853 --bind 203.0.113.10")
    machine.wait_for_unit("netsec-dot-fixture.service")
    machine.wait_until_succeeds("nc -z -w 2 203.0.113.10 853", timeout=30)
    machine.succeed("setpriv --reuid=983 --clear-groups nc -z -w 2 203.0.113.10 853")
    drops = dot_guard_drops()
    machine.fail("setpriv --reuid=1000 --clear-groups nc -z -w 2 203.0.113.10 853")
    assert dot_guard_drops() > drops, "the 853 drop counter did not move: the guard was not what refused the flow"
    # A real validation failure must revoke the old grant, and the host must not
    # lose DNS for it: the verdict is published and a bounded plaintext window
    # takes over while the encrypted path keeps being retried.
    machine.succeed("python3 /etc/netsec-dns.py lease 100.67.0.1 120; systemctl start dns-policy.service; nft get element inet lf_filter dns_plaintext4 '{ 100.67.0.1 }'")
    machine.succeed("rm -f /run/dns-upstream/budgets/*; mkdir -p /run/systemd/system/dns-upstream.service.d; printf '[Service]\\nEnvironment=NETSEC_DNSSEC_PROBE_NAME=bogus.signed.fixture.example.\\nEnvironment=NETSEC_DNSSEC_PROBE_TYPE=1\\nEnvironment=NETSEC_DOT_PRIME_ROUNDS=0\\n' >/run/systemd/system/dns-upstream.service.d/dnssec-test.conf; systemctl daemon-reload; systemctl start dns-upstream.service")
    machine.wait_until_succeeds("jq -e '.dnssec_status == \"validation-error\" and .mode == \"bootstrap-plaintext\" and .window_until > 0' /run/dns-upstream/state.json", timeout=60)
    machine.fail("nft get element inet lf_filter dns_plaintext4 '{ 100.67.0.1 }'")
    # Plaintext is billed against the pool one second per second, so a window
    # left open must visibly shrink the remaining allowance.
    pool_start = int(machine.succeed("jq -r .budget_remaining /run/dns-upstream/state.json"))
    assert 0 < pool_start <= 1800, pool_start
    time.sleep(10)
    pool_spent = int(machine.succeed("jq -r .budget_remaining /run/dns-upstream/state.json"))
    assert pool_spent < pool_start - 4, (pool_start, pool_spent)
    machine.succeed("test $(jq -r .window_until /run/dns-upstream/state.json) -le $(( $(jq -r .monotonic_seconds /run/dns-upstream/state.json) + 120 )); rm /run/systemd/system/dns-upstream.service.d/dnssec-test.conf; systemctl daemon-reload; systemctl restart dns-upstream.service")
    machine.wait_until_succeeds("test $(jq -r .mode /run/dns-upstream/state.json) = dot && test $(jq -r .dnssec_status /run/dns-upstream/state.json) = secure", timeout=60)
    # A validated encrypted path earns the pool back, but a later failure must
    # continue from what is left instead of being handed a fresh 1800 s.
    machine.wait_until_succeeds(f"test $(jq -r .budget_remaining /run/dns-upstream/state.json) -gt {pool_spent}", timeout=90)
    assert int(machine.succeed("jq -r .budget_remaining /run/dns-upstream/state.json")) <= 1800
    machine.succeed("printf '[Service]\\nEnvironment=NETSEC_DNSSEC_PROBE_NAME=bogus.signed.fixture.example.\\nEnvironment=NETSEC_DNSSEC_PROBE_TYPE=1\\nEnvironment=NETSEC_DOT_PRIME_ROUNDS=0\\n' > /run/systemd/system/dns-upstream.service.d/dnssec-test.conf; systemctl daemon-reload; systemctl restart dns-upstream.service")
    machine.wait_until_succeeds("jq -e '.mode == \"bootstrap-plaintext\"' /run/dns-upstream/state.json", timeout=60)
    time.sleep(8)
    flap_pool = int(machine.succeed("jq -r .budget_remaining /run/dns-upstream/state.json"))
    assert 0 < flap_pool < 1800, flap_pool
    machine.succeed("rm /run/systemd/system/dns-upstream.service.d/dnssec-test.conf; systemctl daemon-reload; systemctl stop dns-upstream.service")
    # A slow/unreachable local validator is unknown, not a signature verdict. It
    # must revoke old grants without locking the host out of DNS.
    machine.succeed("rm -f /run/dns-upstream/budgets/*; python3 /etc/netsec-dns.py lease 100.67.0.1 120; systemctl start dns-policy.service")
    machine.succeed("mkdir -p /run/systemd/system/dns-upstream.service.d; printf '[Service]\\nEnvironment=NETSEC_DOT_PRIME_ROUNDS=0\\nEnvironment=NETSEC_PROBE_TRANSPORT_TIMEOUT=0.5\\n' >/run/systemd/system/dns-upstream.service.d/probe-test.conf; systemctl daemon-reload")
    machine.succeed("nft add table inet netsec_probe_blackhole; nft 'add chain inet netsec_probe_blackhole input { type filter hook input priority -195; policy accept; }'; nft add rule inet netsec_probe_blackhole input ip daddr 127.0.0.1 udp dport 1055 drop; systemctl start dns-upstream.service")
    machine.wait_until_succeeds("jq -e '.dnssec_probe.local.detail == \"local-timeout\" and (.dnssec_status == \"indeterminate\" or .dnssec_status == \"unavailable\") and .mode == \"bootstrap-plaintext\" and .window_until > 0' /run/dns-upstream/state.json", timeout=60)
    machine.fail("nft get element inet lf_filter dns_plaintext4 '{ 100.67.0.1 }'")
    machine.succeed("nft delete table inet netsec_probe_blackhole; rm /run/systemd/system/dns-upstream.service.d/probe-test.conf; systemctl daemon-reload")
    machine.wait_until_succeeds("jq -e '.mode == \"dot\" and .dnssec_probe.detail == \"authenticated\"' /run/dns-upstream/state.json", timeout=60)
    machine.succeed("systemctl stop dns-upstream.service")
    # Actual resolved link configuration, with an authenticated-tailnet CLI
    # fixture. No public route or unrelated suffix may survive reconciliation.
    machine.succeed("ip link add tailscale0 type dummy; mkdir -p /run/netsec-tailnet; printf '%s' '{\"BackendState\":\"Running\",\"CurrentTailnet\":{\"MagicDNSSuffix\":\"tailfixture.ts.net\",\"MagicDNSEnabled\":true}}' >/run/netsec-tailnet-status.json")
    machine.succeed("python3 /etc/netsec-resolved-policy.py /etc/netsec-tailnet.json; python3 /etc/netsec-resolved-policy.py /etc/netsec-tailnet.json --check")
    machine.succeed("resolvectl domain tailscale0 | grep -q tailfixture.ts.net; resolvectl default-route tailscale0 | grep -q no")
    machine.succeed("resolvectl domain tailscale0 '~.'")
    machine.fail("python3 /etc/netsec-resolved-policy.py /etc/netsec-tailnet.json --check")
    machine.succeed("python3 /etc/netsec-resolved-policy.py /etc/netsec-tailnet.json; printf '%s' '{\"BackendState\":\"Stopped\"}' >/run/netsec-tailnet-status.json; python3 /etc/netsec-resolved-policy.py /etc/netsec-tailnet.json; test ! -e /run/netsec-tailnet/domain")
    machine.fail("resolvectl domain tailscale0 | grep -q tailfixture.ts.net")
    machine.succeed("ip link del tailscale0")
    for transport in ("", "+tcp"):
        machine.succeed("systemctl reload dnsmasq.service")
        for label, rrtype, expected in (("public", "A", "203.0.113.42"),
                                        ("public6", "AAAA", "2001:db8::42"),
                                        ("mapped-public", "AAAA", "::ffff:203.0.113.42"),
                                        ("allowed", "A", "192.168.1.2")):
            value = machine.succeed(f"dig {transport} +time=2 +tries=1 +short @127.0.0.1 -p 1054 {label}.rebind.fixture.example {rrtype}").strip()
            assert ipaddress.ip_address(value) == ipaddress.ip_address(expected), (label, value)
        for label, rrtype in (("private", "A"), ("cgnat", "A"), ("loopback", "A"),
                              ("unspecified", "A"), ("linklocal", "A"), ("ula", "AAAA"),
                              ("loopback6", "AAAA"), ("unspecified6", "AAAA"),
                              ("linklocal6", "AAAA"), ("mapped", "AAAA"), ("mapped-cgnat", "AAAA")):
            value = machine.succeed(f"dig {transport} +time=2 +tries=1 +short @127.0.0.1 -p 1054 {label}.rebind.fixture.example {rrtype}").strip()
            assert not value, (label, value)
        value = machine.succeed(f"dig {transport} +time=2 +tries=1 +short @127.0.0.1 -p 1054 alias.rebind.fixture.example A").strip()
        assert "192.168.1.1" not in value, value
    for transport in ["", "+tcp"]:
        machine.succeed(f"dig {transport} +time=1 +tries=1 +short @127.0.0.1 -p 1055 fixture.test A | grep -qx 192.0.2.42")
        machine.succeed(f"setpriv --reuid=985 --clear-groups dig {transport} +time=1 +tries=1 +short @127.0.0.1 -p 1055 fixture.test A | grep -qx 192.0.2.42")
        machine.fail(f"setpriv --reuid=1000 --clear-groups dig {transport} +time=1 +tries=1 @127.0.0.1 -p 1055 fixture.test A")
        machine.succeed(f"setpriv --reuid=1000 --clear-groups dig {transport} +time=1 +tries=1 +short @203.0.113.10 fixture.test A | grep -qx 192.0.2.42")

    # A failed required pre-sleep hook cancels the transaction. ExecStopPost
    # still queues recovery; a failed revoke keeps retrying without regranting.
    machine.succeed("touch /run/fail-sleep-nft")
    machine.fail("systemctl start netsec-test-sleep-hold.service")
    machine.succeed("test -e /run/dns-policy/sleeping")
    machine.fail("systemctl is-active --quiet sleep.target")
    machine.wait_until_succeeds("systemctl show -p NRestarts --value dns-policy-resume.service | grep -Eq '^[1-9][0-9]*$'")
    machine.succeed("test -e /run/dns-policy/sleeping")
    machine.succeed("rm /run/fail-sleep-nft")
    machine.wait_until_succeeds("test ! -e /run/dns-policy/sleeping")
    machine.wait_until_succeeds("test ! -e /run/netsec/alerts/dns-policy-sleep.service.json; test ! -e /run/netsec/alerts/dns-policy-resume.service.json")
    machine.succeed("systemctl start netsec-test-sleep-hold.service")
    machine.succeed("test -e /run/dns-policy/sleeping; systemctl start dns-policy.service; test $(jq -r .status /run/dns-policy/state.json) = suspended")
    machine.succeed("systemctl stop netsec-test-sleep-hold.service")
    machine.wait_until_succeeds("test ! -e /run/dns-policy/sleeping")
    machine.succeed("ip netns exec outside sh -c 'python3 -m http.server 8080 --bind 0.0.0.0 >/tmp/outside-http.log 2>&1 &'")
    machine.wait_until_succeeds("curl --noproxy '*' -fsS --connect-timeout 1 http://203.0.113.10:8080 >/dev/null")
    machine.succeed("ip link add virbr0 type bridge; ip addr add 192.168.122.1/24 dev virbr0; ip link set virbr0 up")
    machine.succeed("ip netns add guest; ip link add guest-host type veth peer name guest0; ip link set guest-host master virbr0; ip link set guest-host up; ip link set guest0 netns guest")
    machine.succeed("ip -n guest link set guest0 up; ip -n guest link set lo up")
    machine.succeed("systemd-run --unit=guest-dhcp-fixture /run/current-system/sw/bin/dnsmasq --keep-in-foreground --conf-file=/dev/null --pid-file=/run/guest-fixture.pid --bind-dynamic --interface=virbr0 --except-interface=lo --listen-address=192.168.122.1 --no-resolv --no-hosts --dhcp-range=192.168.122.10,192.168.122.20,1h --address=/fixture.test/192.0.2.10")
    machine.wait_until_succeeds("ss -uln | grep -q 192.168.122.1:53")
    machine.succeed("ip netns exec guest dhcpcd -B -1 -4 -t 10 --config /etc/guest-dhcpcd.conf guest0")
    machine.succeed("ip -n guest -4 addr show dev guest0 | grep -q 192.168.122.")
    machine.succeed("ip netns exec guest dig +time=1 +tries=1 @192.168.122.1 fixture.test A +short | grep -qx 192.0.2.10")
    machine.succeed("ip netns exec guest dig +tcp +time=1 +tries=1 @192.168.122.1 fixture.test A +short | grep -qx 192.0.2.10")
    machine.fail("ip netns exec guest curl --noproxy '*' -fsS --max-time 2 http://192.168.122.1:8080/")
    machine.succeed("systemd-run --unit=netsec-host-echo python3 /etc/netsec-guest-services.py server; systemd-run --unit=netsec-peer-echo ip netns exec outside /run/current-system/sw/bin/python3 /etc/netsec-guest-services.py server")
    machine.wait_until_succeeds("ss -ltn | grep -q ':18081 '", timeout=10)
    machine.wait_until_succeeds("ip netns exec outside ss -ltn | grep -q ':18081 '", timeout=10)
    machine.succeed("nft insert rule inet lf_filter lf_guest_host iifname virbr0 ip saddr 192.168.122.0/24 ip daddr 192.168.122.1 tcp dport 18081 accept")
    machine.succeed("systemd-run --unit=netsec-guest-host ip netns exec guest /run/current-system/sw/bin/python3 /etc/netsec-guest-services.py client 192.168.122.1 guest-host; systemd-run --unit=netsec-guest-private ip netns exec guest /run/current-system/sw/bin/python3 /etc/netsec-guest-services.py client 100.67.0.1 guest-private")
    machine.wait_until_succeeds("test -e /tmp/guest-host.ready -a -e /tmp/guest-private.ready", timeout=15)
    machine.succeed("nft -f /etc/netsec-private-rules.nft; touch /tmp/guest-host.next /tmp/guest-private.next")
    machine.wait_until_succeeds("grep -qx blocked /tmp/guest-host.result && grep -qx blocked /tmp/guest-private.result", timeout=10)
    machine.succeed("ip netns exec guest curl --noproxy '*' -fsS --max-time 2 http://100.67.0.1:8080/ >/dev/null")
    machine.fail("ip netns exec guest curl --noproxy '*' -fsS --max-time 2 http://100.67.0.1:18081/")
    machine.succeed("ip netns exec guest dig +short +time=1 +tries=1 @192.168.122.1 fixture.test A | grep -qx 192.0.2.10; /etc/netsec-mode-script --reload")
    machine.succeed("ip -n guest addr flush dev guest0; ip -n guest addr add 192.168.122.2/24 dev guest0; ip -n guest route replace default via 192.168.122.1")

    # A DNS permission expires even while its controller is stopped. Both the
    # socket UID and the private destination are real kernel facts in this VM.
    machine.succeed("ip netns exec outside sh -c 'python3 /etc/netsec-dns.py server 53 --dns-answer 192.0.2.99 >/tmp/dns-peer.log 2>&1 &'")
    machine.wait_until_succeeds("ip netns exec outside ss -uln | grep -q ':53 '")
    machine.fail("python3 /etc/netsec-dns.py query 100.67.0.1 --uid 985")
    # Allow for service activation on loaded runners, then observe expiry
    # instead of assuming the grant itself finishes within three seconds.
    machine.succeed("python3 /etc/netsec-dns.py lease 100.67.0.1 15; systemctl start dns-policy.service")
    machine.succeed("python3 /etc/netsec-dns.py query 100.67.0.1 --uid 985")
    machine.wait_until_fails("python3 /etc/netsec-dns.py query 100.67.0.1 --uid 985", timeout=30)
    machine.succeed("python3 /etc/netsec-dns.py lease 100.67.0.1 120; systemctl start dns-policy.service")

    # Wi-Fi is an uplink, including for global v4/v6 replies. A marked fixture
    # models the core's transport without depending on an internet node. The v6
    # prefix must not overlap the test driver's own vlan network (2001:db8:1::/64).
    machine.succeed("ip link add wlo1 type veth peer name wifi-peer; ip link set wifi-peer netns outside; ip link set wlo1 up")
    machine.succeed("ip addr add 198.51.100.2/24 dev wlo1; ip -6 addr add 2001:db8:dead::2/64 dev wlo1 nodad")
    machine.succeed("ip -n outside link set wifi-peer up; ip -n outside addr add 198.51.100.1/24 dev wifi-peer; ip -n outside -6 addr add 2001:db8:dead::1/64 dev wifi-peer nodad")
    machine.succeed("ip addr add 10.1.2.2/24 dev wlo1; ip -n outside addr add 10.1.2.53/24 dev wifi-peer")
    machine.succeed("python3 /etc/netsec-split-test.py")
    machine.succeed("ip netns exec outside sh -c 'python3 /etc/netsec-dns.py server 15353 >/tmp/wifi-peer.log 2>&1 &'")
    machine.wait_until_succeeds("python3 /etc/netsec-dns.py query 198.51.100.1 15353 --mark 6666")
    machine.succeed("python3 /etc/netsec-dns.py query 2001:db8:dead::1 15353 --mark 6666")
    machine.wait_until_succeeds("ip netns exec guest curl --noproxy '*' -fsS --connect-timeout 1 http://203.0.113.10:8080 >/dev/null")
    machine.succeed("nft -f /etc/netsec-mode-b.nft")
    machine.succeed("python3 /etc/netsec-ntp.py 61")
    machine.fail("python3 /etc/netsec-ntp.py 1000")
    machine.succeed("ip netns exec guest dig +time=1 +tries=1 @192.168.122.1 fixture.test A +short | grep -qx 192.0.2.10")
    machine.succeed("ip netns exec guest dig +tcp +time=1 +tries=1 @192.168.122.1 fixture.test A +short | grep -qx 192.0.2.10")
    machine.fail("python3 /etc/netsec-dns.py query 100.67.0.1 --uid 985")
    machine.succeed("python3 /etc/netsec-dns.py query 198.51.100.1 15353 --mark 6666")
    machine.succeed("python3 /etc/netsec-dns.py query 2001:db8:dead::1 15353 --mark 6666")
    machine.succeed("systemd-run --unit=netsec-tproxy /run/current-system/sw/bin/python3 /etc/netsec-tproxy.py server")
    machine.wait_until_succeeds("ip netns exec guest curl --noproxy '*' -fsS --connect-timeout 1 http://203.0.113.10:8080 | grep -qx OK")
    machine.succeed("systemd-run --unit=netsec-guest ip netns exec guest /run/current-system/sw/bin/python3 /etc/netsec-tproxy.py client")
    machine.wait_until_succeeds("test -e /tmp/guest-ready")
    machine.succeed("nft -f /etc/netsec-mode-c.nft")
    machine.fail("python3 /etc/netsec-ntp.py 61")
    machine.succeed("ip netns exec guest dig +time=1 +tries=1 @192.168.122.1 fixture.test A +short | grep -qx 192.0.2.10")
    # TCP DNS replies come from the accepted socket, whose sk_uid is the
    # dropped dnsmasq uid: the reply rules must not be uid-scoped.
    machine.succeed("ip netns exec guest dig +tcp +time=1 +tries=1 @192.168.122.1 fixture.test A +short | grep -qx 192.0.2.10")
    machine.succeed("python3 /etc/netsec-dns.py query 2001:db8:dead::1 15353 --mark 6666")
    machine.succeed("touch /tmp/guest-next")
    machine.wait_until_succeeds("grep -qx blocked /tmp/guest-result")
    machine.fail("ip netns exec guest curl --noproxy '*' -fsS --connect-timeout 1 http://203.0.113.10:8080 >/dev/null")
    machine.succeed("ip netns exec guest curl --noproxy '*' -fsS --connect-timeout 1 http://100.67.0.1:8080 >/dev/null")
    machine.fail("su -s /bin/sh lfour -c \"curl --noproxy '*' -fsS --connect-timeout 1 http://203.0.113.10:8080\"")
    machine.fail("su -s /bin/sh lfour -c \"curl --noproxy '*' -fsS --connect-timeout 1 http://127.0.0.1:7897\"")
    machine.succeed("echo proxy >/run/proxy-mode/intent; /etc/netsec-mode-script --reload; systemctl unmask --runtime proxy-mode.service; systemctl start proxy-mode.service nftables-verify-pending.path nftables-verify.timer")
    machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = blocked")
    machine.wait_until_succeeds("test $(jq -r .result /run/netsec/state.json) = ok && test $(jq -r .version /run/netsec/state.json) = $(jq -r .policy_version /run/proxy-mode/state.json)", timeout=90)
    # A commit and a second request during liveness checks must leave a rerun,
    # rather than being swallowed by an already-running oneshot service.
    machine.succeed("systemctl stop unbound.service; touch /run/netsec/requests/request.first")
    machine.wait_until_succeeds("find /run/netsec -maxdepth 1 -name 'rules.*.json' | grep -q .")
    machine.succeed("touch /run/netsec/requests/request.second; /etc/netsec-mode-script --reload; systemctl start unbound.service")
    machine.wait_until_succeeds("test -z \"$(ls -A /run/netsec/requests)\"", timeout=90)
    machine.succeed("test $(jq -r .version /run/netsec/state.json) = $(jq -r .version /run/proxy-mode/state.json)")
    # Exercise core TUN/DNS and SOCKS behavior in isolated namespaces.
    for family in ("ipv4", "dual"):
        machine.succeed(f"ip netns add core-{family}")
        machine.succeed(f"NETSEC_CORE_PATH=${pkgs.lib.makeBinPath [ pkgs.iproute2 pkgs.nftables ]} ip netns exec core-{family} python3 /etc/netsec-core.py ${core}/bin/mihomo {family}")
        machine.succeed(f"ip netns delete core-{family}")

    # Real managed core, real supervisor and real listeners on the same stack.
    # Its SOCKS node and all destinations live in the outside namespace.
    machine.succeed("systemctl stop clash-verge.service netsec-tproxy.service; python3 /etc/netsec-integration.py prepare")
    machine.succeed("ip route replace default via 100.67.0.1 dev ens1 metric 1")
    # Model remote public hosts, reached through defaults rather than connected
    # or explicit main-table routes (sing-tun intentionally preserves those).
    machine.succeed("ip route del 203.0.113.10/32; ip -n outside -6 addr add 2001:db8:ffff::10/128 dev wifi-peer nodad; ip -6 route replace default via 2001:db8:dead::1 dev wlo1 metric 1")
    machine.succeed("ip netns exec outside sh -c 'python3 /etc/netsec-dns.py server 15354 --dns-answer 203.0.113.10 --rebind >/tmp/integration-dns.log 2>&1 &'")
    machine.succeed("ip netns exec outside sh -c 'python3 -m http.server 80 --bind 0.0.0.0 >/tmp/integration-tun-http.log 2>&1 &'")
    machine.succeed("ip netns exec outside sh -c 'python3 /etc/netsec-integration.py http6 >/tmp/integration-http6.log 2>&1 &'")
    machine.wait_until_succeeds("ip netns exec outside curl --noproxy '*' -fsS http://[2001:db8:dead::1]:8080/ >/dev/null")
    machine.succeed("ip netns exec outside sh -c '${core}/bin/mihomo -d /run/netsec-node -f /run/netsec-node.json >/tmp/netsec-node.log 2>&1 &'")
    machine.wait_until_succeeds("ip netns exec outside ss -ltn | grep -q ':18080 '")
    machine.succeed("systemctl start clash-verge.service dns-upstream.service")
    wait_proxy()
    machine.succeed("/etc/netsec-tun-check")
    # SO_BINDTODEVICE must survive transparent TCP DNAT. Redirecting these
    # sockets to loopback used to send 127/8 into the TUN and cause martians.
    martians_before = machine.succeed("journalctl -k --no-pager | grep 'martian destination' | grep -c Mihomo || true").strip()
    for family, address in ((4, "203.0.113.10"), (6, "[2001:db8:ffff::10]")):
        machine.succeed(f"setpriv --reuid=1000 --clear-groups curl -{family} --interface if!Mihomo --noproxy '*' -fsS --max-time 5 http://{address}:8080/ >/dev/null")
    assert machine.succeed("journalctl -k --no-pager | grep 'martian destination' | grep -c Mihomo || true").strip() == martians_before
    machine.succeed("test $(cat /proc/sys/net/ipv4/conf/all/route_localnet) = 0; test $(cat /proc/sys/net/ipv4/conf/Mihomo/route_localnet) = 0")
    for address in ("203.0.113.10", "[2001:db8:ffff::10]"):
        machine.succeed(f"setpriv --reuid=1000 --clear-groups curl --noproxy '*' -fsS --max-time 5 http://{address}:8080/ >/dev/null")
    for address in ("203.0.113.10", "2001:db8:ffff::10"):
        machine.succeed(f"python3 /etc/netsec-dns.py query {address} 15353 --uid 1000")
    machine.succeed("python3 /etc/netsec-integration.py check-node-log")
    machine.succeed("python3 /etc/netsec-integration.py connect-test")
    # MagicDNS names must retain real IPs so Tailscale's routes can win over
    # the TUN. Its DNS transport stays DIRECT even under a GLOBAL profile.
    machine.succeed("dig +short @127.0.0.1 -p 1053 printer.tailfixture.ts.net A | grep -qx 203.0.113.10")
    for name in ("private.core.fixture.example", "mixed.core.fixture.example"):
        machine.fail(f"setpriv --reuid=1000 --clear-groups curl --noproxy \"\" --socks5-hostname 127.0.0.1:7897 -fsS --max-time 3 http://{name}:8080/")
        fake = machine.succeed(f"dig +short @127.0.0.1 -p 1053 {name} A").strip()
        assert ipaddress.ip_address(fake) in ipaddress.ip_network("198.18.0.0/16"), fake
        machine.fail(f"setpriv --reuid=1000 --clear-groups curl --noproxy '*' -fsS --max-time 3 http://{fake}:8080/")
    machine.succeed("curl --noproxy \"\" --socks5-hostname 127.0.0.1:7897 -fsS --max-time 5 http://allowed.home.arpa:8080/ >/dev/null; python3 /etc/netsec-integration.py guarded-udp")
    # Global mode must expose its real leaf too, including nested LoadBalance.
    socket = "/run/user/1000/clash-verge-rev/verge-mihomo.sock"
    machine.succeed(f"curl --noproxy '*' -fsS --unix-socket {socket} -X PUT -H 'Content-Type: application/json' -d '{{\"name\":\"Choice\"}}' http://localhost/proxies/GLOBAL")
    machine.succeed(f"curl --noproxy '*' -fsS --unix-socket {socket} -X PATCH -H 'Content-Type: application/json' -d '{{\"mode\":\"global\"}}' http://localhost/configs")
    # Execute a fresh complete health round while global mode is active; an old
    # rule-mode health.json or an unrelated GLOBAL connection is insufficient.
    machine.succeed("if python3 /etc/netsec-health.py /etc/netsec-health.json >/tmp/netsec-global-health.json; then jq -e .ok /tmp/netsec-global-health.json; else cat /tmp/netsec-global-health.json; exit 1; fi")
    machine.succeed("journalctl -u clash-verge --no-pager | grep 'node.fixture.example:8080' | grep -q 'GLOBAL\\[Fixture\\]'")
    machine.fail("curl --noproxy \"\" --socks5-hostname 127.0.0.1:7897 -fsS --max-time 3 http://private.core.fixture.example:8080/")
    machine.succeed("python3 /etc/netsec-integration.py guarded-udp")
    machine.succeed("dig +short @127.0.0.1 -p 1053 other.tailfixture.ts.net A | grep -qx 203.0.113.10")
    machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = proxy && test $(jq -r .ok /run/proxy-mode/health.json) = true", timeout=90)
    machine.succeed(f"curl --noproxy '*' -fsS --unix-socket {socket} -X PATCH -H 'Content-Type: application/json' -d '{{\"mode\":\"rule\"}}' http://localhost/configs")
    # Keep the original table/rules, but make a higher-priority rule win.
    machine.succeed("ip -6 rule add pref 100 lookup main")
    machine.fail("/etc/netsec-tun-check")
    machine.wait_until_succeeds("test $(jq -r .mode /run/proxy-mode/state.json) = blocked && grep -qx no-tun /run/proxy-mode/reason", timeout=45)
    machine.succeed("ip -6 rule show | grep -q 'lookup 2022' && ip -6 route show table 2022 | grep -q 'dev Mihomo'")
    machine.fail("python3 /etc/netsec-dns.py query 2001:db8:ffff::10 15353")
    machine.fail("python3 /etc/netsec-dns.py query 2001:db8:ffff::10 15353 --uid 1000")
    machine.fail("setpriv --reuid=1000 --clear-groups curl --noproxy '*' -fsS --max-time 2 http://[2001:db8:ffff::10]:8080/")
    for family, address in ((4, "203.0.113.10"), (6, "[2001:db8:ffff::10]")):
        machine.fail(f"setpriv --reuid=1000 --clear-groups curl -{family} --interface if!Mihomo --noproxy '*' -fsS --max-time 2 http://{address}:8080/")
    machine.succeed("ip -6 rule del pref 100 lookup main")
    wait_proxy()
    machine.wait_until_succeeds("test $(jq -r .result /run/netsec/state.json) = ok && test $(jq -r .version /run/netsec/state.json) = $(jq -r .policy_version /run/proxy-mode/state.json)", timeout=90)
    machine.succeed("jq -e '.boot_id and (.monotonic_seconds > 0)' /run/netsec/state.json")
  '';
}
