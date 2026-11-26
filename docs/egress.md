# Direct egress exceptions

English | [简体中文](CN/egress_CN.md) | [Documentation](README.md)

B in rules mode enforces a managed path plus the exceptions below.
Subscription Rule routing may still choose DIRECT.
A remains deliberate direct access.
C blocks ordinary application egress and decrypted tailnet input, including remote desktop and established connections, and never inherits B's optional grants.

Strict browser namespaces have no host IP path and reach only `lf-strict` through a dedicated Unix relay.
A/C refuse traffic to/from core port `7898` in the atomic policy transaction.
B opens it.
The relay checks the established server socket’s core UID before forwarding the first browser bytes, avoiding a listener-only race.
Actual leaf checks reject DIRECT and private targets.
This applies to managed browser entries.
Ordinary desktop and other applications retain the ledger below.

## Proxy-only egress

The ledger below describes rules-mode exceptions. Runtime proxy-only adds a separate host boundary before those exceptions can grant physical egress.
The outer transport accepts the selected core node's fixed TCP/UDP tuple. With the WireGuard exit configured, the physical allowlist instead pins the peer's UDP endpoint and the relay tuple is allowed only inside the tunnel.
Ordinary native applications, root services and restricted socket identities receive no independent public, LAN, multicast, Chrony, Tailscale or inbound-reply permission.
Existing direct reply flows are cut as well as new requests. Loopback IPC, scoped DHCP/NDP and necessary errors for authorized outer transports remain available.

