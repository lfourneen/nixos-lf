# Network architecture

English | [简体中文](CN/network_CN.md) | [Documentation](README.md)

The host uses strict encrypted DNS and registered subscription sources.
Daily Firefox follows the A/B/C policy through Gost.
`firefox-identity` uses a separate network namespace and a dedicated proxy inlet available only in B.
Portal login in A uses a temporary browser.
See [source registration](operations.md#source-registration) for setup.

## Traffic paths

The host uses nftables to select direct access, a managed proxy path or blocking.

Three application paths converge on Mihomo when proxy mode is healthy:

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

Gost listens in the host namespace.
Host loopback access to core ports is translated onto a private veth.
The core listeners and TUN live in `lf-clash-core`.
Uppercase and lowercase environment variables, GSettings and Flatpak overrides point proxy-aware applications at `127.0.0.1:33332`.
Gost forwards both inlets to `127.0.0.1:7897` in B, passes traffic directly in A and closes its listeners in C or unknown state.
Explicit client connections to the transparent port `33333` are rejected before NAT.
Only redirected flows should enter it.

### Reserved ports and service identity

`user.slice` reserves the fixed proxy and local DNS ports through `SocketBindDeny`, covering TCP/UDP and IPv4/IPv6.
Desktop sessions and user managers inherit this restriction.
Other development ports remain available.
`ip_local_reserved_ports` excludes these ports from automatic allocation, including `bind(0)` and implicit client binds.
Host Gost replies require the expected root/Gost socket UID.
Core replies must arrive through the owned veth and tracked inlet connection.
Readiness checks use the listener address, UID, cgroup and network namespace.

Port reservation uses systemd's cgroup `bind4`/`bind6` hooks and requires kernel cgroup-BPF support.
Desktop processes inherit `user.slice`; Hermes uses `hermes.slice`.
Other declared non-root system services require a protected slice or an explicit infrastructure review.
Root and managed service identities are trusted.
Reply-UID checks authenticate the responding socket, while bind restrictions prevent delivery to an unauthorized UDP listener.
See [systemd's bind policy](https://www.freedesktop.org/software/systemd/man/260/systemd.resource-control.html#SocketBindAllow=bind-rule).

### Routing and DNS

Mihomo marks its sockets with `6666` / `0x1a0a` inside its namespace.
The host clears incoming veth packet marks and independently checks source identity, destination scope, reserved ports and reply ownership.
The legacy host socket-mark exception requires both marks and root/core UID.
Isolated core traffic cannot acquire that exception by forging a mark.
Rule routing can choose DIRECT under the default `my.proxy.proxyOnly=false`.

The optional [uniform proxy policy](proxy.md#routing-policy) refuses application DIRECT inside the core.
Private-network and infrastructure paths outside the core have their own firewall rules.
Inside the core, literal and resolved private targets have separate [local DIRECT and remote-leaf authorization](proxy.md#domain-targets).
Node selection follows subscription rules, while access to internal targets requires a matching target grant.

[DNS](dns.md) runs alongside this path: resolved → dnsmasq → Mihomo or Unbound.
Daily [Firefox](browser.md) uses Gost.
Its optional transparent path uses native DNS and host sockets under this TUN/firewall policy, allowing HTTPS-RR/ECH discovery.
The identity entry uses a dedicated Unix relay and strict core inlet.
Both run in an [outer filesystem and desktop-interface sandbox](browser.md#outer-sandbox), retaining the desktop UID.
Strict entries additionally isolate IP networking.

## Core network namespace

[core-network.nix](../system/config/network/proxy/core-network.nix) creates the private namespace and its veth link.
The host interface uses the configured `tunDev` name.
The actual TUN with that name exists only inside the core namespace.
A root observer without capabilities reads private listeners and routes through fixed operations.
Readiness requires both host and private routing checks to finish within a shared two-second deadline.

`my.proxy.coreLink` supplies private IPv4 /30 and IPv6 /64 endpoints and fixed local MAC addresses.
Setup and readiness refuse overlap with existing LAN/VPN addresses or routes.
Loss of the veth cannot redirect a core inlet onto a physical LAN.
Core transport lookup stops if the host main table has no route, preventing a loop back through the TUN table.
Physical address/route changes and resume renew the authorized helper/core.
An explicit `clash-on` reconstructs the namespace and drops existing core connections.

The host firewall rechecks core transports in both directions.
Public TCP/UDP and declared `localTargetCidrs` are available, with unsafe targets, reserved host services and external plaintext DNS separately restricted.
This is a transport boundary, not per-workload business-target authorization.
Localhost compatibility and the GUI channel use [separate Unix relays](proxy.md#components-and-identities), without host INET/netlink descriptors entering the core.

## Modes and enforcement

| Mode | Entry | Application egress | Default DNS upstream |
|---|---|---|---|
| A / `direct` | Cold boot with direct boot policy, or explicit `clash-off` | Direct access | Validating Unbound DoT, with bounded plaintext recovery |
| B / `proxy` | Proxy intent, authorized core, required TUN routes and health proof | Proxy, explicit exceptions, or drop | Mihomo fake-IP |
| C / `blocked` | Proxy requested but not ready, crashed or unhealthy | Blocked except recovery and link infrastructure | Unbound. External DoT is cut, eligible cached answers are served |

`proxy-mode` follows root-written, boot-scoped intent.
Closing the GUI or stopping/crashing the core after `clash-on` keeps C.
Use `clash-off` to request A.
Starting a GUI alone cannot authorize A → B.
`my.proxy.bootMode="proxy"` installs C before physical networking and waits for B.
The default is `"direct"`.

DNS transport has an independent policy: `my.proxy.dnsEncryption="strict"` disables A's public plaintext recovery and its runtime override, while retaining approved split DNS and MagicDNS.
The default `"opportunistic"` preserves portal recovery.
Both modes share domain-filter data between dnsmasq and the core, including proxy target checks.
See [DNS responsibilities](dns.md#responsibilities).

`unenforced` reports that the expected kernel policy could not be confirmed or repaired.
It is an error status, not another protected mode.
Applications using the shared readiness check refuse it.

The core's service cgroup, namespace-specific passive TCP/UDP listener identities, managed controller values, private TUN and host veth routes and actual unmarked IPv6 selection for root and the desktop UID feed the health decision.
HTTP probes must complete through the currently selected proxy leaf with matching source-port logs.
Another real HTTP request to a fake-IP proves the transparent TCP path.
The default shared deadline is 12 seconds.
Definite configuration, route or TUN failures block immediately.
Unavailable observations use two consecutive rounds, at least two seconds apart.

`health.json.scope` declares the invocation's coverage.
Only `ok=true` confirms successful completion.
`scope.requests` lists the TCP request paths and is empty for `--fast`.
`scope.udp_data_plane="not-probed"` makes the UDP boundary explicit: listener identity is checked, while UDP reachability and association return traffic are not continuously proven.
A UDP-only failure can therefore leave B and its TCP verdict healthy.

### B authorization lifetime

The host sets `my.proxy.authorizationLeaseSeconds=60`.
The module default is `null`, which leaves B authorization dependent on the running supervisor.
To enable kernel expiry, choose a lifetime from 30 to 120 seconds:

```nix
my.proxy.authorizationLeaseSeconds = 60;
```

Only a complete successful health round can renew authorization.
Each proof is consumed once and bound to the boot, managed configuration, policy generation and sleep epoch.
Renewal uses the proof's remaining lifetime.
A delayed observation or hysteresis round cannot mint a fresh full lease.

Timed nftables permissions cover both IP families.
When they expire, the installed rules apply C's request and reply guards before TUN/redirection and established-flow accepts, including existing host and guest connections.
The same C recovery and link-maintenance exceptions remain available.
A frozen or stopped supervisor cannot retain application authorization or select direct access.
Its last mode file can still say `proxy`, while readiness independently reports an expired authorization lease.

The sleep hook revokes authorization before suspend.
Resume requires a new successful proof, and a proof from before sleep cannot renew the lease.
Selecting `null` in a rebuilt generation explicitly removes this kernel expiry guarantee.

## Firewall lifecycle

[network.nix](../system/config/network/network.nix) integrates the static policy.
[mode-fragments.nix](../system/config/network/firewall/mode-fragments.nix) generates the B/C fragments.
[coordinator.nix](../system/config/network/proxy/coordinator.nix) installs the static rules plus a safety fragment in one scoped `nft -f` transaction under a shared lock.
Only repository-owned host tables are replaced, including their `lf_core_*` chains.
Mihomo owns its private namespace table.
Setup also installs private `lf_core_inlets` and `lf_core_local_udp` tables, plus `lf_core_dns` when local DNSSEC validation is enabled.
Other services retain their own host tables.

### Table ownership

The loader owns these exact table family/name pairs:

| Table | Responsibility |
|---|---|
| `inet lf_filter` | Host, guest and DNS filtering, including mode-dependent chains |
| `ip lf_nat`, `ip6 lf_nat` | Managed DNS redirection. IPv4 also supplies declared guest masquerading |
| `ip lf_proxymode_nat`, `ip6 lf_proxymode_nat` | Mode-B TCP redirection |
| `ip lf_mangle` | Optional IPv4 guest TPROXY |
| `inet lf_bootstrap` | Early ingress/forwarding baseline, removed by a successful complete load |

Startup, reload and drift repair use this same scope.
Shared iptables-nft tables such as `ip nat`, `ip6 nat`, `ip mangle` and `ip`/`ip6 filter`, plus native service tables such as `inet mihomo`, remain outside it.
External services should use separate tables.
Rules inserted into a repository-owned table are subject to replacement and drift repair.

Ordinary operation preserves `inet filter`, `ip proxymode_nat` and `ip6 proxymode_nat`.
The explicit [table migration](operations.md#firewall-table-migration) removes these targets and installs the complete policy in one locked transaction.
Cleanup is confined to that command.
A rejected transaction retains the installed tables and rules.

### Rule priorities

| Hook / priority | Purpose |
|---|---|
| Output `-202` | Restricted socket guest original-target/UID guard |
| Output `-201` | Reject direct use of Gost's transparent inlet before NAT |
| Prerouting `-201` | Apply guest public-destination restrictions before redirection |
| Prerouting `-199` | Guest source, private-target and gateway-service checks |
| Prerouting `-198` | Apply C's guest ingress guard when B authorization has expired |
| Output `-199` | Track restricted socket guest accounts. Limit dnsmasq upstream DNS to installed destination leases. Strict mode cuts the core's external port 53 except MagicDNS |
| Output `-198` | Reserve internal DNS ports, authenticate DNS/proxy replies, track ordinary application connections and recheck guest host-service replies |
| Output `-197` | C guard and expired B authorization guard, after conntrack at `-200` and before auto-redirect at `-150` |
| Output `-196` | Restrict independent DNS-over-TLS/QUIC on port 853 |
| Output `-195` | B optional private/multicast grants and optional Unbound DoT cut, before NAT/TUN |
| Output `-194` | Optional Chrony original-target CIDR bounds in A/B |
| Output NAT `-190`, filter `-90` | Force restricted socket guest DNS to 1056 and reject stale resolver associations |
| Input `-3`, forward `-2` | Apply C's receive/forward guards when B authorization has expired |
| Input `-2`, forward `-1` | Recheck restricted socket / routed guest replies against current grants |
| Input `-1` | C cuts decrypted tailnet packets and tracked host/guest application replies before established-flow accepts |

NAT priorities order rule lookups within the kernel’s shared destination-NAT hook.
The socket guest DNS filter at `-90` checks the actual rewritten target after that hook.

### Traffic in B

In B, public host TCP is redirected, fake-IP traffic retains its core path, and other public uplink traffic is dropped after the explicit exceptions.
These include marked core sockets, UID-scoped Unbound DoT and Chrony NTS/NTP, narrow DHCP, multicast and default private egress.
Tailscale UDP requires UID 0, a physical uplink, and both socket `SO_MARK` and packet mark equal to the upstream Linux bypass mark `0x80000`.
Peer/STUN ports remain dynamic.

Unmarked root UDP has no blanket exception.
Root services administer host policy.
The core can change only its private network namespace and remains trusted for application routing within the host-authorized transport scope.
The current LKL kernel supports `socket mark`, but not `socket cgroupv2`.

Tailscale's bypass mark is the upstream constant `0x80000`, independent of the active network.
[transport.nix](../system/config/network/tailscale/transport.nix) checks the selected package's marking behavior during system builds.
If marking fails, B continues to block unmarked root UDP.

The [direct-egress ledger](egress.md) records every actor, target, port and mode.
Optional restricted B grants use interface/UID/CIDR/ports and cut revoked established host-initiated flows before TUN/NAT.
Host Unbound is limited to configured DoT targets even in A.
Its B exception can be disabled.
Chrony can pin destination CIDRs in A/B.
Defaults preserve LAN and changing NTS addresses.

### Traffic in C

In C, an early output guard blocks application traffic before the core can redirect it.
It preserves loopback IPC, marked core sockets, root recovery through proxy/DNS ports and fake-IP ports 53/80/443, narrow DHCP and hop-limit-255 NDP.
Non-root access to the core/Gost application ports is blocked for TCP and UDP.
Self-test reply exceptions require the expected loopback source or root/core conntrack DNAT reply to an original fake-IP HTTP destination.

Guest DHCP/DNS replies are bound to bridge, gateway source and port rather than daemon UID.
Host Unbound and ordinary tailnet traffic lose external egress, including established remote-desktop flows.
B grants provide no C remote-control exception.
A separate input guard drops decrypted tailnet packets before established-flow accepts, including packets already in flight.

C blocks replies to tracked host-initiated application TCP/UDP flows, covering native TUN, fake-IP and explicit SOCKS UDP.
The output hook at priority `-198` assigns conntrack mark `0x4c460001` to unmarked ordinary application connections.
Restricted guests use separate marks.
Root, core and Gost transport connections are excluded.
The C input guard drops marked network and fixed proxy-inlet replies.
Enforcement at the receiving side allows a live SOCKS UDP association to resume when B is restored.

Receive enforcement depends on these conntrack marks.
Other firewall owners must preserve them.
The high conntrack bit records flows sent into the core veth.
Application/guest identity checks preserve and mask that bit.
Socket-queued data is outside this enforcement boundary.
Loopback IPC, local DNS, root/core recovery and link maintenance remain available in C.

### Loading and recovery

[bootstrap.nix](../system/config/network/firewall/bootstrap.nix) installs ingress/forwarding protection before NetworkManager or dhcpcd.
A failed first full load retains that baseline.
Static drift triggers a complete repair into A for direct intent or C for proxy intent, followed by health evaluation.
A failed repair reports `unenforced`.

Stopping `nftables.service` leaves the rules installed.
Restart or reload reconciles them.
Avoid `nft flush ruleset`, which also destroys core-owned tables.
[Operations](operations.md) describes verification and recovery.

## Uplinks and local services

Wired `ens1` uses dhcpcd and is excluded from NetworkManager by name and hardware MAC.
Its name is matched by permanent MAC, independently of a cloned on-link address. dhcpcd runs in native single-interface mode, binding its DHCPv6 listener to the wired link-local address so Wi-Fi DHCPv6 can operate concurrently.

Wi-Fi `wlo1` uses NetworkManager, with a stable PCI-path match.
Scans randomize their MAC.
Connections default to `stable-ssid`.
Persisted NetworkManager state and machine ID retain that MAC identity across root rollback.
Explicit profile settings override the MAC default.
[Link identity](identity.md) explains coordinated per-profile MAC/DHCP/IPv6 migration, wired alternatives, persistent state and reauthorization.

Tailscale accepts neither DNS, subnet routes nor an exit node automatically.
Only the authenticated tailnet's MagicDNS suffix receives conditional resolver routing.
Host input defaults to Sunshine streaming TCP 47984/47989/48010 and UDP 47998/47999/48000/48002/48010.
Sunshine starts with the graphical session.
Its administration UI stays local and UPnP is disabled.
See [remote desktop operation](operations.md#remote-desktop).

Avahi advertises on loopback and tailnet, plus an explicitly enabled AP.
A Wi-Fi client on a foreign network does not advertise.
This configuration does not import the sshd module.
A firewall allowance alone does not create an SSH listener.

[time.nix](../system/config/network/time.nix) configures Chrony with authenticated NTS servers, UDP 123 and NTS-KE TCP 4460.
RTC/drift restoration provides an offline time floor.
Certificate-time checks stay enabled and no plaintext time fallback is configured.
[hardening.nix](../system/config/network/hardening.nix) supplies shared sysctl, redirect/source-route and module restrictions.
Nix daemon trust is restricted to root.

## Guests and containers

[policy.nix](../system/config/network/guests/policy.nix) distinguishes routed guest networks from backends that create host sockets.
Trust is assigned by declared ingress interfaces, source CIDRs and dedicated backend UIDs.

| Class | Identity | Private destinations | Public destinations |
|---|---|---|---|
| Restricted bridge / hotspot, default | Exact ingress interface and declared source CIDR | Explicit interface/CIDR/TCP or UDP port grants | A allows IPv4. B requires explicit IPv4 TPROXY selection. C refuses |
| Trusted development bridge | Same interface/source checks. Explicit `trust="trusted"` | Private routing allowed, with unsafe targets still refused | Same public policy as restricted bridges |
| Restricted host-socket backend | Dedicated fixed UID and all its delegated subordinate UIDs | Explicit account/original-output-interface/CIDR/port grants | Host A/B/C policy. C cuts tracked replies too |
| Desktop-owned passt / rootless Docker | Shared desktop UID | Host policy | Host policy |

`my.proxy.guestInterfaces` defaults to `virbr0` and `waydroid0`.
An enabled dedicated AP joins that list.
`my.hardening.guestNetworks.<interface>` supplies its trust, disjoint canonical `sourceCidrs`, gateway addresses and host-service ports.
Built-in roles receive their machine IPv4 subnet/gateway.
IPv6 needs an explicit ULA source subnet.
Public guest IPv6 is refused in every mode.
Wi-Fi client uplinks cannot become guest roles.
Creating an AP, DHCP server or bridge remains separate from installing its firewall policy.

Private forwarding defaults to restricted for all roles, including the hotspot.
`guestPrivateExceptions` grants exact ingress interface, private destination CIDR and TCP/UDP ports.
`trust="trusted"` permits private routing for a development network.
It does not expose arbitrary host services or grant public B/C forwarding.
The legacy `guestPrivateEgress=true` trusts only libvirt/Waydroid roles whose `trust` is unset.
Explicit per-interface choices take precedence.

Gateway DHCP/DNS remains separate from the host loopback resolver.
`hostTcpPorts` / `hostUdpPorts` expose services only on that role's declared gateway addresses.
The legacy libvirt `guestHostServices` and port lists feed the same policy.
Source checks run before redirection, host access is checked before established input accepts, and current grants constrain both directions of established routed connections and host-service replies.

Revocation therefore also cuts unsolicited server pushes.
Trusted and restricted peers need separate bridges: this policy identifies a network, and does not isolate same-bridge layer-2 traffic or authenticate individual peers.

`transparentGuestInterfaces` defaults to empty and selects only explicit IPv4 TPROXY inlets in healthy B.
Valid fake-IP mappings require that inlet.
Synthetic pools, selected metadata endpoints and known translation/transition ranges otherwise remain denied.
C cuts already proxied guest replies while retaining core recovery.
Private bridge grants remain independent of the public mode guard.

Passt and rootless networking terminate guest packets into host sockets, so bridge rules cannot identify them.
Use `my.hardening.socketGuestUsers` with a separate fixed nonprivileged account for an untrusted backend.
The normal build rejects desktop/root/infrastructure identities, shared UIDs and overlapping delegated ranges.
`socketGuestPrivateExceptions` grants that account an original output interface, private CIDR and destination ports.

Loopback/proxy inlets, reserved proxy/DNS destination ports, fake-IP and known translation targets cannot be granted.
TCP/UDP port 53 is forced to the [filtered real-address resolver](dns.md#restricted-socket-guest-dns), so B's synthetic addresses cannot hide a private final target from the UID guard.
The rootless Docker launcher clears inherited explicit proxy variables for these accounts.

Run each backend under its declared account after installing the policy.
Stop existing backends before adding/changing restricted identities or UID mappings.
Reply tracking cannot retroactively identify an old unmarked socket.
Removing a private grant while the identity remains declared cuts both directions of its tracked connections.
Desktop-owned rootless Docker remains a trusted development environment because its sockets share the desktop UID.

UID policy governs network egress.
Filesystem access, shared Unix sockets, inbound publishing and same-bridge peer isolation need their own controls.
Grants match original host socket/routed destinations, not destinations inside an application tunnel.
Root remains trusted for host administration.
The isolated core remains trusted for application routing within its transport authorization.
See [configuration examples](operations.md#configuration).

The host assigns Hermes Docker workloads to the dedicated `hermes-containers` account, UID 1200 by default, with separate subordinate UID/GID ranges.
Its native sockets use the restricted account policy.
Hermes agent, dashboard, local terminal and MCP processes retain their own host identity and can use Gost in healthy B.
C blocks their application proxy traffic.
Membership in the `hermes` group does not grant a separate network exception.

Containers in the same backend share its network authorization.
See [Hermes containers](operations.md#hermes-containers) for endpoints, data and service management.
