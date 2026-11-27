# Network architecture

English | [简体中文](CN/network_CN.md) | [Documentation](README.md)

The host enforces strict encrypted DNS and requires registered subscription sources. Daily Firefox uses system DNS and shares the same transparent **A**/**B**/**C** policy as other desktop apps, `firefox-identity` runs in its own network namespace behind a B-only proxy inlet, and captive-portal logins in **A** use a disposable browser. See [source registration](operations.md#source-registration) for initial setup.

## Traffic paths

The host uses `nftables` to route between direct access, a managed proxy path, or full blocking. Applications open native TCP and UDP sockets; in healthy **B**, both the TUN path and the TCP fallback converge on Mihomo:

```text
Native TCP and UDP                    TCP fallback
        |                                  |
   TUN "Mihomo"                 nft REDIRECT -> Gost :33333
        |                                  |
        +------> Mihomo <---------- mixed :7897
                    |
            rules or fixed exit
```

Gost listens in the host namespace, while host loopback access to core ports is forwarded across a private `veth` into `lf-clash-core`, where the core listeners and actual TUN live. Because the host enables `my.proxy.transparentApplications`, Niri, Brush, GSettings, Flatpak, Zed, the Nix daemon, and Hermes all use native sockets without any managed app-level proxy variables.

On the first Flatpak installation service run after enabling transparency, the service strips unchanged legacy Gost settings from the global system override whenever `nix-flatpak` records ownership (edited or unverified keys stay put and are noted in the journal; user and per-app overrides are untouched). Log out and back in after switching generations so session processes pick up the updated environment. In **A**, native sockets go out directly; **C** blocks ordinary external traffic. Setups using the compatibility default (`my.proxy.transparentApplications = false`) can still point clients at Gost HTTP on `127.0.0.1:33332`.

With transparent applications on, the firewall rejects ordinary TCP and UDP connections straight to the Gost and Mihomo inlets in **A**, **B**, and **C**—it checks local destinations before NAT and only allows managed relays to reach the mixed and strict core inlets. The core controller sits on a private Unix socket that Clash Verge accesses through its managed Unix forwarder, while the identity browser uses its own [isolated relay](browser.md#network-policy-and-identities).

### Reserved ports and service identity

`user.slice` reserves the fixed proxy and local DNS ports via `SocketBindDeny` for both TCP/UDP and IPv4/IPv6, so desktop sessions and user managers inherit the restriction while leaving other development ports free. `ip_local_reserved_ports` also keeps those ports—along with the configured fixed Tailscale UDP port—out of automatic ephemeral allocation (`bind(0)` and implicit client binds). Meanwhile, host Gost replies must come from the expected root/Gost socket UID, core replies must arrive over the owned `veth` and tracked inlet connection, and readiness checks verify listener address, UID, cgroup, and network namespace.

Port reservation relies on systemd's cgroup `bind4`/`bind6` hooks and kernel cgroup-BPF support. Desktop processes inherit `user.slice` and Hermes runs in `hermes.slice`; any other non-root system service needs a protected slice or an explicit infrastructure review. Root and managed service identities form the trust boundary—reply-UID checks authenticate the responding socket, and bind restrictions prevent unauthorized UDP listeners from receiving traffic (see [systemd's bind policy](https://www.freedesktop.org/software/systemd/man/260/systemd.resource-control.html#SocketBindAllow=bind-rule)).

### Routing and DNS

Mihomo marks its own sockets with `6666` / `0x1a0a` inside its namespace. The host scrubs incoming `veth` packet marks and independently checks source identity, destination scope, reserved ports, and reply ownership, so isolated core traffic can never acquire the legacy host socket-mark exception (which requires both marks and a root/core UID) by forging a mark. Under the default `my.proxy.proxyOnly=false`, `Rule` routing can pick `DIRECT`; the optional [uniform proxy policy](proxy.md#routing-policy) refuses application `DIRECT` inside the core, while private-network and infrastructure paths outside the core keep their own firewall rules. Inside the core, literal and resolved private targets have separate [local DIRECT and remote-leaf authorization](proxy.md#domain-targets)—subscription rules pick the node, and reaching internal targets requires a matching target grant.

[DNS](dns.md) runs alongside this path (`resolved → dnsmasq → Mihomo or Unbound`). Daily [Firefox](browser.md) normally uses Gost, or with its optional transparent path uses native DNS and host sockets under this same TUN/firewall policy so HTTPS-RR/ECH discovery works. The identity browser uses a dedicated Unix relay and strict core inlet; both run inside an [outer filesystem and desktop-interface sandbox](browser.md#outer-sandbox) under the desktop UID, with strict entries also isolating IP networking.

## Core network namespace

[core-network.nix](../system/config/network/proxy/core-network.nix) sets up the private namespace and its `veth` link. The host-side interface uses the configured `tunDev` name, while the actual TUN with that name lives solely inside `lf-clash-core`; a capability-free root observer reads private listeners and routes via fixed operations, and readiness requires both host and private routing checks to finish within a shared two-second deadline.

`my.proxy.coreLink` provides private IPv4 `/30` and IPv6 `/64` endpoints with fixed local MACs, and both setup and readiness reject any overlap with existing LAN/VPN addresses or routes. Losing the `veth` cannot spill a core inlet onto a physical LAN, and core transport lookups fail immediately if the host main table has no route rather than looping back through the TUN table. Physical address/route changes and resume refresh the authorized helper/core, while an explicit `clash-on` rebuilds the namespace and drops existing core connections.

The host firewall checks core transports in both directions: forwarded public TCP/UDP and declared `localTargetCidrs` are allowed, with unsafe targets, reserved ports, and external plaintext DNS restricted. Host input from the core `veth` accepts tracked replies carrying the core-link conntrack mark, and drops any new flows targeting host listeners on private or public addresses. This is a transport boundary rather than per-workload business-target authorization; localhost compatibility and the GUI channel use [separate Unix relays](proxy.md#components-and-identities) without passing host INET/netlink descriptors into the core.

## Modes and enforcement

| Mode | Entry | Application egress | Default DNS upstream |
|---|---|---|---|
| **A** / `direct` | Direct boot policy or explicit `clash-off` | Direct access | Validating Unbound DoT, with bounded plaintext recovery |
| **B** / `proxy` | Proxy intent, authorized core, required TUN routes, and health proof | Proxy, explicit exceptions, or drop | Managed fake-IP, or validated real addresses with the WireGuard exit |
| **C** / `blocked` | Proxy requested but not ready, crashed, or unhealthy | Blocked except recovery and link infrastructure | Unbound (external DoT cut; eligible cached answers served) |

`proxy-mode` follows root-written, boot-scoped intent. Closing the GUI leaves proxy intent intact—if the core stops or crashes while proxy intent is active, the host drops into **C**. Run `clash-off` to switch to **A**, or see [recover a registered source](operations.md#recover-a-registered-source) to fetch a source while staying in **C**. Starting the GUI alone never authorizes `A → B`. `my.proxy.bootMode="proxy"` (default `"direct"`) installs **C** before physical networking comes up and waits for **B**.

DNS transport has its own independent knob: `my.proxy.dnsEncryption="strict"` disables **A**'s public plaintext recovery and its runtime override while keeping approved split DNS and MagicDNS, whereas the default `"opportunistic"` preserves portal recovery. Both share domain-filter data between `dnsmasq` and the core, including proxy target checks (see [DNS responsibilities](dns.md#responsibilities)). `unenforced` is an error state—not another protected mode—indicating that the expected kernel policy could not be confirmed or repaired, and apps using the shared readiness check reject it.

Health evaluation looks at the core's service cgroup, namespace-specific passive TCP/UDP listener identities, managed controller values, private TUN and host `veth` routes, and actual unmarked IPv6 selection for both root and the desktop UID. Mixed-port HTTP runs before the DNS/TUN checks so its result can be diagnosed on its own. With TUN enabled, all configured candidate names are warmed concurrently and their fake-IPs are reused in the same probe—this host tries `cp.cloudflare.com` first and `connectivity-check.ubuntu.com` second (both routed through `lf-remote` in `rules` mode). TUN TCP tries candidates in order, requiring at least one to complete fake-IP DNS, TUN DNS hijack, and real HTTP. HTTP route proof matches source address, source port, and destination against the core log or a live connection-table entry (allowing up to 2.5 seconds for log delivery within the shared deadline—12 seconds by default, raised to 18 seconds here for cold DNSSEC lookups). Mixed HTTP requires a remote proxy, while TUN follows the root-approved route class for its target. Definite config, route, or TUN failures block immediately; observation, DNS, leaf, and app-resolver failures get three consecutive rounds at least two seconds apart, sharing the health budget with leaf recovery, candidate session samples, and system resolver diagnostics. Each fake-IP lookup and TUN DNS self-test uses `my.proxy.healthDnsTimeoutSeconds` (default 3 seconds per attempt, with one retry within the shared deadline).

`data_plane_ok`, `route_ok`, `dns_ok`, and `tun_ok` report per-layer verdicts (`null` means unproven or out of scope), and only an overall `ok=true` authorizes **B** or renews its lease. `health.json.scope` records what the invocation covered: `scope.requests` lists active request paths (empty for `--fast`), and the WireGuard exit adds real-address DNS plus native IPv4/IPv6 echo to the full proof. `scope.udp_data_plane="not-probed"` makes the UDP boundary explicit—listener identity is checked, while UDP reachability and association return traffic are not continuously probed, so a UDP-only outage can leave **B** and its TCP verdict healthy. In runtime `proxy-only`, the selected adapter reports `unsupported` when it lacks UDP, and IPv6 target support depends on the node.

Mode **B** has two routing selections: `rules` and `proxy-only`. `clash-rules` and `clash-proxy-only` transition through **C** and wait for verified **B**, while `proxy-status` shows the selection and transaction state in **B** and **C**. `clash-off`/`clash-on` preserves the choice within the current boot, and rebooting resets to `rules` (see [routing policy](proxy.md#routing-policy)).

### B authorization lifetime

This host sets `my.proxy.authorizationLeaseSeconds=60` (the module default is `null`, which leaves **B** authorization tied to the running supervisor; valid kernel-expiry values are 30–120 seconds):

```nix
my.proxy.authorizationLeaseSeconds = 60;
```

Only a complete, passing health round can renew the lease. Each proof is single-use and bound to the boot, managed configuration, policy generation, and sleep epoch, and renewal uses only the proof's remaining lifetime—a delayed observation or hysteresis round cannot mint a fresh full lease.

Timed `nftables` permissions cover both IP families: once they expire, the installed rules enforce **C**'s request and reply guards ahead of TUN/redirection and established-flow accepts—including existing host and guest connections—while leaving **C**'s recovery and link-maintenance exceptions intact. A frozen or dead supervisor cannot keep application authorization alive or fall back to direct access; its last mode file may still say `proxy`, while readiness independently reports an expired lease. The sleep hook revokes authorization before suspend so resume always needs a fresh proof, and setting the option back to `null` in a rebuilt generation removes this kernel expiry guarantee.

## Firewall lifecycle

[network.nix](../system/config/network/network.nix) integrates the static policy, [mode-fragments.nix](../system/config/network/firewall/mode-fragments.nix) generates the B/C fragments, and [coordinator.nix](../system/config/network/proxy/coordinator.nix) installs the static rules plus a safety fragment in a single scoped `nft -f` transaction under a shared lock. Only repository-owned host tables (and their `lf_core_*` chains) are replaced; Mihomo owns its private namespace table, setup installs private `lf_core_inlets` and `lf_core_local_udp` tables (plus `lf_core_dns` when local DNSSEC is on), and other services keep their own host tables.

### Table ownership

The loader owns these exact table family/name pairs:

| Table | Responsibility |
|---|---|
| `inet lf_filter` | Host, guest, and DNS filtering, including mode-dependent chains |
| `inet lf_policy_guard` | Independent per-packet enforcement if the primary table or required mode chains disappear during proxy intent |
| `ip lf_nat`, `ip6 lf_nat` | Managed DNS redirection; IPv4 also supplies declared guest masquerading |
| `ip lf_proxymode_nat`, `ip6 lf_proxymode_nat` | Mode-B TCP redirection |
| `ip lf_mangle` | Optional IPv4 guest TPROXY |
| `arp lf_arp` | Optional IPv4 ARP frame validation on declared links |
| `inet lf_bootstrap` | Early ingress/forwarding baseline, removed by a successful complete load |

Startup, reload, and drift repair all operate within this scope. Shared iptables-nft tables (`ip nat`, `ip6 nat`, `ip mangle`, `ip`/`ip6 filter`) and native service tables like `inet mihomo` stay outside it—external services should use their own tables, as rules injected into repository-owned tables are subject to replacement and drift repair. Normal runs leave `inet filter`, `ip proxymode_nat`, and `ip6 proxymode_nat` alone; the explicit [table migration](operations.md#firewall-table-migration) removes those targets and installs the full policy in one locked transaction (leaving existing tables untouched if the transaction is rejected).

### Rule priorities

| Hook / priority | Purpose |
|---|---|
| Output `-202` | Restricted socket guest original-target/UID guard |
| Output `-201` | Reject direct use of Gost's transparent inlet before NAT |
| Prerouting `-201` | Apply guest public-destination restrictions before redirection |
| Prerouting `-199` | Guest source, private-target, and gateway-service checks |
| Prerouting `-198` | Apply C's guest ingress guard when B authorization has expired |
| Output `-199` | Track restricted socket guest accounts; limit dnsmasq upstream DNS to installed destination leases; strict mode cuts the core's external port 53 except MagicDNS |
| Output `-198` | Reserve internal DNS ports, authenticate DNS/proxy replies, track ordinary application connections, and recheck guest host-service replies |
| Output `-197` | C guard and expired B authorization guard, after conntrack at `-200` and before auto-redirect at `-150` |
| Output `-196` | Restrict independent DNS-over-TLS/QUIC on port 853 |
| Output `-195` | B optional private/multicast grants and optional Unbound DoT cut, before NAT/TUN |
| Output `-194` | Optional Chrony original-target CIDR bounds in A/B |
| Output NAT `-190`, filter `-90` | Force restricted socket guest DNS to 1056 and reject stale resolver associations |
| Input `-3`, forward `-2` | Apply C's receive/forward guards when B authorization has expired |
| Input `-2`, forward `-1` | Recheck restricted socket / routed guest replies against current grants |
| Input `-1` | C cuts decrypted tailnet packets and tracked host/guest application replies before established-flow accepts |

NAT priorities order lookups inside the kernel's shared destination-NAT hook, and the socket guest DNS filter at `-90` inspects the rewritten destination afterward.

### Traffic in B

In **B**, public host TCP is redirected, fake-IP traffic stays on its core path, and any other public uplink traffic is dropped once explicit exceptions are checked: marked core sockets, UID-scoped Unbound DoT and Chrony NTS/NTP, narrow DHCP, multicast, and default private egress. Tailscale UDP requires UID 0, a physical uplink, and **both** socket `SO_MARK` and packet mark equal to upstream `LinuxBypassMarkNum` (`0x80000`), with dynamic peer/STUN ports. Unsolicited physical ingress uses `services.tailscale.port` on either uplink and checks the receiving UDP socket's bypass mark ahead of established-flow accepts (supporting both IP families in **A** and Rules **B**, while Strict **B**, **C**, and expired **B** cut reception; setting `services.tailscale.port = 0` falls back to automatic port allocation and the normal tracked-reply path).

Unmarked root UDP has no blanket exception. Root services administer host policy, while the core can only touch its private network namespace and is trusted for application routing within its host-authorized transport scope (the current LKL kernel supports `socket mark`, though not `socket cgroupv2`). Tailscale's bypass mark is the upstream constant `0x80000`, independent of the active network; [transport.nix](../system/config/network/tailscale/transport.nix) verifies the selected package's marking behavior at build time, and if marking fails, **B** still blocks unmarked root UDP.

The [direct-egress ledger](egress.md) documents every actor, target, port, and mode. Optional restricted **B** grants match interface/UID/CIDR/ports and cut revoked established host-initiated flows before TUN/NAT. Host Unbound is restricted to configured DoT targets in **A** (and the separate infrastructure Unbound's Rules **B** exception can be turned off), while Chrony can pin destination CIDRs in **A**/**B**; defaults keep LAN and changing NTS addresses working.

### Traffic in C

In **C**, an early output guard blocks ordinary application traffic before the core can redirect it, preserving only loopback IPC, marked core sockets, root recovery via proxy/DNS ports and fake-IP ports 53/80/443, narrow DHCP, and hop-limit-255 NDP. Non-root TCP/UDP access to core/Gost application ports is blocked, and self-test reply exceptions require either the expected loopback source or a root/core conntrack DNAT reply to an original fake-IP HTTP destination.

Guest DHCP/DNS replies are matched by bridge, gateway source, and port rather than daemon UID. Host Unbound and ordinary tailnet traffic lose external egress—including established remote-desktop flows—since **B** grants give no **C** remote-control exception, and a separate input guard drops decrypted tailnet packets (even those already in flight) before established-flow accepts.

**C** also cuts replies to tracked host-initiated TCP/UDP flows across native TUN, fake-IP, and explicit SOCKS UDP: the `output -198` hook tags unmarked ordinary application connections with conntrack mark `0x4c460001` (restricted guests use separate marks, while root, core, and Gost transport connections are excluded), and the **C** input guard drops marked network and fixed proxy-inlet replies. Enforcing this on receive lets a live SOCKS UDP association resume once **B** comes back. Because receive enforcement depends on these conntrack marks, other firewall owners must preserve them (the high conntrack bit tracks flows entering the core `veth`, and application/guest identity checks mask and preserve that bit). Data already queued in a socket buffer is outside this boundary; loopback IPC, local DNS, root/core recovery, and link maintenance stay available in **C**.

### Loading and recovery

[bootstrap.nix](../system/config/network/firewall/bootstrap.nix) installs ingress, forwarding, and DNS protection ahead of NetworkManager or `dhcpcd`, and if the first full load fails, that baseline stays in place. Static drift triggers a full repair into **A** (for direct intent) or **C** (for proxy intent) followed by health checks, reporting `unenforced` if repair fails. During proxy intent, `inet lf_policy_guard` checks every packet to ensure it traversed the required main policy chains, cutting new and established flows immediately—even if an old **B** lease hasn't expired yet—whenever the main table or one of those chains goes missing.

The coordinator and verifier dump full `nftables` rulesets into root-only runtime directories and delete the snapshots right after comparison, while desktop diagnostics read the published mode, verification verdict, and counter summaries. Stopping `nftables.service` leaves rules loaded, restart/reload reconciles them, and you should avoid `nft flush ruleset` since it wipes core-owned tables too. See [Operations](operations.md) for verification and recovery.

## Uplinks and local services

Wired `ens1` runs under `dhcpcd` and is excluded from NetworkManager by name and hardware MAC; its interface name is matched by permanent MAC independently of any cloned on-link address. `dhcpcd` runs in native single-interface mode and binds its DHCPv6 listener to the wired link-local address so NetworkManager's Wi-Fi DHCPv6 client can run alongside it.

Wi-Fi `wlo1` is managed by NetworkManager with a stable PCI-path match. Scans randomize the MAC, and connections default to `stable-ssid` (persisted NetworkManager state and machine ID keep that MAC identity stable across root rollbacks, while explicit profile settings override the default). See [Link identity](identity.md) for coordinated per-profile MAC/DHCP/IPv6 migration, wired options, persisted state, and reauthorization, and [IPv4 ARP protection](identity.md#ipv4-arp-protection) for trusted gateways, reply scope, and link-level isolation.

Tailscale does not auto-accept DNS, subnet routes, or an exit node; only the authenticated tailnet's MagicDNS suffix gets conditional resolver routing. Host input defaults to Sunshine streaming (TCP 47984/47989/48010 and UDP 47998/47999/48000/48002/48010). Sunshine starts with the graphical session, keeps its admin UI local, and disables UPnP (see [remote desktop operation](operations.md#remote-desktop)).

Avahi advertises only on loopback, tailnet, and an explicitly enabled AP—never as a Wi-Fi client on an untrusted network. This configuration does not import `sshd` (a firewall allowance by itself never creates an SSH listener). [time.nix](../system/config/network/time.nix) configures Chrony with authenticated NTS servers (UDP 123, NTS-KE TCP 4460), using RTC/drift restoration for an offline time floor while keeping certificate-time checks on with no plaintext fallback. [hardening.nix](../system/config/network/hardening.nix) applies shared sysctl, redirect/source-route, and module restrictions, and Nix daemon trust is limited to root.

## Guests and containers

[policy.nix](../system/config/network/guests/policy.nix) separates routed guest networks from backends that open host sockets, assigning trust by declared ingress interfaces, source CIDRs, and dedicated backend UIDs.

| Class | Identity | Private destinations | Public destinations |
|---|---|---|---|
| Restricted bridge / hotspot (default) | Exact ingress interface and declared source CIDR | Explicit interface/CIDR/TCP or UDP port grants | **A** allows IPv4; **B** requires explicit IPv4 TPROXY selection; **C** refuses |
| Trusted development bridge | Same interface/source checks + explicit `trust="trusted"` | Private routing allowed, with unsafe targets still refused | Same public policy as restricted bridges |
| Restricted host-socket backend | Dedicated fixed UID and all its delegated subordinate UIDs | Explicit account/original-output-interface/CIDR/port grants | Host **A**/**B**/**C** policy; **C** cuts tracked replies too |
| Desktop-owned passt / rootless Docker | Shared desktop UID | Host policy | Host policy |

`my.proxy.guestInterfaces` defaults to `virbr0` and `waydroid0`, adding a dedicated AP when enabled. `my.hardening.guestNetworks.<interface>` defines trust level, disjoint canonical `sourceCidrs`, gateway addresses, and host-service ports (built-in roles inherit their machine IPv4 subnet/gateway, IPv6 requires an explicit ULA source subnet, public guest IPv6 is refused in all modes, and Wi-Fi client uplinks cannot be guest roles). Creating an AP, DHCP server, or bridge stays separate from installing its firewall policy.

Private forwarding defaults to restricted for every role, including hotspots. `guestPrivateExceptions` grants exact ingress interface, private destination CIDR, and TCP/UDP ports, while `trust="trusted"` opens private routing for a development network without exposing arbitrary host services or granting public B/C forwarding (legacy `guestPrivateEgress=true` trusts only libvirt/Waydroid roles without an explicit `trust`, and per-interface settings win).

Gateway DHCP/DNS stays separate from the host loopback resolver, and `hostTcpPorts`/`hostUdpPorts` expose services only on that role's declared gateway addresses (legacy `guestHostServices` and port lists merge into the same policy). Source checks run before redirection, host access is checked before established input accepts, and current grants constrain both directions of established routed connections and host-service replies—so revoking a grant cuts unsolicited server pushes too. Trusted and restricted peers need separate bridges, since this policy identifies networks rather than isolating same-bridge layer-2 traffic or authenticating individual peers.

`transparentGuestInterfaces` is empty by default and only enables explicit IPv4 TPROXY inlets in healthy **B** (valid fake-IP mappings require that inlet; synthetic pools, selected metadata endpoints, and known translation/transition ranges stay denied, and **C** cuts already-proxied guest replies while keeping core recovery). Private bridge grants remain independent of the public mode guard.

Because `passt` and rootless networking terminate guest packets into host sockets, bridge rules cannot tell them apart. For untrusted backends, declare a dedicated fixed nonprivileged account via `my.hardening.socketGuestUsers` (normal builds reject desktop/root/infrastructure identities, shared UIDs, and overlapping delegated ranges) and grant private access with `socketGuestPrivateExceptions` (account, original output interface, private CIDR, and destination ports). Loopback/proxy inlets, reserved proxy/DNS destination ports, fake-IP, and known translation targets cannot be granted; TCP/UDP port 53 is forced to the [filtered real-address resolver](dns.md#restricted-socket-guest-dns) so **B**'s synthetic addresses can't hide a private final target from the UID guard, and the rootless Docker launcher strips inherited explicit proxy variables for those accounts.

Install the policy before starting backends under their declared accounts, and stop running backends before adding or changing restricted identities or UID mappings (reply tracking can't retroactively tag an old unmarked socket). Removing a private grant while keeping the identity declared cuts both directions of its tracked connections. Desktop-owned rootless Docker remains a trusted dev environment because its sockets share the desktop UID. Keep in mind that UID policy only governs network egress—filesystem access, shared Unix sockets, inbound publishing, and same-bridge peer isolation need their own controls, and grants match original host socket/routed destinations rather than destinations inside an application tunnel. Root remains trusted for host administration, and the isolated core is trusted for application routing within its transport authorization (see [configuration examples](operations.md#configuration)).

This host runs Hermes Docker workloads under a dedicated `hermes-containers` account (UID 1200 by default, with separate subordinate UID/GID ranges), whose native sockets follow the restricted account policy. Meanwhile, the Hermes agent, dashboard, local terminal, and MCP processes keep their own host identity and can use Gost in healthy **B** (**C** blocks their application proxy traffic, and membership in the `hermes` group grants no separate network exception). Containers in the same backend share its network authorization—see [Hermes containers](operations.md#hermes-containers) for endpoints, data, and service management.
