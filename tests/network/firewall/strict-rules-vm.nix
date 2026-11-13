{ pkgs, config }:

assert config.my.proxy.strictBrowser;

let
  static = pkgs.writeText "strict-static.nft" config.networking.nftables.ruleset;
  blocked = pkgs.writeText "strict-blocked.nft" config.my.proxy.blockedModeRules;
  proxy = pkgs.writeText "strict-proxy.nft" config.my.proxy.proxyModeRules;

in
pkgs.testers.runNixOSTest {
  name = "strict-browser-ordered-firewall";

  nodes.machine = { lib, pkgs, ... }: {
    imports = [ (import ../../../system/programs/clash/service/strict-browser.nix { inherit config lib pkgs; }) ];
    users.users.${config.my.machine.desktopUser} = { isNormalUser = true; uid = config.users.users.${config.my.machine.desktopUser}.uid; };
    users.groups.gost = {};
    users.users.gost = { isSystemUser = true; group = "gost"; };
    networking.firewall.enable = false;
    environment.systemPackages = [ pkgs.nftables pkgs.python3 ];
    virtualisation.memorySize = 512;
  };

  testScript = ''
    import json
    import shlex

    start_all()
    machine.wait_for_unit("multi-user.target")
    machine.wait_for_unit("clash-strict.socket")
    machine.succeed("test -S /run/clash-strict/proxy.sock")
    machine.succeed("python3 -c " + shlex.quote(
        "from pathlib import Path; text=Path('${blocked}').read_text(); "
        "Path('/tmp/legacy-blocked.nft').write_text('\\n'.join(line for line in text.splitlines() "
        "if 'lf_strict_reply_guard' not in line)+'\\n')"))
    previous = None
    checked_legacy = False
    for mode, fragment in [("direct", None), ("blocked", "${blocked}"),
                           ("proxy", "${proxy}"), ("blocked", "${blocked}"),
                           ("blocked", "${blocked}"), ("proxy", "${proxy}"),
                           ("proxy", "${proxy}"), ("direct", None)]:
        with subtest("strictBrowser=true ordered rules: " + mode):
            # Ordinary B/C transitions apply only the production fragment.
            # Reloading static rules here would hide guards lost by B's flush.
            if previous == "proxy" and mode == "blocked" and not checked_legacy:
                machine.succeed("nft -f /tmp/legacy-blocked.nft")
                machine.fail("nft -j list ruleset > /tmp/live.json; ${config.my.proxy.rulesCheck} compare ${config.my.proxy.expectedRules}/blocked.json /tmp/live.json > /tmp/legacy-difference.log 2>&1")
                difference = machine.succeed("cat /tmp/legacy-difference.log")
                assert "lf_strict_reply_guard" in difference, difference
                checked_legacy = True
            machine.succeed("nft -f " + (fragment or "${static}"))
            machine.succeed("nft -j list ruleset > /tmp/live.json")
            machine.succeed("${config.my.proxy.rulesCheck} compare ${config.my.proxy.expectedRules}/" + mode + ".json /tmp/live.json")
            chain = json.loads(machine.succeed("nft -j list chain inet lf_filter lf_strict_reply_guard"))
            replies = [entry['rule'] for entry in chain['nftables'] if 'rule' in entry]
            assert len(replies) == (0 if mode == "proxy" else 1), (mode, replies)
            previous = mode
    assert checked_legacy
    machine.succeed("nft add rule inet lf_filter lf_host_egress_guard counter drop")
    machine.fail("nft -j list ruleset > /tmp/live.json; ${config.my.proxy.rulesCheck} compare ${config.my.proxy.expectedRules}/direct.json /tmp/live.json > /tmp/difference.log 2>&1")
    output = machine.succeed("cat /tmp/difference.log")
    assert "lf_host_egress_guard rule[" in output and "expected=" in output and "actual=" in output, output
  '';
}
