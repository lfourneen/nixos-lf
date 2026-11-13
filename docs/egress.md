# Direct egress exceptions

English | [简体中文](CN/egress_CN.md) | [Documentation](README.md)

B enforces a managed path plus the exceptions below; subscription Rule routing may still choose DIRECT. A remains deliberate direct access. C blocks ordinary application egress and decrypted tailnet input, including remote desktop and established connections, and never inherits B's optional grants.

Strict browser namespaces have no host IP path and reach only `lf-strict` through a dedicated Unix relay. A/C refuse traffic to/from core port `7898` in the atomic policy transaction; B opens it. The relay checks the established server socket’s core UID before forwarding the first browser bytes, avoiding a listener-only race. Actual leaf checks reject DIRECT and private targets. This applies to managed browser entries; ordinary desktop and other applications retain the ledger below.

## Exception ledger

UIDs, interfaces and ports come from [machine.nix](../system/config/machine.nix), service accounts and [options.nix](../system/config/network/options.nix). This table describes host output; guest forwarding and host input have separate policies.

| Actor / path | Destination and transport | Modes and limits |
|---|---|---|
| Proxy core | Socket owner root or `clash-core` (982), **both** socket `SO_MARK` and packet mark 6666; destinations selected by the core | B and C recovery. UID alone or a packet-only mark gives no exception. Root and the core's `NET_ADMIN` remain trusted |
| Tailscale | Root-owned UDP socket, **both** marks equal to upstream `LinuxBypassMarkNum` (`0x80000`), physical `ens1`/`wlo1`; dynamic peer/STUN targets and ports | B allows hole punching, C blocks it. Normal system builds check the [source contract](../system/config/network/tailscale/transport.nix); this is not authentication against root |
| Unbound (983) | Configured literal IPv4/IPv6 upstreams, TCP 853 | A/B target guard; B can disable this exception with `unboundDotInProxy=false`. C blocks external DoT. Other ordinary UIDs cannot use TCP/UDP 853; root diagnostics remain trusted. Port 443 DoH is separate |
| Chrony (61) | Physical uplinks, UDP 123 and TCP 4460 | A/B; C blocks. Default permits changing addresses. Optional `timeServerCidrs` bounds original targets before TUN/NAT in A/B; include NTS-KE and negotiated NTP servers |
| dnsmasq (985) split DNS | Installed, expiring interface + resolver-address leases, TCP/UDP 53 | Earlier DNS guard enforces the lease; restricted B retains it, C blocks external queries |
| Managed DNS continuation | Ordinary physical-uplink TCP/UDP 53, except MagicDNS | Continues into the managed DNS redirect, not an arbitrary direct DNS grant. Earlier lease/internal-port guards still apply |
| MagicDNS actors | Root, dnsmasq and systemd-resolved (153), `tailscale0`, `100.100.100.100`, TCP/UDP 53 | Restricted B retains these actors; resolver routing uses the authenticated tailnet suffix. C blocks external queries |
| DHCP clients | Root or dhcpcd (986), physical uplinks, UDP 68→67 / 546→547 | A/B/C link maintenance; no generic physical-uplink server 67→68 grant |
| Guest gateway replies | Configured guest bridge, gateway source, guest subnet; DHCP 67→68 and DNS source port 53 | Narrow A/B/C infrastructure, independent of UID because accepted TCP sockets can carry the daemon's dropped UID |
| IPv6 maintenance | Physical uplinks, specified NDP types with hop limit 255 | A/B/C. Restricted B also permits link-local MLD reports/done with hop limit 1 |
| Private / multicast / broadcast | Private IPv4, CGNAT, link-local, ULA and multicast scopes | Compatibility B retains existing access; restricted B requires grants below. A direct, C blocks ordinary traffic |
| Sunshine media | Desktop UID, `tailscale0`, configured streaming UDP **source** ports | Restricted B retains negotiated media and input-authorized established replies. C cuts both; administration UI stays local |

Loopback remains local IPC. C additionally keeps the bounded recovery/self-test paths in [Network architecture](network.md); ordinary clients cannot escape C through loopback proxy listeners.

DNS transport is independently controlled by `my.proxy.dnsEncryption`. The default permits leased Mode-A plaintext recovery; `"strict"` removes that permission and blocks the core's external port 53 except configured tailnet MagicDNS. Approved split DNS remains leased, and native MagicDNS keeps its conditional route. See [DNS](dns.md#optional-strict-encryption).

## Optional restricted B policy

