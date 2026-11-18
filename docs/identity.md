# Link identity and MAC policy

English | [简体中文](CN/identity_CN.md) | [Index](README.md)

Link identity is visible to the local network before application proxying begins.
MAC policy, DHCP identifiers and IPv6 address generation therefore need a common lifecycle.
A/B/C routing and browser privacy remain separate settings.

## Defaults and precedence

Wi-Fi scanning randomizes its address.
Connections without an explicit profile value use `my.hardening.wifi.clonedMacAddress`, whose default is `stable-ssid`: the same SSID receives the same MAC on this host.
NetworkManager state, saved connections and machine ID persist across root rollback.
This MAC default alone does not select a per-network DHCPv6 DUID.
NetworkManager documents [profile MAC precedence](https://networkmanager.dev/docs/api/latest/settings-802-11-wireless.html) and the separate [DHCPv6 identity choices](https://networkmanager.dev/docs/api/latest/settings-ipv6.html).

An explicit `802-11-wireless.cloned-mac-address` in a saved profile takes precedence over the global default.
Clearing that property restores the default on the next activation.
`permanent` selects the hardware address.
`preserve` keeps the current address.

The primary wired uplink defaults to its hardware MAC.
`my.machine.wired.mac` identifies the hardware through `PermanentMACAddress`.
Changing the on-link address does not change its interface name or firewall role.
Wired dhcpcd and Wi-Fi NetworkManager retain separate ownership.

## Per-network Wi-Fi identities

Declare targets by the UUID of an existing Wi-Fi client profile.
Obtain UUIDs with `nmcli -f NAME,UUID connection show`.

Replace the examples with the intended profiles:

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

The migration tool sets a common `connection.stable-id` seed for DHCPv4 client-id/IAID, DHCPv6 DUID/IAID and stable IPv6 address generation.
Random mode adds NetworkManager's per-activation random substitution.
It also prefers temporary IPv6 addresses and disables DHCP hostname transmission, clearing saved hostname/FQDN and vendor-class values.
These relationships use NetworkManager's [stable-id substitutions](https://networkmanager.dev/docs/api/latest/settings-connection.html) and [DHCPv4 settings](https://networkmanager.dev/docs/api/latest/settings-ipv4.html).

Separate profile UUIDs can give separate identities to networks sharing an SSID.
Select the intended profile explicitly when SSIDs overlap.
Increment `epoch` and explicitly apply the target to rotate derived identifiers.
Fixed and permanent modes retain their selected MAC while their derived DHCP/IPv6 identifiers change.
Service discovery, authentication credentials and account sessions retain their own identifiers.

## Apply and restore

Declaring a target does not rewrite saved profiles.

Build and install a generation containing the declaration, then inspect the selected profile:

```sh
profile_uuid=11111111-1111-4111-8111-111111111111
network-identity plan "$profile_uuid"
sudo nmcli connection modify uuid "$profile_uuid" connection.autoconnect no
```

If the profile is active, disconnect it with `sudo nmcli connection down uuid "$profile_uuid"`.

Then apply and reconnect deliberately:

```sh
sudo network-identity apply "$profile_uuid"
sudo nmcli connection up uuid "$profile_uuid"
```

Both `apply` and `restore` require an inactive client profile with autoconnect disabled.
Re-enable autoconnect afterward if desired.
The tool does not reconnect, change network mode or alter credentials, DNS and routes.
Each change saves a private identity-only backup before one NetworkManager settings transaction.
The selected [nmcli property names](https://networkmanager.dev/docs/api/latest/nm-settings-nmcli.html) include both the hostname ternary and its compatibility boolean so restoration preserves the original behavior.

To restore the most recent backup, disable autoconnect and disconnect again, then run:

```sh
sudo network-identity restore "$profile_uuid"
sudo nmcli connection up uuid "$profile_uuid"
```

Restore reinstates saved properties and retains NetworkManager lease state.
Lease-based DUID selection continues to use that lease state.
Check leases and authorization after restoring.
Restore refuses to overwrite identity fields edited after migration.
The backup remains usable after removing the Nix target.
Saved profiles persist independently of system generations: restore before rolling back to a generation without the tool.
Keep `/var/lib/NetworkManager/secret_key` and `/etc/machine-id`.
Deleting either changes identity beyond the selected network.

## Wired policy

The primary dhcpcd uplink supports these alternatives:

```nix
my.hardening.wired.clonedMacAddress = "random";
# Or a fixed address, for example "02:11:22:33:44:66".
# The default is "permanent".
```

Random policy assigns an address during initial udev setup and uses dhcpcd's native randomization on interface bring-up/carrier loss.
A literal address is installed by the `.link` file.
Non-permanent policies enable DHCP anonymity and current-MAC SLAAC: client identifiers follow the current MAC, hostnames are suppressed and negotiation starts without rebooting an old lease.
See the pinned [dhcpcd configuration reference](https://raw.githubusercontent.com/NetworkConfiguration/dhcpcd/v10.3.1/src/dhcpcd.conf.5.in) and [systemd.link matching and address policies](https://www.freedesktop.org/software/systemd/man/latest/systemd.link.html).

Wired dhcpcd runs as its dedicated user in native single-interface mode.
Its DHCPv6 listener binds the wired link-local address, allowing NetworkManager's Wi-Fi DHCPv6 client to operate concurrently.
The upstream generated configuration and service hardening are retained.

Prepare wired policy changes with a built boot generation and a planned reboot.
Ensure the intended MAC is authorized by any portal, allowlist or DHCP reservation before reconnecting.
Wired policy applies to the primary uplink.
It has no NetworkManager per-network profile selector.

## IPv4 ARP protection

By default, the host answers ARP for addresses on the receiving interface and prefers a source address appropriate to the target link. `my.hardening.arp.sameSubnetReplies` further limits replies to senders on a shared IPv4 subnet, using the kernel's `arp_ignore=2` setting on the named uplinks or guest interfaces. For a Wi-Fi interface, this setting applies across profiles. Enable it on links where the router and clients use a common subnet. [Linux ARP parameters](https://docs.kernel.org/networking/ip-sysctl.html#ip-variables)

Verify the subnet, gateway IP and gateway MAC through the router console or managed network inventory before enrolling a connection or replacing a router. DHCP and observed ARP traffic can help identify differences. Use the verified virtual MAC if the routers share one for failover.

```nix
my.hardening.arp = {
  wired = {
    subnet = "192.0.2.0/24";
    gateway = "192.0.2.1";
    mac = "02:00:00:00:02:01";
    filter = "observe";
  };
  wifi."11111111-1111-4111-8111-111111111111" = {
    subnet = "198.51.100.0/24";
    gateway = "198.51.100.1";
    mac = "02:00:00:00:51:01";
    filter = "observe";
  };
  sameSubnetReplies = [ "ens1" ];
  managedInterfaces.virbr0 = "observe";
};
```

`wired` applies to the dedicated dhcpcd-managed interface. `wifi` keys are NetworkManager connection UUIDs, available from `nmcli -f UUID,NAME connection show`. Both uplinks can have their own bindings at the same time. The reconciler checks the link's address and the IPv4 main routing table before installing a permanent neighbour. It removes owned entries on carrier loss or when the connection changes. The default configuration leaves bindings empty, and unlisted Wi-Fi profiles use dynamic ARP.

`network-arp status` reports `pinned`, `dynamic` and binding failures. An active binding failure prevents entering B or renewing its lease. After both the link and the normal proxy health checks pass, B can recover. IPv6 continues to use the separate NDP policy.

`filter = "observe"` pins the gateway and counts invalid frames and conflicting claims at the host's ARP input hook. Use `sudo nft list table arp lf_arp` to inspect the counters, then select `"enforce"` for rejection on a compatible connection. `managedInterfaces` applies the frame checks to explicitly declared guest interfaces. Valid address conflict probes and announcements remain possible. [IPv4 address conflict detection](https://www.rfc-editor.org/rfc/rfc5227.html)

Use `clash-off` for explicit A access. The configured bindings still apply in A. If a binding itself prevents recovery, activate a configuration with the corrected values, set `wired = null` for the wired link, or remove the affected Wi-Fi UUID. The connection can then obtain its gateway dynamically. Restore `"observe"` to retain the pin while backing out frame rejection.

The active link MAC remains visible within its broadcast domain. For network-side isolation, verify same-AP, across-AP and wired-bridge reachability with the intended VLAN or AP isolation settings. When using DHCP Snooping and Dynamic ARP Inspection, verify the VLANs, trusted uplinks, static bindings and whether access ports reject a station reusing both an authorized IP and MAC. For workloads that require a separate neighbour view, use a network namespace such as the [identity browser's network sandbox](browser.md#network-policy-and-identities). [OpenWrt wireless isolation](https://openwrt.org/docs/guide-user/network/wifi/basic?s%5B%5D=2&s%5B%5D=server), [Cisco Dynamic ARP Inspection](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9400/software/release/16-6/configuration_guide/sec/b_166_sec_9400_cg/configuring_dynamic_arp_inspection.html)

## Persisted state and validation

Identity backups persist in `/var/lib/network-identity` with root-only directory/file permissions.

NetworkManager state and saved profiles already persist. dhcpcd state persistence is separately opt-in:

```nix
my.hardening.wired.persistDhcpIdentity = true;
```

Before first enabling this option, build the candidate, stop dhcpcd and copy its current `/var/lib/dhcpcd` directory, including ownership and permissions, to `/persist/var/lib/dhcpcd`.
Prepare this before booting the new generation: an empty first bind would hide the existing DUID and leases.
If the destination already exists, inspect and reconcile it without overwriting newer state.
Anonymous mode ignores the retained DUID.
Leaving the option false retains the existing persistence layout.

After reconnecting, check the actual MAC, IPv4/IPv6 acquisition and portal authorization, then [network readiness](operations.md#state-and-diagnosis).
C still blocks application egress.
Intentional portal recovery uses the existing [offline recovery procedure](operations.md#offline-recovery).

For dependency updates affecting NetworkManager, systemd or dhcpcd, use the `link-identity` component in the [maintenance workflow](maintenance.md#source-compatibility-checks).
