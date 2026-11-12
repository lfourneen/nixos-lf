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
    ];

    users.users.lfour = { isNormalUser = true; uid = 1000; };
    users.groups.gost = { };
    users.users.gost = { isSystemUser = true; group = "gost"; uid = config.my.machine.uids.gost; };
    users.groups.dhcpcd = { };
    users.users.dhcpcd = { isSystemUser = true; group = "dhcpcd"; };
    my.proxy.bootMode = bootMode;
    services.avahi.enable = lib.mkForce false;
    networking.interfaces.ens1.useDHCP = lib.mkForce false;
    networking.networkmanager.unmanaged = [ "interface-name:eth*" ];

    # Exercise the production loader without asynchronous repairs or external
    # DNS/core/network dependencies. The broad VM covers first-load failure and
    # the real helper/core VM covers B recovery after the safety C transaction.
    systemd.services = lib.genAttrs [ "proxy-mode" "tailscaled" "tailscaled-set"
      "dhcpcd" "dnsmasq" "unbound" "chronyd" ] (_: { enable = lib.mkForce false; });

    environment.etc."firewall-mode".source = config.my.proxy.modeScript;
    environment.etc."firewall-expected".source = config.my.proxy.expectedRules;
    environment.etc."firewall-check".source = config.my.proxy.rulesCheck;
    environment.etc."firewall-static".source = pkgs.writeText "lf-static-rules.nft" config.networking.nftables.ruleset;
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

    start_all()
    for machine, mode in [(direct, "direct"), (blocked, "blocked")]:
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
  '';
}
