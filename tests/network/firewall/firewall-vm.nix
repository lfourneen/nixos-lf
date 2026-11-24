{ pkgs }:

let
  node = bootMode: { config, lib, pkgs, ... }: {
    imports = [
      ../../../system/programs/clash/service/identity.nix
      ../../../system/config/machine.nix
      ../../../system/config/network/options.nix
      ../../../system/config/network/proxy/options.nix
      ../../../system/config/network/proxy/profile.nix
      ../../../system/config/network/network.nix
      ../../../system/config/network/proxy/coordinator.nix
      ../../../system/config/network/firewall/bootstrap.nix
      ../../../system/config/network/firewall/alert.nix
      ../../../system/config/network/firewall/verify.nix
      ../../../system/config/network/diagnostics
    ];

    users.users.lfour = { isNormalUser = true; uid = 1000; };
    users.groups.gost = { };
    users.users.gost = { isSystemUser = true; group = "gost"; uid = config.my.machine.uids.gost; };
    users.groups.dhcpcd = { };
    users.users.dhcpcd = { isSystemUser = true; group = "dhcpcd"; };
    my.proxy.bootMode = bootMode;
    services.avahi.enable = lib.mkForce false;
    my.hardening.guestNetworks.${config.my.machine.vmBridge} = {
      sourceCidrs = [ config.my.machine.vmSubnet "fd42:122::/64" ];
      gateways = [ config.my.machine.vmAddress "fd42:122::1" ];
    };
    networking.interfaces.ens1.useDHCP = lib.mkForce false;
    networking.networkmanager.unmanaged = [ "interface-name:eth*" ];

    # Exercise the production loader without asynchronous repairs or external
    # DNS/core/network dependencies. The broad VM covers first-load failure and
    # the real helper/core VM covers B recovery after the safety C transaction.
    systemd.services = lib.genAttrs [ "proxy-mode" "tailscaled" "tailscaled-set"
      "dhcpcd" "dnsmasq" "unbound" "chronyd" ] (_: { enable = lib.mkForce false; }) // {
        nftables-verify.wantedBy = lib.mkForce [ ];
      };
    systemd.paths.nftables-verify-pending.enable = false;
    systemd.timers.nftables-verify.enable = false;
    my.proxy.listenerCheck = lib.mkForce "${pkgs.coreutils}/bin/true";
    my.proxy.resolvedPolicySettings = pkgs.emptyFile;

    environment.etc."firewall-mode".source = config.my.proxy.modeScript;
    environment.etc."firewall-expected".source = config.my.proxy.expectedRules;
    environment.etc."firewall-check".source = config.my.proxy.rulesCheck;
    environment.etc."firewall-static".source = pkgs.writeText "lf-static-rules.nft" config.networking.nftables.ruleset;
    environment.etc."posture-fixture.py".source = ./posture_fixture.py;
    environment.etc."snapshot-nft".source = pkgs.writeShellScript "snapshot-nft" ''
      if [ "$*" = "-j list ruleset" ] && [ -f /run/snapshot-test/result ]; then
        if mkdir /run/snapshot-test/capture 2>/dev/null; then
          touch /run/snapshot-test/opened
          until [ -f /run/snapshot-test/write ]; do sleep 0.05; done
          printf ' '
          touch /run/snapshot-test/writing
          until [ -f /run/snapshot-test/finish ]; do sleep 0.05; done
        fi
        [ "$(cat /run/snapshot-test/result)" != fail ] || exit 1
      fi
      exec /run/snapshot-test/real-nft "$@"
    '';
    environment.systemPackages = [ pkgs.python3 pkgs.nftables pkgs.jq pkgs.util-linux pkgs.procps ];
    virtualisation.memorySize = 512;
  };