Configured IPv4 transparent guests may use their declared core inlet. Other routed guest traffic, including public IPv6 and private forwarding, is refused in strict mode.
This controls traffic routed by the host; same-bridge layer-two traffic remains a separate boundary.
Restricted backends continue to require real-address DNS and cannot borrow explicit host proxy ports or fake-IP targets.
With the [WireGuard exit](proxy.md#wireguard-exit-with-real-ping), public IPv4 and IPv6 echo requests use the same peer as the fixed relay. Fake-IP echo is refused, and ordinary application echo requires the live kernel lease. With application-proxy transport, proxy-only refuses echo. UDP remains outside the continuous data-plane probe.

The whole switch passes through C and consumes a fresh, epoch-bound full proof before kernel authorization is renewed.
Explicit `clash-off` restores A. Returning to rules uses the same controlled transaction.
`proxy-inspect --drops` includes the strict input and output refusal counters.

## Exception ledger

UIDs, interfaces and ports come from [machine.nix](../system/config/machine.nix), service accounts and [options.nix](../system/config/network/options.nix).
This table describes host output.
Guest forwarding and host input have separate policies.

| Actor / path | Destination and transport | Modes and limits |
|---|---|---|
| Isolated proxy core | Fixed private veth/source, public TCP/UDP or authorized local targets. Host scrubs packet marks and checks return ownership | B and C recovery. Dangerous targets, reserved host services and external port 53 are restricted. Network capabilities apply only inside the core namespace |
| Tailscale | Root-owned UDP socket, **both** marks equal to upstream `LinuxBypassMarkNum` (`0x80000`), physical `ens1`/`wlo1`. Dynamic peer/STUN targets and ports | B allows hole punching, C blocks it. Normal system builds check the [source contract](../system/config/network/tailscale/transport.nix). This is not authentication against root |
| Application Unbound (983) | Configured literal IPv4/IPv6 upstreams, TCP 853 | A only. The kernel cuts traffic and loopback replies from this resolver when B takes over |
| Infrastructure Unbound (979) | Same configured DoT upstreams, TCP 853 | A and Rules B for source downloads and restricted socket backends. Set `unboundDotInProxy=false` to revoke the Rules exception |
| Chrony (61) | Physical uplinks, UDP 123 and TCP 4460 | A/B. C blocks. Default permits changing addresses. Optional `timeServerCidrs` bounds original targets before TUN/NAT in A/B. Include NTS-KE and negotiated NTP servers |
| dnsmasq (985) split DNS | Installed, expiring interface + resolver-address leases, TCP/UDP 53 | Earlier DNS guard enforces the lease. Restricted B retains it, C blocks external queries |
| Managed DNS continuation | Ordinary physical-uplink TCP/UDP 53, except MagicDNS | Continues into the managed DNS redirect, not an arbitrary direct DNS grant. Earlier lease/internal-port guards still apply |
| MagicDNS actors | Root, dnsmasq and systemd-resolved (153), `tailscale0`, `100.100.100.100`, TCP/UDP 53 | Restricted B retains these actors. Resolver routing uses the authenticated tailnet suffix. C blocks external queries |
| DHCP clients | Root or dhcpcd (986), physical uplinks, UDP 68→67 / 546→547 | A/B/C link maintenance. No generic physical-uplink server 67→68 grant |
| Guest gateway replies | Configured guest bridge, gateway source, guest subnet. DHCP 67→68 and DNS source port 53 | Narrow A/B/C infrastructure, independent of UID because accepted TCP sockets can carry the daemon's dropped UID |
| IPv6 maintenance | Physical uplinks, specified NDP types with hop limit 255 | A/B/C. Restricted B also permits link-local MLD reports/done with hop limit 1 |
| Private / multicast / broadcast | Private IPv4, CGNAT, link-local, ULA and multicast scopes | Compatibility B retains existing access. Restricted B requires grants below. A direct, C blocks ordinary traffic |
| Sunshine media | Desktop UID, `tailscale0`, configured streaming UDP **source** ports | Restricted B retains negotiated media and input-authorized established replies. C cuts both. Administration UI stays local |

Loopback remains local IPC.
C additionally keeps the bounded recovery/self-test paths in [Network architecture](network.md).
Ordinary clients cannot escape C through loopback proxy listeners.

With `localDnssec=true`, private validators query only loopback transport listeners inside the core namespace and have no external DNS transport permission.
For foreign application queries, rules mode uses authenticated Cloudflare or Google DoH through the separate `lf-dns` selector. Runtime `proxy-only` carries both application and bootstrap DoH through its fixed leaf.
The core's existing recovery transport remains available in C.
`unboundDotInProxy` applies only to the Rules exception for infrastructure Unbound. C blocks ordinary host DoT, with a separately leased resolver for registered-source recovery. Root diagnostics are trusted.

DNS transport is independently controlled by `my.proxy.dnsEncryption`.
The default permits leased Mode-A plaintext recovery.
`"strict"` removes that permission and blocks the core's external port 53 except configured tailnet MagicDNS.
Approved split DNS remains leased, and native MagicDNS keeps its conditional route.
See [DNS](dns.md#optional-strict-encryption).

## Optional restricted B policy

The default `my.hardening.directEgress.mode="compatibility"` preserves LAN access.

To constrain native host sockets, select `"restricted"` and grant necessary services:

```nix
my.hardening.directEgress = {
  mode = "restricted";
  unboundDotInProxy = false;
  exceptions = [
    {
      interface = "ens1";
      cidr = "192.168.1.20/32";
      uids = [ 1000 ];
      tcpPorts = [ 443 ];
    }
    {
      interface = "ens1";
      cidr = "224.0.0.251/32";
      uids = [ 1000 ];
      udpPorts = [ 5353 ];
    }
  ];
};
```

Entries require an exact interface, canonical destination CIDR, explicit UID list and destination ports.
Explicit `uids=[]` permits every socket owner for that entry.
Normal system builds reject host bits, public destinations and entries without ports.
Incoming service exposure stays separate.

The guard sees original targets at output `-195`, before redirection.
Restricted mode keeps private routes excluded from TUN/NAT even with legacy `allowPrivateEgress=false`, so denied sockets cannot fall through to a proxy.
Revocation cuts established host-initiated flows.
Only the **reply direction** of input-authorized established flows is retained automatically.
Legacy compatibility mode with `allowPrivateEgress=false` continues to subject private targets to proxy routing.

These grants govern native host sockets.
The core separately authorizes [local and remote internal targets](proxy.md#domain-targets) using `localTargetCidrs` and `remoteTargetGrants`.
Restricted native grants do not constrain or authorize those core sockets.
Guest forwarding and incoming authorization remain separate.

## Guest trust scopes

Routed guests use `guestNetworks` and `guestPrivateExceptions`.
Private forwarding is restricted by default for libvirt, Waydroid and an enabled hotspot.
A trusted development role permits private forwarding without changing host-service or public-mode policy.
Current grants apply before generic established accepts in both directions.
See [guest architecture](network.md#guests-and-containers).

`socketGuestUsers` applies a separate original-target guard in A/B/C to dedicated host-socket backends and their delegated UIDs.
It precedes host compatibility exceptions, TUN and NAT, so broad desktop LAN permission does not authorize a restricted backend.
Private grants use `socketGuestPrivateExceptions`.
They are additional bounds, not an exemption from a separately restricted host policy.
Port 53 has a forced filtered real-address path.

Loopback proxies and synthetic or known translation destinations cannot be granted. Reserved ports are protected on host addresses and the internal core link. An explicit exception can still authorize a remote private service on the same port.
Per-account conntrack identities cut replies after grant revocation and in C.
These identities must remain declared while revoking grants.
Ordinary loopback IPC and desktop-owned backends retain the host trust class.

## Optional time destination pins

`timeServerCidrs=[]` keeps changing NTS addresses usable under UID/uplink/port limits.
Canonical IPv4/IPv6 CIDRs restrict Chrony's original NTP/NTS-KE targets in **A and B**.
Populate them from actual provider endpoints.
Documentation addresses are not usable servers.
The CIDRs must include both the NTS-KE endpoint and the NTP server returned by key exchange.
Address changes fail closed, without automatic widening or plaintext fallback.
See [Chrony's server options](https://chrony-project.org/doc/4.8/chrony.conf.html).

Managed DNS returns real addresses for the configured NTS-KE names so Chrony can reach them on the physical uplink.
If key exchange returns another hostname, add it to `timeServerNames`. When using `timeServerCidrs`, also include that server's addresses.
Gost's TCP fallback preserves the scoped NTS-KE exception.
Valid targets use the configured route.
The direct exception additionally requires a physical uplink.
Disabling host Unbound DoT in B leaves A recovery and managed B DNS available.

## Validation and boundaries

The core is trusted to route application traffic within its host-authorized transport scope.
Its network capabilities apply only to its private namespace.
Root services administer the host firewall and remain trusted.

Phone pairing, capture and encoding depend on the deployed graphical session.
Use the [remote desktop procedure](operations.md#remote-desktop) to configure them.
Checks for changes to egress policy are listed in [maintenance](maintenance.md#validation).