The default `my.hardening.directEgress.mode="compatibility"` preserves LAN access. To constrain native host sockets, select `"restricted"` and grant necessary services:

```nix
my.hardening.directEgress = {
  mode = "restricted";
  unboundDotInProxy = false;
  exceptions = [
    { interface = "ens1"; cidr = "192.168.1.20/32";
      uids = [ 1000 ]; tcpPorts = [ 443 ]; }
    { interface = "ens1"; cidr = "224.0.0.251/32";
      uids = [ 1000 ]; udpPorts = [ 5353 ]; }
  ];
};
```

Entries require an exact interface, canonical destination CIDR, explicit UID list and destination ports. Explicit `uids=[]` permits every socket owner for that entry. Normal system builds reject host bits, public destinations and entries without ports. Incoming service exposure stays separate.

The guard sees original targets at output `-195`, before redirection. Restricted mode keeps private routes excluded from TUN/NAT even with legacy `allowPrivateEgress=false`, so denied sockets cannot fall through to a proxy. Revocation cuts established host-initiated flows; only the **reply direction** of input-authorized established flows is retained automatically. Legacy compatibility mode with `allowPrivateEgress=false` continues to subject private targets to proxy routing.

These grants govern native host sockets. The core separately authorizes [local and remote internal targets](proxy.md#domain-targets) using `localTargetCidrs` and `remoteTargetGrants`; restricted native grants do not constrain or authorize those core sockets. Guest forwarding and incoming authorization remain separate.

## Guest trust scopes

Routed guests use `guestNetworks` and `guestPrivateExceptions`; private forwarding is restricted by default for libvirt, Waydroid and an enabled hotspot. A trusted development role permits private forwarding without changing host-service or public-mode policy. Current grants apply before generic established accepts in both directions. See [guest architecture](network.md#guests-and-containers).

`socketGuestUsers` applies a separate original-target guard in A/B/C to dedicated host-socket backends and their delegated UIDs. It precedes host compatibility exceptions, TUN and NAT, so broad desktop LAN permission does not authorize a restricted backend. Private grants use `socketGuestPrivateExceptions`; they are additional bounds, not an exemption from a separately restricted host policy. Port 53 has a forced filtered real-address path; loopback proxies, synthetic/known translation destinations and reserved listener ports cannot be granted. Per-account conntrack identities cut replies after grant revocation and in C. These identities must remain declared while revoking grants. Ordinary loopback IPC and desktop-owned backends retain the host trust class.

## Optional time destination pins

`timeServerCidrs=[]` keeps changing NTS addresses usable under UID/uplink/port limits. Canonical IPv4/IPv6 CIDRs restrict Chrony's original NTP/NTS-KE targets in **A and B**. Populate them from actual provider endpoints; documentation addresses are not usable servers. The CIDRs must include both the NTS-KE endpoint and the NTP server returned by key exchange. Address changes fail closed, without automatic widening or plaintext fallback. See [Chrony's server options](https://chrony-project.org/doc/4.8/chrony.conf.html).

With pins, managed DNS excludes configured NTS-KE names from fake-IP so checks see real addresses. If KE negotiates another hostname, declare it in `timeServerNames` as well as pinning its address CIDRs; extra names never grant extra addresses. Gost's TCP fallback preserves the scoped NTS-KE exception. Valid targets use the configured route; the direct exception additionally requires a physical uplink. Disabling B Unbound DoT leaves A recovery and managed B DNS available.

## Validation and boundaries

`proxy-egress-config` validates scopes and is required by normal system builds. `proxy-rules` / `proxy-egress-rules` parse complete compatibility/restricted rules in LKL. Profile tests compare root/GUI route exclusions and DNS filters. `proxy-tailscale-vm` / `proxy-egress-vm` cover both policies, dual-stack actual Tailscale STUN, UID/socket/packet identity, DNS/DoT/NTS, multicast, interface/port denials, grant revocation and C. `proxy-control-vm` / `proxy-egress-control-vm` use the production helper and actual TUN core; `proxy-network-vm` covers broader integration. See [maintenance checks](maintenance.md#validation).

Socket marks plus UID reduce accidental or unprivileged bypass. They do not isolate malicious root or the `NET_ADMIN` core. LKL supports `socket mark`, not `socket cgroupv2`; unsupported service matching never silently restores blanket root UDP. Phone pairing, hardware capture/encoding and internet hole punching are checked in the deployed graphical session using the [remote desktop procedure](operations.md#remote-desktop).
