{ pkgs }:

let
  uuids = [
    "11111111-1111-4111-8111-111111111111"
    "22222222-2222-4222-8222-222222222222"
    "33333333-3333-4333-8333-333333333333"
    "44444444-4444-4444-8444-444444444444"
    "55555555-5555-4555-8555-555555555555"
  ];
  
  policies = {
    "${builtins.elemAt uuids 0}" = { mode = "stable"; };
    "${builtins.elemAt uuids 1}" = { mode = "stable"; };
    "${builtins.elemAt uuids 2}" = { mode = "random"; };
    "${builtins.elemAt uuids 3}" = { mode = "fixed"; address = "02:11:22:33:44:55"; };
    "${builtins.elemAt uuids 4}" = { mode = "permanent"; };
  };
  
in
pkgs.testers.runNixOSTest {
  name = "network-identity-lifecycle";
  node.pkgs = pkgs.lib.mkForce (pkgs.extend (_final: prev: { unstable = prev; }));

  nodes.client = { config, lib, pkgs, ... }: {
    imports = [
      ../../system/programs/clash/identity.nix
      ../../system/config/machine.nix
      ../../system/config/hardening-options.nix
      ../../system/config/proxy-options.nix
      ../../system/config/proxy-profile.nix
      ../../system/config/network.nix
      ../../system/programs/systemd/proxy-mode.nix
      ../../system/programs/systemd/network-bootstrap.nix
      ../../system/programs/systemd/netsec-alert.nix
    ];

    users.users.lfour = { isNormalUser = true; uid = 1000; };
    users.groups.gost = { };
    users.users.gost = { isSystemUser = true; group = "gost"; uid = config.my.machine.uids.gost; };
    my.machine.wired = { mac = "52:54:00:12:09:01"; driver = "virtio_net"; };
    my.hardening.wired.clonedMacAddress = "random";
    my.hardening.wifi.identities = policies;
    virtualisation.vlans = [ 9 ];
    virtualisation.memorySize = 1024;
    networking.useDHCP = false;

    # qemu-vm disables wireless by default; exercise NM's real DBus supplicant.
    networking.wireless.enable = lib.mkOverride 0 true;
    networking.interfaces = lib.mkForce { ens1.useDHCP = true; };
    networking.networkmanager.settings.main.no-auto-default = "*";
    networking.networkmanager.unmanaged = [ "interface-name:eth*" ];
    services.avahi.enable = lib.mkForce false;
    services.tailscale.enable = lib.mkForce false;

    systemd.services = (lib.genAttrs [ "proxy-mode" "dnsmasq" "unbound" "chronyd" ]
      (_: { enable = lib.mkForce false; })) // {
        dhcpcd.wantedBy = lib.mkForce [ ];
      };

    environment.etc."identity-capture.py".source = ./identity_fixture.py;
    environment.etc."identity-settings.json".source = config.my.hardening.linkIdentitySettings;
    environment.etc."identity-tool.py".source = ../../system/programs/systemd/netsec/link_identity.py;
    environment.etc."identity-hostapd.conf".text = ''
      interface=ap0
      driver=nl80211
      ssid=identity-fixture
      hw_mode=g
      channel=6
      auth_algs=1
      wpa=0
    '';

    environment.etc."identity-ap-dhcp.conf".text = ''
      interface=ap0
      bind-interfaces
      port=0
      enable-ra
      dhcp-range=10.44.0.10,10.44.0.40,255.255.255.0,1h
      dhcp-range=fd42:44::10,fd42:44::40,64,1h
      ra-param=ap0,5,15
      dhcp-leasefile=/tmp/identity-ap.leases
    '';

    environment.systemPackages = [ pkgs.python3 pkgs.jq pkgs.iw pkgs.hostapd pkgs.dnsmasq pkgs.ethtool ];
  };

  nodes.router = { pkgs, lib, ... }: {
    virtualisation.vlans = [ 9 ];
    virtualisation.memorySize = 512;
    networking.useDHCP = false;

    networking.interfaces.eth1 = {
      ipv4.addresses = lib.mkForce [{ address = "192.168.9.10"; prefixLength = 24; }];
      ipv6.addresses = [{ address = "fd42:9::1"; prefixLength = 64; }];
    };

    networking.firewall.allowedUDPPorts = [ 67 547 ];
    
    services.dnsmasq = {
      enable = true;
      settings = {
        interface = "eth1";
        bind-interfaces = true;
        port = 0;
        enable-ra = true;
        ra-param = "eth1,5,15";
        dhcp-range = [ "192.168.9.100,192.168.9.150,255.255.255.0,1h" "fd42:9::100,fd42:9::150,64,1h" ];
      };
    };

    environment.etc."identity-capture.py".source = ./identity_fixture.py;
    systemd.services.identity-capture = {
      wantedBy = [ "multi-user.target" ];
      after = [ "network-addresses-eth1.service" ];
      serviceConfig = {
        ExecStart = "${pkgs.python3}/bin/python3 /etc/identity-capture.py eth1 /tmp/wired-identities.jsonl";
        Restart = "on-failure";
      };
    };
  };

  testScript = ''
    import json
    import time

    uuids = ${builtins.toJSON uuids}

    def mac(machine, interface):
        return machine.succeed(f"cat /sys/class/net/{interface}/address").strip()

    def packets(machine, path, address):
        return [r for r in (json.loads(line) for line in machine.succeed(f"cat {path}").splitlines())
                if r['mac'] == address and r['clientid']
                and r['message'] in ({1, 3} if r['family'] == 4 else {1, 3, 4, 5, 6})]

    def observations(machine, path, address, private=True):
        result = []
        for _ in range(40):
            result = packets(machine, path, address)
            if {r['family'] for r in result} == {4, 6}:
                break
            time.sleep(1)
        else:
            if path.startswith('/tmp/wifi'):
                print(machine.succeed("ip -6 addr show wlo1; nmcli -f IP6,DHCP6 device show wlo1; ip netns exec identity-ap ip -6 addr show ap0; journalctl -b -u NetworkManager --no-pager -n 90"))
            raise AssertionError((address, result))
        if private:
            assert not any(r['hostname'] for r in result), result
        values: dict[int | str, str] = {family: next(r['clientid'] for r in reversed(result) if r['family'] == family)
                  for family in (4, 6)}
        values['ipv6-source'] = next(r['source_ip'] for r in reversed(result) if r['family'] == 6)
        values['iaid'] = next(r['iaid'] for r in reversed(result) if r['family'] == 6)
        assert values['iaid'], values
        return values

    start_all()
    client.wait_for_unit("nftables.service")
    client.wait_for_unit("NetworkManager.service")
    router.wait_for_unit("dnsmasq.service")
    router.wait_for_file("/tmp/wired-identities.ready")
    client.succeed("test -d /sys/class/net/ens1; ethtool -P ens1 | grep -qi 52:54:00:12:09:01")
    client.succeed("systemctl start dhcpcd")
    client.wait_until_succeeds("ip -4 addr show ens1 | grep -q 192.168.9.", timeout=40)
    dhcp_pid = client.succeed("systemctl show dhcpcd -p MainPID --value").strip()
    assert client.succeed(f"ps -o uid= -p {dhcp_pid}").strip() == "986"
    assert client.succeed("cat /run/dhcpcd/ens1.pid").strip() == dhcp_pid
    first = mac(client, "ens1")
    assert first != "52:54:00:12:09:01" and int(first[:2], 16) & 3 == 2, first
    first_ids = observations(router, "/tmp/wired-identities.jsonl", first)
    assert first_ids[4] == "01" + first.replace(":", ""), first_ids
    assert first_ids[6].endswith(first.replace(":", "")), first_ids
    client.succeed("systemctl stop dhcpcd; ip link set ens1 down; systemctl start dhcpcd")
    client.wait_until_succeeds("ip -4 addr show ens1 | grep -q 192.168.9.", timeout=40)
    second = mac(client, "ens1")
    assert first != second, (first, second)
    second_ids = observations(router, "/tmp/wired-identities.jsonl", second)
    assert all(first_ids[key] != second_ids[key] for key in (4, 6, 'ipv6-source')), (first_ids, second_ids)
    # RFC 7844 anonymity uses a common zero IAID, not a retained host identifier.
    assert first_ids['iaid'] == second_ids['iaid'] == '00000000', (first_ids, second_ids)
    assert second_ids[4] == "01" + second.replace(":", "") and second_ids[6].endswith(second.replace(":", "")), second_ids
    client.succeed("nmcli -g GENERAL.STATE device show ens1 | grep -qi unmanaged")

    # Two simulated radios in the same isolated kernel; move only the AP into
    # a separate namespace so DHCP and RA are real remote input to the client.
    client.succeed("modprobe mac80211_hwsim radios=2; udevadm settle")
    client.wait_for_unit("wpa_supplicant.service")
    client.wait_until_succeeds("test -d /sys/class/net/wlan0 && test -d /sys/class/net/wlan1", timeout=20)
    client.succeed("nmcli device set wlan1 managed no; ip link set wlan0 down; ip link set wlan0 name wlo1; ip netns add identity-ap; iw phy phy1 set netns name identity-ap")
    client.succeed("ip -n identity-ap link set wlan1 name ap0; ip -n identity-ap addr add 10.44.0.1/24 dev ap0; ip -n identity-ap -6 addr add fd42:44::1/64 dev ap0 nodad; ip -n identity-ap link set lo up; ip -n identity-ap link set ap0 up")
    client.succeed("systemd-run --unit=identity-ap ip netns exec identity-ap ${pkgs.hostapd}/bin/hostapd /etc/identity-hostapd.conf; systemd-run --unit=identity-dhcp ip netns exec identity-ap ${pkgs.dnsmasq}/bin/dnsmasq --keep-in-foreground --conf-file=/etc/identity-ap-dhcp.conf; systemd-run --unit=identity-capture ip netns exec identity-ap ${pkgs.python3}/bin/python3 /etc/identity-capture.py ap0 /tmp/wifi-identities.jsonl")
    client.wait_for_file("/tmp/wifi-identities.ready")
    client.wait_for_unit("identity-ap.service")
    client.wait_for_unit("identity-dhcp.service")
    client.wait_until_succeeds("nmcli device wifi list ifname wlo1 | grep -q identity-fixture", timeout=30)
    for index, uuid in enumerate(uuids):
        client.succeed(f"nmcli connection add type wifi ifname wlo1 con-name identity-{index} ssid identity-fixture connection.uuid {uuid} connection.autoconnect no ipv4.method auto ipv6.method auto")
    # First activate an existing default identity, creating NM's actual host
    # key and lease state before migrating it to a different profile identity.
    client.succeed("nmcli general logging level INFO domains IP6,DHCP6")
    client.succeed(f"nmcli --wait 30 connection up uuid {uuids[0]}")
    legacy_mac = mac(client, "wlo1")
    legacy_ids = observations(client, "/tmp/wifi-identities.jsonl", legacy_mac, private=False)
    client.succeed(f"nmcli connection down uuid {uuids[0]}")
    host_identity = client.succeed("sha256sum /etc/machine-id /var/lib/NetworkManager/secret_key")
    for uuid in uuids:
        before = client.succeed(f"nmcli -g connection.stable-id connection show uuid {uuid}")
        client.succeed(f"network-identity plan {uuid}")
        assert client.succeed(f"nmcli -g connection.stable-id connection show uuid {uuid}") == before
        client.fail(f"setpriv --reuid=1000 --clear-groups network-identity apply {uuid}")
        client.succeed(f"network-identity apply {uuid}")

    def connect(uuid, private=True):
        # Require this activation's exchange even when its MAC is unchanged.
        client.succeed("truncate -s 0 /tmp/wifi-identities.jsonl")
        client.succeed(f"nmcli --wait 30 connection up uuid {uuid}")
        address = mac(client, "wlo1")
        ids = observations(client, "/tmp/wifi-identities.jsonl", address, private=private)
        client.fail(f"network-identity apply {uuid}")
        client.succeed(f"nmcli connection down uuid {uuid}")
        return address, ids

    stable = connect(uuids[0])
    assert stable[0] != legacy_mac and all(stable[1][key] != legacy_ids[key]
        for key in (4, 6, 'ipv6-source')), (legacy_mac, legacy_ids, stable)
    assert connect(uuids[0]) == stable
    client.succeed("python3 -c 'import json; from pathlib import Path; p=json.loads(Path(\"/etc/identity-settings.json\").read_text()); p[\"profiles\"][\"11111111-1111-4111-8111-111111111111\"][\"epoch\"]=\"2\"; Path(\"/tmp/identity-epoch2.json\").write_text(json.dumps(p))'")
    client.succeed(f"python3 /etc/identity-tool.py /tmp/identity-epoch2.json apply {uuids[0]}")
    rotated = connect(uuids[0])
    assert rotated[0] != stable[0] and all(rotated[1][key] != stable[1][key] for key in (4, 6, 'ipv6-source', 'iaid')), (stable, rotated)
    client.succeed(f"network-identity apply {uuids[0]}")
    assert connect(uuids[0]) == stable
    separate = connect(uuids[1])
    assert separate[0] != stable[0] and all(separate[1][key] != stable[1][key] for key in (4, 6, 'ipv6-source', 'iaid')), (stable, separate)
    random_first, random_second = connect(uuids[2]), connect(uuids[2])
    assert random_first[0] != random_second[0] and all(random_first[1][key] != random_second[1][key] for key in (4, 6, 'ipv6-source', 'iaid'))
    fixed = connect(uuids[3])
    assert fixed[0] == "02:11:22:33:44:55" and connect(uuids[3]) == fixed
    permanent = connect(uuids[4])
    assert permanent[0] == client.succeed("cat /sys/class/ieee80211/phy0/macaddress").strip(), permanent
    assert connect(uuids[4]) == permanent
    client.succeed(f"network-identity restore {uuids[3]}")
    client.succeed(f"test -z \"$(nmcli -g 802-11-wireless.cloned-mac-address connection show uuid {uuids[3]})\"")
    # A per-profile override takes precedence over the global stable-ssid default.
    client.succeed(f"nmcli connection modify uuid {uuids[3]} 802-11-wireless.cloned-mac-address 02:aa:bb:cc:dd:ee")
    assert connect(uuids[3], private=False)[0] == "02:aa:bb:cc:dd:ee"
    client.succeed(f"nmcli connection modify uuid {uuids[3]} 802-11-wireless.cloned-mac-address \"\"")
    fallback = connect(uuids[3], private=False)
    # The unchanged global default promises an SSID-derived MAC. Its default
    # lease-based DUID is separate, unlike the explicit coordinated policies.
    assert fallback[0] == legacy_mac and connect(uuids[3], private=False)[0] == legacy_mac, fallback
    assert client.succeed("sha256sum /etc/machine-id /var/lib/NetworkManager/secret_key") == host_identity
    # Persisted settings survive daemon restart; no global identity deletion.
    client.succeed("systemctl restart NetworkManager")
    client.succeed(f"network-identity apply {uuids[0]} | grep -q unchanged")
  '';
}
