# Direct egress exceptions

English | [简体中文](CN/egress_CN.md) | [Documentation](README.md)

In **B** (`rules` mode), the system enforces the managed proxy path along with the explicit exceptions listed below, while subscription `Rule` entries may still pick `DIRECT`. **A** remains deliberate direct access, and **C** blocks ordinary application egress as well as decrypted tailnet input—including remote desktop and established connections—without inheriting any optional **B** grants.

Strict browser namespaces have no host IP path at all: they only reach `lf-strict` through a dedicated Unix relay. **A** and **C** refuse traffic to and from core port `7898` inside the atomic policy transaction, and **B** opens it. Before forwarding the browser's first bytes, the relay verifies the established server socket's core UID rather than trusting a listener-only check that could race. Inside the core, actual leaf checks reject `DIRECT` and private targets. This boundary applies to managed browser entries, while ordinary desktop apps and other workloads follow the ledger below.

## Proxy-only egress

The ledger below describes `rules`-mode exceptions. Runtime `proxy-only` adds a separate host boundary in front of those exceptions to lock down physical egress: the outer transport only allows the selected core node's fixed TCP/UDP tuple (or, with the WireGuard exit configured, pins the peer's UDP endpoint on the physical link and allows the relay tuple only inside the tunnel). Ordinary native applications, root services, and restricted socket identities get no independent public, LAN, multicast, Chrony, Tailscale, or inbound-reply permissions—both existing direct reply flows and new requests are cut, leaving only loopback IPC, scoped DHCP/NDP, and necessary errors for authorized outer transports.

Configured IPv4 transparent guests can still use their declared core inlet, whereas other routed guest traffic—including public IPv6 and private forwarding—is refused in strict mode. Note that this governs traffic routed by the host; same-bridge layer-two traffic remains a separate boundary. Restricted backends continue to require real-address DNS and cannot borrow explicit host proxy ports or fake-IP targets.