in
pkgs.testers.runNixOSTest {
  name = "firewall-ownership-and-migration";
  node.pkgs = pkgs.lib.mkForce (pkgs.extend (_final: prev: { unstable = prev; }));
  nodes.direct = node "direct";
  nodes.blocked = node "proxy";

  testScript = ''
    import json
    import shlex

    legacy = [("inet", "filter"), ("ip", "proxymode_nat"), ("ip6", "proxymode_nat")]
    # Shared iptables-nft and native service tables, with sets and ordered rules.
    # These are sentinels, not substitutes for the real services in other VMs.
    foreign = [("ip", "nat"), ("ip6", "nat"), ("ip", "mangle"),
               ("ip", "filter"), ("ip6", "filter"), ("inet", "mihomo"),
               ("ip", "libvirt_network"), ("ip6", "libvirt_network"),
               ("ip", "nm-shared-ap0"), ("inet", "external_policy")]

    def seed(machine, tables):
        text = "\n".join(
            f"table {family} {name} {{\n"
            "  set sentinel { type inet_service; elements = { 12345, 23456 }; }\n"
            "  chain sentinel {\n"
            "    tcp dport @sentinel counter accept\n"
            "    counter drop\n"
            "  }\n}"
            for family, name in tables
        )
        machine.succeed("printf %s " + shlex.quote(text) + " | nft -f -")

    def snapshot(machine, tables):
        return {f"{family} {name}": json.loads(machine.succeed(f"nft -j list table {family} {name}"))
                for family, name in tables}

    def assert_mode(machine, mode):
        machine.succeed(f"jq -e '.mode == \"{mode}\"' /run/proxy-mode/state.json")
        machine.succeed(f"nft -j list ruleset >/tmp/live.json; /etc/firewall-check compare /etc/firewall-expected/{mode}.json /tmp/live.json")

    for machine, mode in [(direct, "direct"), (blocked, "blocked")]:
        machine.start()
        with subtest(f"{machine.name}: ordinary loads preserve all foreign names"):
            machine.wait_for_unit("nftables.service", timeout=60)
            assert_mode(machine, mode)
            machine.fail("nft list table inet lf_bootstrap")
            seed(machine, legacy + foreign)
            original = snapshot(machine, legacy + foreign)
            for command in ["systemctl reload nftables", "systemctl restart nftables",
                            "systemctl stop nftables", "systemctl start nftables",
                            "/etc/firewall-mode --once"]:
                machine.succeed(command)
                assert snapshot(machine, legacy + foreign) == original, command
                assert_mode(machine, mode)
            machine.succeed("nft add rule inet lf_filter input tcp dport 65432 accept; /etc/firewall-mode --once")
            assert_mode(machine, mode)
            assert snapshot(machine, legacy + foreign) == original

        with subtest(f"{machine.name}: failed reload and migration retain the complete old policy"):
            before = machine.succeed("nft -s -j list ruleset")
            state = machine.succeed("cat /run/proxy-mode/state.json")
            # A valid batch with a nonexistent chain rejects the entire load,
            # including the attempted owned-table replacements and old-name cleanup.
            machine.succeed("cat /etc/firewall-static >/tmp/invalid.nft; echo 'add rule inet lf_filter nonexistent_chain counter drop' >>/tmp/invalid.nft")
            machine.succeed("mount --bind /tmp/invalid.nft $(readlink -f /etc/firewall-static)")
            try:
                for command in ["systemctl reload nftables", "proxy-control migrate-firewall"]:
                    machine.fail(command)
                    assert json.loads(machine.succeed("nft -s -j list ruleset")) == json.loads(before)
                    assert machine.succeed("cat /run/proxy-mode/state.json") == state
            finally:
                machine.succeed("umount $(readlink -f /etc/firewall-static)")
            machine.succeed("systemctl reload nftables")
            assert_mode(machine, mode)

        if mode == "blocked":
            with subtest("independent C guard holds through chain loss and failed repair"):
                machine.succeed("ip netns add guard-peer; ip link add guard-host type veth peer name guard-wire netns guard-peer; "
                                "ip addr add 192.0.2.1/30 dev guard-host; ip -6 addr add 2001:db8:122::1/64 dev guard-host nodad; "
                                "ip -n guard-peer addr add 192.0.2.2/30 dev guard-wire; "
                                "ip -n guard-peer -6 addr add 2001:db8:122::2/64 dev guard-wire nodad; "
                                "ip link set guard-host up; ip -n guard-peer link set guard-wire up")
                machine.succeed("ip netns exec guard-peer nft add table inet guard_receipts; "
                                "ip netns exec guard-peer nft 'add chain inet guard_receipts wire { type filter hook input priority -10; policy accept; }'; "
                                "ip netns exec guard-peer nft add rule inet guard_receipts wire udp dport 19001 counter")
                probe = shlex.quote("import socket\nfor family, address in ((socket.AF_INET, '192.0.2.2'), (socket.AF_INET6, '2001:db8:122::2')):\n"
                                    "    with socket.socket(family, socket.SOCK_DGRAM) as stream:\n"
                                    "        try:\n"
                                    "            stream.sendto(b'fixture', (address, 19001))\n"
                                    "        except PermissionError:\n"
                                    "            pass")
                guard = "nft -s -j list table inet lf_policy_guard"
                wire = "ip netns exec guard-peer nft list chain inet guard_receipts wire"
                independent = json.loads(machine.succeed(guard))
                support = snapshot(machine, legacy + foreign)

                def refuses_drift():
                    counters = ("nft -j list chain inet lf_policy_guard lf_guard_require_output | "
                                "jq '[.nftables[].rule?.expr[]?.counter.packets // empty] | add'")
                    drops = int(machine.succeed(counters))
                    machine.succeed(f"setpriv --reuid=1000 --clear-groups python3 -c {probe}")
                    machine.succeed(f"{wire} | grep -F 'counter packets 0 bytes 0'")
                    assert int(machine.succeed(counters)) >= drops + 2
                    assert json.loads(machine.succeed(guard)) == independent

                machine.succeed("nft flush chain inet lf_filter lf_blocked_guard")
                refuses_drift()
                machine.succeed("systemctl reload nftables")
                independent = json.loads(machine.succeed(guard))
                for attempt in range(2):
                    machine.succeed("nft delete table inet lf_filter; mount --bind /tmp/invalid.nft $(readlink -f /etc/firewall-static)")
                    try:
                        machine.fail("/etc/firewall-mode --once")
                        machine.succeed("jq -e '.mode == \"unenforced\" and .reason == \"policy-repair-failed\"' /run/proxy-mode/state.json")
                        machine.fail("nft list table inet lf_filter")
                        refuses_drift()
                        assert snapshot(machine, legacy + foreign) == support, attempt
                    finally:
                        machine.succeed("umount $(readlink -f /etc/firewall-static)")
                    machine.succeed("systemctl reload nftables")
                    assert_mode(machine, mode)
                    independent = json.loads(machine.succeed(guard))
                machine.succeed("ip link delete guard-host; ip netns delete guard-peer")

        with subtest(f"{machine.name}: migration requires root and the shared policy lock"):
            machine.fail("setpriv --reuid=1000 --clear-groups proxy-control migrate-firewall")
            assert snapshot(machine, legacy + foreign) == original
            machine.succeed("systemd-run --unit=test-policy-lock flock /run/proxy-mode/lock ${pkgs.runtimeShell} -c '${pkgs.coreutils}/bin/touch /run/policy-lock-held; ${pkgs.coreutils}/bin/sleep 30'")
            machine.wait_until_succeeds("test -e /run/policy-lock-held", timeout=10)
            machine.succeed("systemd-run --no-block --unit=test-migration --property=Type=oneshot --property=RemainAfterExit=yes proxy-control migrate-firewall")
            machine.wait_until_succeeds("pgrep -f '[/]bin/flock -x -w 10 9'", timeout=5)
            assert snapshot(machine, legacy + foreign) == original
            machine.succeed("systemctl stop test-policy-lock")
            machine.wait_for_unit("test-migration.service", timeout=10)
            machine.succeed("test $(systemctl show -p ExecMainStatus --value test-migration) = 0")
            for family, name in legacy:
                machine.fail(f"nft list table {family} {name}")
            assert snapshot(machine, foreign) == {key: value for key, value in original.items()
                                                if key not in {f"{f} {n}" for f, n in legacy}}
            assert_mode(machine, mode)

        with subtest(f"{machine.name}: migration is idempotent and never arms later cleanup"):
            machine.succeed("proxy-control migrate-firewall")
            assert_mode(machine, mode)
            seed(machine, legacy)
            reused = snapshot(machine, legacy + foreign)
            for command in ["systemctl reload nftables", "systemctl restart nftables",
                            "nft add rule inet lf_filter input tcp dport 65432 accept; /etc/firewall-mode --once"]:
                machine.succeed(command)
                assert snapshot(machine, legacy + foreign) == reused
                assert_mode(machine, mode)

        if mode == "direct":
            with subtest("live TCP/22 and unknown listeners are refused across ingress roles"):
                roles = [
                    ("lan", "ens1", "198.51.100.1/24", "198.51.100.2/24",
                     "2001:db8:12::1/64", "2001:db8:12::2/64"),
                    ("tail", "tailscale0", "100.64.0.1/24", "100.64.0.2/24",
                     "fd7a:115c:a1e0::1/64", "fd7a:115c:a1e0::2/64"),
                    ("guest", "virbr0", "192.168.122.1/24", "192.168.122.2/24",
                     "fd42:122::1/64", "fd42:122::2/64"),
                ]
                fixture = "python3 /etc/posture-fixture.py"
                sentinel = "fixture-private-credential"
                private_ca = "/tmp/fixture-private-ca"
                machine.succeed("systemd-run --unit=posture-fixture "
                                f"--setenv=HTTPS_PROXY=http://test:{sentinel}@127.0.0.1:8788 "
                                f"--setenv=NODE_EXTRA_CA_CERTS={private_ca} {fixture} server")
                machine.wait_until_succeeds(f"{fixture} client 127.0.0.1 22", timeout=10)
                machine.succeed(f"{fixture} client ::1 22")
                for role, host, host4, peer4, host6, peer6 in roles:
                    machine.succeed(f"ip netns add {role}; ip link add {host} type veth peer name peer netns {role}; "
                                    f"ip addr add {host4} dev {host}; ip -6 addr add {host6} dev {host} nodad; "
                                    f"ip -n {role} addr add {peer4} dev peer; ip -n {role} -6 addr add {peer6} dev peer nodad; "
                                    f"ip link set {host} up; ip -n {role} link set peer up")
                    for address in (host4.split("/")[0], host6.split("/")[0]):
                        machine.succeed(f"{fixture} client {address} 22")
                        for port in (22, 18080):
                            machine.fail(f"ip netns exec {role} {fixture} client {address} {port}")

                # The same live destination is reachable when the policy is
                # deliberately opened on the physical test link.
                machine.succeed("nft insert rule inet lf_filter input 'iifname \"ens1\" tcp dport 22 accept'")
                machine.succeed(f"ip netns exec lan {fixture} client 198.51.100.1 22")
                machine.succeed(f"ip netns exec lan {fixture} client 2001:db8:12::1 22")
                machine.succeed("/etc/firewall-mode --once")
                assert_mode(machine, mode)

                pid = machine.succeed("systemctl show -p MainPID --value posture-fixture.service").strip()
                cmd = f"proxy-inspect --posture --json --pid {pid}"
                output = machine.succeed(cmd)
                assert sentinel not in output and private_ca not in output, output
                report = json.loads(output)
                assert report["scope"]["host_namespace_confirmed"] is True
                assert report["openssh"]["tcp_22"] is True
                assert report["openssh"]["declared_tcp_22"] == {"lan": False, "tailnet": False, "guests": []}
                assert report["openssh"]["units"]["sshd.service"]["load"] == "not-found"
                assert report["ca_bundle"]["generation_match"] is True
                rows = {(row["family"], row["port"]): row for row in report["listeners"] if row["protocol"] == "tcp"}
                for family in (4, 6):
                    assert (family, 18080) in rows, report
                    listener = rows[family, 18080]
                    assert listener["binding"] == "wildcard" and listener["declaration"] == "unlisted"
                    assert listener["ingress_policy"] == {"lan": "denied", "tailnet": "denied", "guests": "denied"}
                assert rows[4, 8788]["binding"] == "loopback" and rows[4, 8788]["proxy_port_hint"]
                assert rows[4, 8788]["declaration"] == "unlisted"
                assert rows[4, 33332]["declaration"] == "identity-mismatch"
                assert rows[4, 33333]["declaration"] == "binding-mismatch"
                env = report["environment"]["processes"][0]
                assert env["available"] and {item["name"] for item in env["variables"]} >= {"HTTPS_PROXY", "NODE_EXTRA_CA_CERTS"}
                public = json.loads(machine.succeed(f"setpriv --reuid=1000 --clear-groups {cmd}"))
                assert not public["environment"]["processes"][0]["available"]
                journal = machine.succeed("journalctl -u posture-fixture --output=cat")
                assert sentinel not in journal and private_ca not in journal
                machine.succeed("systemctl stop posture-fixture.service")
                for role, host, *_ in roles:
                    machine.succeed(f"ip link delete {host}; ip netns delete {role}")

            with subtest("full snapshots remain private during capture and recover after interruption"):
                machine.succeed(
                    "mkdir -m 0700 /run/snapshot-test; "
                    "cp ${pkgs.nftables}/bin/nft /run/snapshot-test/real-nft; "
                    "mount --bind $(readlink -f /etc/snapshot-nft) ${pkgs.nftables}/bin/nft"
                )
                try:
                    for unit, directory, prefix, start in [
                        ("snapshot-coordinator", "/run/proxy-mode", "check",
                         "systemd-run --no-block --collect --unit=snapshot-coordinator --setenv=PATH=${pkgs.coreutils}/bin "
                         "--property=Type=oneshot /etc/firewall-mode --once"),
                        ("nftables-verify", "/run/netsec", "rules", "systemctl start --no-block nftables-verify"),
                    ]:
                        stale = None
                        for outcome in ["ok", "fail", "kill", "ok"]:
                            machine.succeed(
                                "rmdir /run/snapshot-test/capture 2>/dev/null || true; "
                                "rm -f /run/snapshot-test/{opened,write,writing,finish}; "
                                f"touch {directory}/{prefix}.123.json {directory}/{prefix}.123-other.json; "
                                f"echo {outcome} >/run/snapshot-test/result; {start}"
                            )
                            machine.wait_until_succeeds("test -f /run/snapshot-test/opened", timeout=10)
                            private = f"{directory}/.ruleset"
                            path = machine.succeed(f"printf '%s' {private}/{prefix}.*.json")
                            if stale:
                                machine.fail(f"test -e {stale}")
                            machine.succeed(f"test ! -e {directory}/{prefix}.123.json; test -e {directory}/{prefix}.123-other.json")
                            assert machine.succeed(f"stat -c '%a %u' {private} {path}").splitlines() == ["700 0", "600 0"]
                            machine.succeed(f"test ! -s {path}; touch /run/snapshot-test/write")
                            machine.wait_until_succeeds("test -f /run/snapshot-test/writing", timeout=10)
                            machine.succeed(f"test -s {path}")
                            for uid in [1000, machine.succeed("id -u gost").strip()]:
                                machine.fail(f"setpriv --reuid={uid} --clear-groups cat {path}")
                                machine.fail(f"setpriv --reuid={uid} --clear-groups ls {private}")
                            if outcome == "kill":
                                machine.succeed(f"systemctl kill --signal=KILL --kill-whom=all {unit}")
                                stale = path
                            else:
                                machine.succeed("touch /run/snapshot-test/finish")
                            machine.wait_until_succeeds(
                                f"systemctl show -p SubState --value {unit} | grep -Eq '^(dead|failed)$'", timeout=15
                            )
                            if outcome == "kill":
                                machine.succeed(f"test -f {path}")
                            else:
                                machine.succeed(f"test ! -e {path}")
                                if unit == "nftables-verify":
                                    result = "failed" if outcome == "fail" else "ok"
                                    machine.succeed(
                                        f"setpriv --reuid=1000 --clear-groups jq -e '.result == \"{result}\"' "
                                        "/run/netsec/state.json"
                                    )
                                elif outcome == "ok":
                                    machine.succeed("setpriv --reuid=1000 --clear-groups cat /run/proxy-mode/state.json")
                finally:
                    machine.succeed("umount ${pkgs.nftables}/bin/nft")
        machine.shutdown()
  '';
}
