# Link identity and MAC policy

English | [简体中文](CN/identity_CN.md) | [Index](README.md)

The local network sees your link identity before any application proxy kicks in, so MAC policy, DHCP identifiers, and IPv6 address generation need to share a coherent lifecycle. A/B/C routing and browser privacy remain separate layers.

## Defaults and precedence

Wi-Fi scanning uses a randomized address. When a connection profile doesn't specify its own MAC, it falls back to `my.hardening.wifi.clonedMacAddress`, which defaults to `stable-ssid`—meaning the same SSID gets the same MAC on this host. NetworkManager state, saved profiles, and machine ID all persist across root rollbacks, though this MAC default by itself does not set a per-network DHCPv6 DUID. NetworkManager documents [profile MAC precedence](https://networkmanager.dev/docs/api/latest/settings-802-11-wireless.html) and the separate [DHCPv6 identity choices](https://networkmanager.dev/docs/api/latest/settings-ipv6.html) independently.

An explicit `802-11-wireless.cloned-mac-address` on a saved profile overrides the global default; clearing that property brings the default back on the next activation. `permanent` uses the hardware address, while `preserve` keeps whatever address is currently assigned.

The primary wired uplink defaults to its hardware MAC. `my.machine.wired.mac` matches the hardware via `PermanentMACAddress`, so changing the on-link address won't rename the interface or alter its firewall role. Wired `dhcpcd` and Wi-Fi NetworkManager stay cleanly separated.

## Per-network Wi-Fi identities

You can declare per-network targets using the UUID of an existing Wi-Fi client profile (run `nmcli -f NAME,UUID connection show` to list them) and replace the examples below:

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
| `stable` | Derived from the profile UUID, device name, host key, and epoch |
| `random` | A fresh address on every activation |
| `permanent` | The hardware address |
| `fixed` | The declared nonzero unicast `address` |