With the [WireGuard exit](proxy.md#wireguard-exit-with-real-ping), public IPv4 and IPv6 echo requests share the same peer as the fixed relay; fake-IP echo is refused, and ordinary application echo requires a live kernel lease. Under application-proxy transport, `proxy-only` refuses echo altogether, and UDP remains outside the continuous data-plane probe.

The entire switch passes through **C** and must consume a fresh, epoch-bound full proof before kernel authorization is renewed. Running `clash-off` restores **A**, and returning to `rules` goes through the same controlled transaction. `proxy-inspect --drops` includes the strict input and output refusal counters.

## Exception ledger

UIDs, interfaces, and ports come from [machine.nix](../system/config/machine.nix), service accounts, and [options.nix](../system/config/network/options.nix). The table below covers host `output` only; guest forwarding and host `input` are governed by separate policies.

| Actor / path | Destination and transport | Modes and limits |
|---|---|---|
| Isolated proxy core | Fixed private veth/source, public TCP/UDP or authorized local targets. Host scrubs packet marks and checks return ownership | **B** and **C** recovery. Dangerous targets, reserved host services, and external port 53 are restricted. Network capabilities apply only inside the core namespace |
| Tailscale | Root-owned UDP socket, **both** marks equal to upstream `LinuxBypassMarkNum` (`0x80000`), physical `ens1`/`wlo1`. Dynamic peer/STUN targets and ports | **B** allows hole punching, **C** blocks it. Normal system builds check the [source contract](../system/config/network/tailscale/transport.nix). This is not authentication against root |
| Application Unbound (983) | Configured literal IPv4/IPv6 upstreams, TCP 853 | **A** only. The kernel cuts traffic and loopback replies from this resolver when **B** takes over |
| Infrastructure Unbound (979) | Same configured DoT upstreams, TCP 853 | **A** and Rules **B** for source downloads and restricted socket backends. Set `unboundDotInProxy=false` to revoke the Rules exception |
| Chrony (61) | Physical uplinks, UDP 123 and TCP 4460 | **A**/**B**; **C** blocks. Default permits changing addresses. Optional `timeServerCidrs` bounds original targets before TUN/NAT in **A**/**B**—include both NTS-KE and negotiated NTP servers |
| dnsmasq (985) split DNS | Installed, expiring interface + resolver-address leases, TCP/UDP 53 | Earlier DNS guard enforces the lease. Restricted **B** retains it, **C** blocks external queries |
| Managed DNS continuation | Ordinary physical-uplink TCP/UDP 53, except MagicDNS | Continues into the managed DNS redirect, not an arbitrary direct DNS grant. Earlier lease/internal-port guards still apply |
| MagicDNS actors | Root, dnsmasq, and systemd-resolved (153), `tailscale0`, `100.100.100.100`, TCP/UDP 53 | Restricted **B** retains these actors. Resolver routing uses the authenticated tailnet suffix. **C** blocks external queries |
| DHCP clients | Root or dhcpcd (986), physical uplinks, UDP 68→67 / 546→547 | **A**/**B**/**C** link maintenance. No generic physical-uplink server 67→68 grant |
| Guest gateway replies | Configured guest bridge, gateway source, guest subnet. DHCP 67→68 and DNS source port 53 | Narrow **A**/**B**/**C** infrastructure, independent of UID because accepted TCP sockets can carry the daemon's dropped UID |
| IPv6 maintenance | Physical uplinks, specified NDP types with hop limit 255 | **A**/**B**/**C**. Restricted **B** also permits link-local MLD reports/done with hop limit 1 |
| Private / multicast / broadcast | Private IPv4, CGNAT, link-local, ULA, and multicast scopes | Compatibility **B** retains existing access. Restricted **B** requires grants below. **A** direct, **C** blocks ordinary traffic |
| Sunshine media | Desktop UID, `tailscale0`, configured streaming UDP **source** ports | Restricted **B** retains negotiated media and input-authorized established replies. **C** cuts both. Administration UI stays local |

Loopback remains open for local IPC, and **C** keeps the bounded recovery and self-test paths described in [Network architecture](network.md); ordinary clients cannot escape **C** through loopback proxy listeners.

With `localDnssec=true`, private validators query only loopback transport listeners inside the core namespace and have no external DNS transport permission. For foreign application queries, `rules` mode uses authenticated Cloudflare or Google DoH through the separate `lf-dns` selector, while runtime `proxy-only` carries both application and bootstrap DoH through its fixed leaf. The core's existing recovery transport remains available in **C**. Note that `unboundDotInProxy` applies only to the `rules` exception for infrastructure Unbound; **C** blocks ordinary host DoT and uses a separately leased resolver for registered-source recovery, while root diagnostics remain trusted.

DNS transport is controlled independently by `my.proxy.dnsEncryption`. The default allows leased Mode-A plaintext recovery, whereas `"strict"` removes that permission and blocks the core's external port 53 except for configured tailnet MagicDNS. Approved split DNS stays leased, and native MagicDNS keeps its conditional route (see [DNS](dns.md#optional-strict-encryption)).

## Optional restricted B policy

By default, `my.hardening.directEgress.mode="compatibility"` preserves LAN access. To lock down native host sockets instead, switch to `"restricted"` and grant only the services you actually need:

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

Each entry requires an exact interface, a canonical destination CIDR, an explicit UID list (`uids=[]` allows any socket owner for that entry), and destination ports. Normal system builds reject host bits, public destinations, and portless entries, and incoming service exposure remains separate.

The guard inspects original destinations at `output -195`, before redirection. Restricted mode keeps private routes out of TUN/NAT even when legacy `allowPrivateEgress=false` is set, so denied sockets cannot fall through into a proxy. Revoking a grant cuts established host-initiated flows; only the **reply direction** of input-authorized established flows stays open automatically. Legacy compatibility mode with `allowPrivateEgress=false` continues to send private targets through the proxy.

These grants only govern native host sockets. Inside the core, [local and remote internal targets](proxy.md#domain-targets) are authorized separately via `localTargetCidrs` and `remoteTargetGrants`—restricted native grants neither constrain nor authorize core sockets, and guest forwarding and incoming rules stay separate as well.

## Guest trust scopes

Routed guests use `guestNetworks` and `guestPrivateExceptions`. Private forwarding is restricted by default for libvirt, Waydroid, and an enabled hotspot; a trusted development role allows private forwarding without changing host-service or public-mode policy, and current grants apply in both directions before generic established accepts (see [guest architecture](network.md#guests-and-containers)).

`socketGuestUsers` attaches a separate original-target guard in **A**, **B**, and **C** to dedicated host-socket backends and their delegated UIDs. Because it runs ahead of host compatibility exceptions, TUN, and NAT, broad desktop LAN permissions do not carry over to a restricted backend. Private grants use `socketGuestPrivateExceptions` as additional bounds rather than an exemption from a restricted host policy, and port 53 is forced through a filtered real-address path.

Loopback proxies and synthetic or known translation destinations cannot be granted. Reserved ports stay protected on host addresses and the internal core link, though an explicit exception can still authorize a remote private service on the same port. Per-account conntrack identities cut replies after grant revocation and in **C**, so keep those identities declared while revoking grants. Ordinary loopback IPC and desktop-owned backends retain the host trust class.

## Optional time destination pins

Leaving `timeServerCidrs=[]` keeps changing NTS addresses working under the UID, uplink, and port limits. Setting canonical IPv4/IPv6 CIDRs pins Chrony's original NTP/NTS-KE targets in **A and B** before TUN/NAT—make sure to populate them with actual provider endpoints (documentation addresses will not work) and include both the NTS-KE endpoint and the NTP server returned by key exchange. Address changes fail closed without automatic widening or plaintext fallback; see [Chrony's server options](https://chrony-project.org/doc/4.8/chrony.conf.html).

Managed DNS returns real addresses for the configured NTS-KE names so Chrony can reach them on the physical uplink. If key exchange returns a different hostname, add it to `timeServerNames` (and include its addresses in `timeServerCidrs` when pinning is enabled). Gost's TCP fallback preserves the scoped NTS-KE exception; valid targets follow the configured route, and the direct exception additionally requires a physical uplink. Disabling host Unbound DoT in **B** still leaves **A** recovery and managed **B** DNS intact.

## Validation and boundaries

The core is trusted to route application traffic within its host-authorized transport scope, and its network capabilities only apply inside its private namespace. Root services administer the host firewall and remain trusted.

Phone pairing, capture, and encoding depend on the deployed graphical session—follow the [remote desktop procedure](operations.md#remote-desktop) to set them up. Checks for egress policy changes are listed in [maintenance](maintenance.md#validation).
