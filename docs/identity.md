# Link identity and MAC policy

English | [简体中文](CN/identity_CN.md) | [Index](README.md)

Link identity is visible to the local network before application proxying begins. MAC policy, DHCP identifiers and IPv6 address generation therefore need a common lifecycle. A/B/C routing and browser privacy remain separate settings.

## Defaults and precedence

Wi-Fi scanning randomizes its address. Connections without an explicit profile value use `my.hardening.wifi.clonedMacAddress`, whose default is `stable-ssid`: the same SSID receives the same MAC on this host. NetworkManager state, saved connections and machine ID persist across root rollback. This MAC default alone does not select a per-network DHCPv6 DUID. NetworkManager documents [profile MAC precedence](https://networkmanager.dev/docs/api/latest/settings-802-11-wireless.html) and the separate [DHCPv6 identity choices](https://networkmanager.dev/docs/api/latest/settings-ipv6.html).

An explicit `802-11-wireless.cloned-mac-address` in a saved profile takes precedence over the global default. Clearing that property restores the default on the next activation. `permanent` selects the hardware address; `preserve` keeps the current address.

The primary wired uplink defaults to its hardware MAC. `my.machine.wired.mac` identifies the hardware through `PermanentMACAddress`; changing the on-link address does not change its interface name or firewall role. Wired dhcpcd and Wi-Fi NetworkManager retain separate ownership.

## Per-network Wi-Fi identities

Declare targets by the UUID of an existing Wi-Fi client profile. Obtain UUIDs with `nmcli -f NAME,UUID connection show`; replace the examples with the intended profiles:

```nix
my.hardening.wifi.identities = {
  "11111111-1111-4111-8111-111111111111" = {
    mode = "stable";
    epoch = "1";
  };
  "22222222-2222-4222-8222-222222222222" = {
    mode = "random";
  };
  "33333333-3333-4333-8333-333333333333" = {
    mode = "fixed";
    address = "02:11:22:33:44:55";
  };
};
```

| Mode | MAC on activation |
|---|---|
| `stable` | Derived from the profile UUID, device name, host key and epoch |
| `random` | A new address for each activation |
| `permanent` | The hardware address |
| `fixed` | The declared nonzero unicast `address` |

The migration tool sets a common `connection.stable-id` seed for DHCPv4 client-id/IAID, DHCPv6 DUID/IAID and stable IPv6 address generation. Random mode adds NetworkManager's per-activation random substitution. It also prefers temporary IPv6 addresses and disables DHCP hostname transmission, clearing saved hostname/FQDN and vendor-class values. These relationships use NetworkManager's [stable-id substitutions](https://networkmanager.dev/docs/api/latest/settings-connection.html) and [DHCPv4 settings](https://networkmanager.dev/docs/api/latest/settings-ipv4.html).

Separate profile UUIDs can give separate identities to networks sharing an SSID; select the intended profile explicitly when SSIDs overlap. Increment `epoch` and explicitly apply the target to rotate derived identifiers. Fixed and permanent modes retain their selected MAC while their derived DHCP/IPv6 identifiers change. Service discovery, authentication credentials and account activity can still associate connections.

## Apply and restore

Declaring a target does not rewrite saved profiles. Build and install a generation containing the declaration, then inspect the selected profile:

```sh
profile_uuid=11111111-1111-4111-8111-111111111111
network-identity plan "$profile_uuid"
sudo nmcli connection modify uuid "$profile_uuid" connection.autoconnect no
```

If the profile is active, disconnect it with `sudo nmcli connection down uuid "$profile_uuid"`. Then apply and reconnect deliberately:

```sh
sudo network-identity apply "$profile_uuid"
sudo nmcli connection up uuid "$profile_uuid"
```

Both `apply` and `restore` require an inactive client profile with autoconnect disabled. Re-enable autoconnect afterward if desired. The tool does not reconnect, change network mode or alter credentials, DNS and routes. Each change saves a private identity-only backup before one NetworkManager settings transaction. The selected [nmcli property names](https://networkmanager.dev/docs/api/latest/nm-settings-nmcli.html) include both the hostname ternary and its compatibility boolean so restoration preserves the original behavior.

To restore the most recent backup, disable autoconnect and disconnect again, then run:

```sh
sudo network-identity restore "$profile_uuid"
sudo nmcli connection up uuid "$profile_uuid"
```

Restore reinstates saved properties and retains NetworkManager lease state; the default lease-based DUID can therefore follow its most recent lease. Check leases and authorization after restoring. Restore refuses to overwrite identity fields edited after migration. The backup remains usable after removing the Nix target. Saved profiles persist independently of system generations: restore before rolling back to a generation without the tool. Keep `/var/lib/NetworkManager/secret_key` and `/etc/machine-id`; deleting either changes identity beyond the selected network.

## Wired policy

The primary dhcpcd uplink supports these alternatives:

```nix
my.hardening.wired.clonedMacAddress = "random";
# Or a fixed address, for example "02:11:22:33:44:66".
# The default is "permanent".
```

Random policy assigns an address during initial udev setup and uses dhcpcd's native randomization on interface bring-up/carrier loss. A literal address is installed by the `.link` file. Non-permanent policies enable DHCP anonymity and current-MAC SLAAC: client identifiers follow the current MAC, hostnames are suppressed and negotiation starts without rebooting an old lease. See the pinned [dhcpcd configuration reference](https://raw.githubusercontent.com/NetworkConfiguration/dhcpcd/v10.3.1/src/dhcpcd.conf.5.in) and [systemd.link matching and address policies](https://www.freedesktop.org/software/systemd/man/latest/systemd.link.html).

Wired dhcpcd runs as its dedicated user in native single-interface mode. Its DHCPv6 listener binds the wired link-local address, allowing NetworkManager's Wi-Fi DHCPv6 client to operate concurrently. The upstream generated configuration and service hardening are retained.

Prepare wired policy changes with a built boot generation and a planned reboot. Ensure the intended MAC is authorized by any portal, allowlist or DHCP reservation before reconnecting. Wired policy applies to the primary uplink; it has no NetworkManager per-network profile selector.

## Persisted state and validation

Identity backups persist in `/var/lib/network-identity` with root-only directory/file permissions. NetworkManager state and saved profiles already persist. dhcpcd state persistence is separately opt-in:

```nix
my.hardening.wired.persistDhcpIdentity = true;
```

Before first enabling this option, build the candidate, stop dhcpcd and copy its current `/var/lib/dhcpcd` directory, including ownership and permissions, to `/persist/var/lib/dhcpcd`. Prepare this before booting the new generation: an empty first bind would hide the existing DUID and leases. If the destination already exists, inspect and reconcile it without overwriting newer state. Anonymous mode ignores the retained DUID. Leaving the option false retains the existing persistence layout.

After reconnecting, check the actual MAC, IPv4/IPv6 acquisition and portal authorization, then [network readiness](operations.md#state-and-diagnosis). A changed address does not erase records already held by the network. C still blocks application egress; intentional portal recovery uses the existing [offline recovery procedure](operations.md#offline-recovery).

`network-identity-config` checks defaults, wired variants, persistence and the actual selected nmcli parser. `network-identity-vm` uses real dhcpcd, NetworkManager and simulated Wi-Fi radios to observe fresh dual-stack DHCP exchanges, identity rotation, profile precedence and restore. NetworkManager, systemd and dhcpcd upgrades select the `link-identity` component in the [maintenance workflow](maintenance.md#component-review-gate).
