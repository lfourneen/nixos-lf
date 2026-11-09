# Network architecture

English | [简体中文](CN/network_CN.md) | [Documentation](README.md)

## Traffic paths

The host uses nftables to select direct access, a managed proxy path or blocking. Three application paths converge on Mihomo when proxy mode is healthy:

```text
Proxy-aware applications    Public TCP without proxy settings    TUN / fake-IP
          |                              |                            |
          v                              v                            v
 gost HTTP :33332              nft REDIRECT -> gost :33333       TUN "Mihomo"
          |                              |                            |
          +----------------> Mihomo mixed :7897 <-------------------+
                                      |
                              subscription routing
                               /             \
                            DIRECT       proxy group / node
                               \             /
                                    uplink
```

The listeners are local. Environment variables (both cases), GSettings and Flatpak overrides point proxy-aware applications at `127.0.0.1:33332`. Gost forwards both inlets to `127.0.0.1:7897` in B, passes traffic directly in A and closes its listeners in C or unknown state. Explicit client connections to the transparent port `33333` are rejected before NAT; only redirected flows should enter it.

Mihomo marks its outbound sockets with `6666` (`0x1a0a`); the exception requires both socket and packet marks plus socket UID root or `clash-core`. Rule routing can choose DIRECT under the default `my.proxy.proxyOnly=false`. The optional [uniform proxy policy](proxy.md#routing-policy) refuses application DIRECT inside the core. Private-network and infrastructure paths outside the core have their own firewall rules.

[DNS](dns.md) runs alongside this path: resolved → dnsmasq → Mihomo or Unbound. [Firefox](browser.md) uses the same Gost HTTP inlet.

## Modes and enforcement

| Mode | Entry | Application egress | Default DNS upstream |
|---|---|---|---|
| A / `direct` | Cold boot with direct boot policy, or explicit `clash-off` | Direct access | Validating Unbound DoT, with bounded plaintext recovery |
| B / `proxy` | Proxy intent, authorized core, required TUN routes and health proof | Proxy, explicit exceptions, or drop | Mihomo fake-IP |
| C / `blocked` | Proxy requested but not ready, crashed or unhealthy | Blocked except recovery and link infrastructure | Unbound; external DoT is cut, cached answers may remain |

`proxy-mode` follows root-written, boot-scoped intent. Closing the GUI or stopping/crashing the core after `clash-on` keeps C. Use `clash-off` to request A. Starting a GUI alone cannot authorize A → B. `my.proxy.bootMode="proxy"` installs C before physical networking and waits for B; the default is `"direct"`.

DNS transport has an independent policy: `my.proxy.dnsEncryption="strict"` disables A's public plaintext recovery and its runtime override, while retaining approved split DNS and MagicDNS. The default `"opportunistic"` preserves portal recovery. Both modes share domain-filter data between dnsmasq and the core, including proxy target checks; see [DNS responsibilities](dns.md#responsibilities).

`unenforced` reports that the expected kernel policy could not be confirmed or repaired. It is an error status, not another protected mode. Applications using the shared readiness check refuse it.

The core's service cgroup, passive TCP/UDP listener identities, managed controller values, TUN device/routes and actual unmarked IPv6 selection for root and the desktop UID feed the health decision. HTTP probes must complete through the currently selected proxy leaf with matching source-port logs; another real HTTP request to a fake-IP proves the transparent TCP path. The default shared deadline is 12 seconds. Definite configuration, route or TUN failures block immediately; unavailable observations use two consecutive rounds, at least two seconds apart. This samples selected TCP paths, not every UDP, IPv6 or application destination.

## Firewall lifecycle

[network.nix](../system/config/network.nix) defines the static policy and the B/C fragments. [proxy-mode.nix](../system/programs/systemd/proxy-mode.nix) installs the static rules plus a safety fragment in one scoped `nft -f` transaction under a shared lock. Only repository-owned tables are replaced; Mihomo and other services retain their own tables.

| Hook / priority | Purpose |
|---|---|
| Output `-201` | Reject direct use of Gost's transparent inlet before NAT |
| Prerouting `-201` | Apply guest public-destination restrictions before redirection |
| Output `-199` | Limit dnsmasq upstream DNS to installed destination leases; strict mode cuts the core's external port 53 except MagicDNS |
| Output `-198` | Reserve internal DNS ports and authenticate their replies |
| Output `-197` | C guard, after conntrack (`-200`) and before auto-redirect (`-150`) |
| Output `-196` | Restrict independent DNS-over-TLS/QUIC on port 853 |
| Output `-195` | B optional private/multicast grants and optional Unbound DoT cut, before NAT/TUN |
| Output `-194` | Optional Chrony original-target CIDR bounds in A/B |
| Input `-1` | C drops decrypted tailnet packets before established-flow accepts |

In B, public host TCP is redirected, fake-IP traffic retains its core path, and other public uplink traffic is dropped after the explicit exceptions. These include marked core sockets, UID-scoped Unbound DoT and Chrony NTS/NTP, narrow DHCP, multicast and default private egress. Tailscale UDP requires UID 0, a physical uplink, and both socket `SO_MARK` and packet mark equal to the upstream Linux bypass mark (`0x80000`). Peer/STUN ports remain dynamic; unmarked root UDP has no blanket exception. This is a routing contract, not service authentication or isolation against root, which can set socket marks or change the firewall. The current LKL kernel supports `socket mark`, but not `socket cgroupv2`.

[tailscale-transport.nix](../system/config/tailscale-transport.nix) treats the mark as an upstream constant, not a configurable local allocation. Normal system builds check the selected Tailscale source for its value and the reviewed magicsock/STUN marking paths. Network changes do not change the constant; upgrades require this check and `proxy-tailscale-vm`, which exercises the real selected CLI with isolated dual-stack STUN peers. Falling back to `SO_BINDTODEVICE` does not silently reopen unmarked root UDP.

The [direct-egress ledger](egress.md) records every actor, target, port and mode. Optional restricted B grants use interface/UID/CIDR/ports and cut revoked established host-initiated flows before TUN/NAT. Unbound is limited to configured DoT targets even in A; its B exception can be disabled. Chrony can pin destination CIDRs in A/B. Defaults preserve LAN and changing NTS addresses.

In C, an early output guard blocks application traffic before the core can redirect it. It preserves loopback IPC, marked core sockets, root recovery through proxy/DNS ports and fake-IP ports 53/80/443, narrow DHCP and hop-limit-255 NDP. Non-root access to the core/Gost application ports is blocked for TCP and UDP. Self-test reply exceptions require the expected loopback source or root/core conntrack DNAT reply to an original fake-IP HTTP destination. Guest DHCP/DNS replies are bound to bridge, gateway source and port rather than daemon UID. Unbound and ordinary tailnet traffic lose external egress, including established remote-desktop flows; B grants provide no C remote-control exception. A separate input guard drops decrypted tailnet packets before established-flow accepts, including packets already in flight.

[network-bootstrap.nix](../system/programs/systemd/network-bootstrap.nix) installs ingress/forwarding protection before NetworkManager or dhcpcd. A failed first full load retains that baseline. Static drift triggers a complete repair into A for direct intent or C for proxy intent, followed by health evaluation. A failed repair reports `unenforced`.

Stopping `nftables.service` leaves the rules installed. Restart or reload reconciles them; avoid `nft flush ruleset`, which also destroys core-owned tables. [Operations](operations.md) describes verification and recovery.

## Uplinks and local services

Wired `ens1` uses dhcpcd and is excluded from NetworkManager by name and MAC. Wi-Fi `wlo1` uses NetworkManager, with a stable PCI-path match. Scans randomize their MAC; connections default to `stable-ssid`. Persisted NetworkManager state and machine ID keep each SSID's identity across root rollback. Profiles can override [the MAC default](operations.md#configuration).

Tailscale accepts neither DNS, subnet routes nor an exit node automatically. Only the authenticated tailnet's MagicDNS suffix receives conditional resolver routing. Host input defaults to Sunshine streaming TCP 47984/47989/48010 and UDP 47998/47999/48000/48002/48010. Sunshine starts with the graphical session; its administration UI stays local and UPnP is disabled. See [remote desktop operation](operations.md#remote-desktop). Avahi advertises on loopback and tailnet, plus an explicitly enabled AP; a Wi-Fi client on a foreign network does not advertise. This configuration does not import the sshd module. A firewall allowance alone does not create an SSH listener.

[network-time.nix](../system/config/network-time.nix) configures Chrony with authenticated NTS servers, UDP 123 and NTS-KE TCP 4460. RTC/drift restoration provides an offline time floor; certificate-time checks stay enabled and no plaintext time fallback is configured. [network-hardening.nix](../system/config/network-hardening.nix) supplies shared sysctl, redirect/source-route and module restrictions. Nix daemon trust is restricted to root.

## Guests and containers

`my.proxy.guestInterfaces` defaults to `virbr0` and `waydroid0`; a dedicated AP interface joins only when hotspot policy is enabled. Wi-Fi client uplinks never become guest ingress. In B/C, public guest destinations are checked before NAT and both forwarding directions are constrained before established-flow accepts.

Guest public forwarding is refused by default in B/C. `transparentGuestInterfaces` explicitly selects an IPv4 TPROXY subset for healthy B; it defaults to empty. Public guest IPv6 remains refused. A shares the uplink directly. A hotspot setting installs the firewall role only; creating the separate AP interface, hostapd and DHCP remains explicit.

Guest DHCP/DNS gateway services are separate from the host loopback resolver. Default host access is limited to that infrastructure. Libvirt desktop-service access requires `guestHostServices` and explicit TCP/UDP lists; removing an allowance also cuts established guest-initiated connections. TUN input admits only tracked established/related flows.

Private guest forwarding defaults to allowed. `my.hardening.guestPrivateEgress=false` restricts libvirt/Waydroid using exact interface/CIDR/transport/port entries in `guestPrivateExceptions`; it does not change a separately enabled hotspot. LAN/CGNAT/ULA reachability is a compatibility choice, not evidence of trust. Passt and rootless containers that create host sockets follow the host path and need separate validation.