The migration tool sets a shared `connection.stable-id` seed for DHCPv4 client-id/IAID, DHCPv6 DUID/IAID, and stable IPv6 address generation, with `random` mode adding NetworkManager's per-activation random substitution. It also prefers temporary IPv6 addresses, disables sending a DHCP hostname, and clears any existing hostname/FQDN or vendor-class values—all built on NetworkManager's [stable-id substitutions](https://networkmanager.dev/docs/api/latest/settings-connection.html) and [DHCPv4 settings](https://networkmanager.dev/docs/api/latest/settings-ipv4.html).

Different profile UUIDs can give distinct identities to networks that share an SSID—just pick the intended profile explicitly when SSIDs overlap. Incrementing `epoch` and re-applying rotates the derived identifiers (`fixed` and `permanent` keep their chosen MAC while their derived DHCP/IPv6 identifiers rotate). Service discovery, auth credentials, and account sessions still keep their own identifiers.

## Apply and restore

Declaring a target in Nix does not automatically rewrite saved profiles. After building and activating a generation with your declaration, inspect the target profile first:

```sh
profile_uuid=11111111-1111-4111-8111-111111111111
network-identity plan "$profile_uuid"
sudo nmcli connection modify uuid "$profile_uuid" connection.autoconnect no
```

If the profile is currently active, bring it down with `sudo nmcli connection down uuid "$profile_uuid"`, then apply and reconnect explicitly:

```sh
sudo network-identity apply "$profile_uuid"
sudo nmcli connection up uuid "$profile_uuid"
```

Both `apply` and `restore` require an inactive profile with autoconnect disabled (you can re-enable autoconnect afterward). The tool won't reconnect on its own, switch network modes, or touch credentials, DNS, and routes. Before each change, it saves a private identity-only backup and applies the update in a single NetworkManager settings transaction. The selected [nmcli property names](https://networkmanager.dev/docs/api/latest/nm-settings-nmcli.html) cover both the hostname ternary and its compatibility boolean so restoration brings back the original behavior.

To roll back to the latest backup, disable autoconnect and disconnect again, then run:

```sh
sudo network-identity restore "$profile_uuid"
sudo nmcli connection up uuid "$profile_uuid"
```

Restoring writes back the saved properties while leaving NetworkManager's lease state intact (so lease-based DUID selection continues using it—check your leases and authorization afterward). To avoid clobbering manual tweaks, `restore` refuses to overwrite identity fields that were edited after migration. Backups remain usable even after removing the Nix target, and because profiles persist across system generations, make sure to restore before rolling back to an older generation that doesn't include the tool. Keep `/var/lib/NetworkManager/secret_key` and `/etc/machine-id` intact too; deleting either changes derived identities beyond the selected network.

## Wired policy

For the primary `dhcpcd` uplink, you can override the default `"permanent"` policy:

```nix
my.hardening.wired.clonedMacAddress = "random";
# Or a fixed address, for example "02:11:22:33:44:66".
# The default is "permanent".
```

With `"random"`, udev sets an initial address and `dhcpcd` randomizes it natively on interface bring-up or carrier loss; a literal address is applied via `.link` file. Any non-permanent policy also enables DHCP anonymity and current-MAC SLAAC: client identifiers follow the active MAC, hostnames are suppressed, and negotiation starts fresh without attempting to reboot an old lease. See the pinned [dhcpcd configuration reference](https://raw.githubusercontent.com/NetworkConfiguration/dhcpcd/v10.3.1/src/dhcpcd.conf.5.in) and [systemd.link matching and address policies](https://www.freedesktop.org/software/systemd/man/latest/systemd.link.html).

Wired `dhcpcd` runs as its own dedicated user in single-interface mode, binding its DHCPv6 listener to the wired link-local address so NetworkManager's Wi-Fi DHCPv6 client can run alongside it while keeping upstream generated config and service hardening intact. When changing wired policy, build the new boot generation first and plan a reboot, making sure any portal, MAC allowlist, or DHCP reservation already permits the target MAC. Wired policy applies globally to the primary uplink—there is no per-network profile selector like NetworkManager has.

## IPv4 ARP protection

By default, the host only answers ARP for addresses on the receiving interface and picks a source address suited to the target link. `my.hardening.arp.sameSubnetReplies` tightens this further with `arp_ignore=2` on named uplinks or guest interfaces so replies only go to senders on the same IPv4 subnet (on a Wi-Fi interface, this applies across all profiles—enable it where the router and clients share a subnet; see [Linux ARP parameters](https://docs.kernel.org/networking/ip-sysctl.html#ip-variables)).

Before enrolling a connection or swapping routers, double-check the subnet, gateway IP, and gateway MAC from the router console or managed inventory (DHCP and observed ARP traffic can help spot mismatches; if failover routers share a virtual MAC, pin the verified virtual MAC).

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

`wired` targets the `dhcpcd`-managed interface, and `wifi` is keyed by NetworkManager connection UUID (`nmcli -f UUID,NAME connection show`). Both uplinks can have bindings at once. The reconciler checks the link address and IPv4 main routing table before installing a permanent neighbour entry, and cleans up its entries on carrier loss or connection changes. By default no bindings are configured, and unlisted Wi-Fi profiles use dynamic ARP.

`network-arp status` shows `pinned`, `dynamic`, and any binding failures. An active binding failure blocks entering **B** or renewing its lease until both the link binding and normal proxy health checks pass; IPv6 continues using its separate NDP policy.

`filter = "observe"` pins the gateway and counts invalid frames and conflicting claims at the host ARP input hook—inspect the counters with `sudo nft list table arp lf_arp`, and switch to `"enforce"` once you've verified compatibility to drop those frames (`managedInterfaces` applies the same frame checks to declared guest interfaces, while valid address-conflict probes and announcements still pass; see [IPv4 address conflict detection](https://www.rfc-editor.org/rfc/rfc5227.html)).

Use `clash-off` for explicit **A** access—configured bindings stay active in **A** too. If a binding itself is blocking recovery, activate a corrected configuration, set `wired = null` for the wired link, or drop the affected Wi-Fi UUID so the link falls back to dynamic resolution; switching back to `"observe"` keeps the pin while backing out frame rejection.

Keep in mind that the active link MAC is still visible across its broadcast domain. For network-side isolation, check same-AP, cross-AP, and wired-bridge reachability under your VLAN or AP isolation settings; with DHCP Snooping and Dynamic ARP Inspection, verify VLANs, trusted uplinks, static bindings, and whether access ports reject a station spoofing both an authorized IP and MAC. For workloads that need a separate neighbour view, use an isolated network namespace such as the [identity browser's network sandbox](browser.md#network-policy-and-identities) (see [OpenWrt wireless isolation](https://openwrt.org/docs/guide-user/network/wifi/basic?s%5B%5D=2&s%5B%5D=server) and [Cisco Dynamic ARP Inspection](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9400/software/release/16-6/configuration_guide/sec/b_166_sec_9400_cg/configuring_dynamic_arp_inspection.html)).

## Persisted state and validation

Identity backups live in `/var/lib/network-identity` with root-only permissions. NetworkManager state and saved profiles already persist, whereas `dhcpcd` state persistence is opt-in:

```nix
my.hardening.wired.persistDhcpIdentity = true;
```

Before enabling this for the first time, build the candidate, stop `dhcpcd`, and copy `/var/lib/dhcpcd` (preserving ownership and permissions) to `/persist/var/lib/dhcpcd` before booting—binding an empty directory on first boot would mask existing DUIDs and leases. If `/persist/var/lib/dhcpcd` already exists, inspect and reconcile it without overwriting newer state. Anonymous mode ignores the retained DUID, and leaving this option `false` keeps the existing persistence layout.

After reconnecting, verify the actual MAC, IPv4/IPv6 acquisition, and portal authorization, then check [network readiness](operations.md#state-and-diagnosis). **C** still blocks application egress; for intentional portal recovery, follow the [offline recovery procedure](operations.md#offline-recovery). When updating NetworkManager, systemd, or `dhcpcd`, pick the `link-identity` component in the [maintenance workflow](maintenance.md#source-compatibility-checks).
