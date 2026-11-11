# Proxy core and permissions

English | [简体中文](CN/proxy_CN.md) | [Documentation](README.md)

## Components and identities

| Component | Default endpoint / state | Identity |
|---|---|---|
| Mihomo | Mixed `127.0.0.1:7897`, DNS `:1053`, TUN `Mihomo` | `clash-core`, UID 982; `NET_ADMIN` and `NET_RAW` |
| Clash Verge helper | On-demand `clash-verge.service` | Root with bounded launch/file/network capabilities |
| GUI controller forwarder | `/run/user/<desktop-uid>/clash-verge-rev/verge-mihomo.sock` | Desktop UID, core group, no capabilities; socket 0600 |
| Gost | HTTP `127.0.0.1:33332`, transparent `127.0.0.1:33333` and `[::1]:33333` | UID 987, no capabilities |
| dnsmasq / Unbound / dhcpcd | Local DNS / wired DHCP | UIDs 985 / 983 / 986 |

[identity.nix](../system/programs/clash/identity.nix) and [runtime.nix](../system/programs/clash/runtime.nix) define the core identity and service contract. The root launcher starts the core via `setpriv`, retaining only the two network capabilities in its permitted, effective, inheritable, ambient and bounding sets. The real controller is `/run/clash-verge-rev/core/mihomo.sock`; supervisors use it directly. A desktop-UID `socat` forwarder supplies the GUI path.

The helper retains the privileges needed for identity changes, directory ownership and child management. It accepts only the packaged, root-owned launcher as an executable. Managed logs use `/var/lib/clash-verge-service/logs`, with fixed rotation. Caller-supplied IPC paths do not drive root chmod/unlink or startup reconciliation. Systemd owns child cleanup. The helper's private state is separate from the writable core cache in `/var/lib/clash-core`.

This reduces desktop-controlled root operations. A compromised core still has host `NET_ADMIN`; it is not isolated from host networking. Desktop programs with the same UID can use allowed GUI/controller operations. The helper's upstream IPC protocol is not a substitute for these execution and file-operation constraints.

An explicitly enabled IPv4 guest TPROXY inlet binds `0.0.0.0:7896`; the ordinary mixed/DNS/controller endpoints remain local under `allow-lan=false`. The packaged listener handles this bind separately and selects IPv4 sockets. Its TCP/UDP UID and service cgroup are checked before B; guest interface/source authorization remains in the firewall. Later core auto-redirect preserves the owned guest mark instead of replacing its transparent socket. See [guest networking](network.md#guests-and-containers).

## Authorizing a configuration

`clash-on` records proxy intent, enters C and starts a new import session. It clears previous authorization and opens one **single-use, 300-second** window. The GUI supplies the effective configuration; the packaged launcher imports it under a shared authorization lock before starting the core. A failed import consumes the window too. Run `clash-on` again after correcting inputs.

[clash-verge-stage.py](../system/programs/clash/clash-verge-stage.py) opens desktop-owned, bounded regular inputs without following symlinks, copies YAML and geodata into a root-owned snapshot and detects concurrent input changes. It normalizes the controller location and runs the core's `-t` validation as `clash-core` without capabilities. Snapshot directories use 0750 and files 0640, readable by the core group. Authority records and validation/helper logs are root-only.

The digest covers the effective YAML, geodata bytes and absent optional assets. Canonical assets include `Country.mmdb`, `GeoIP.dat`, `GeoSite.dat` and optional `ASN.mmdb`/`BundleMRS.7z`; supported legacy country-database names are normalized. `cache.db` points to separate writable core state and is outside the digest.

Ordinary restarts require matching GUI YAML and intact snapshot hashes, then reuse the authorized databases. GUI database updates do not replace the running authorization. Import new YAML/geodata with another `clash-on`. Managed geodata downloads are disabled, including replacement attempts for missing or invalid databases. Snapshots survive helper restarts; health checks also verify their digest. `clash-off` clears boot-scoped authorization and requests direct mode.

An import authorizes the inputs selected for that session. It does not make an already compromised desktop account trustworthy. Review subscription/configuration sources before granting a new import.

## Managed profile and controller

[proxy-profile.nix](../system/config/proxy-profile.nix) is the root policy; [cvr-merge.nix](../home/config/cvr-merge.nix) generates the matching GUI Merge configuration. The final core parser enforces listeners, mark, DNS, TUN routes/exclusions and private-target settings after GUI/subscription processing. Rule providers are pinned empty and automatic geodata updates are disabled. Unconditional `MATCH,DIRECT` and `FINAL,DIRECT` rules are refused. Subscription rules and group selection otherwise retain their authorized meaning.

The core reports a `managed-policy` digest to health consumers. This policy digest describes system invariants; the separate snapshot digest binds authorized YAML and assets.

The root policy also supplies the shared domain-filter store path, additions and exceptions. The core checks DNS before hosts/cache/fake-IP and application targets before TCP/UDP dialing, so delegated HTTP/SOCKS resolution uses the same list as system DNS. Internal bootstrap resolver queries use the filter too; their transport and target-validation scope remain separate. See [DNS](dns.md) for split DNS, native MagicDNS and literal-IP boundaries.

| Controller operation | Managed behavior |
|---|---|
| GET / HEAD / OPTIONS | Allowed, including GUI delay tests |
| PUT / DELETE `/proxies/<name>` | Select or release a proxy choice |
| DELETE `/connections` or `/connections/<id>` | Close connections |
| PATCH `/configs` | Only `mode=rule` or `mode=global` (or no change) |
| Other writes | Forbidden by default |

Configuration loads, rule disabling, provider/geodata/storage changes, restart and upgrade APIs are forbidden. PATCH validates canonical upstream field names and duplicate keys before applying the same decoded schema. New controller write endpoints require an explicit policy decision when upgrading the core.

## Routing policy

```nix
my.proxy.proxyOnly = false; # Default: subscription Rule routing.
# my.proxy.proxyOnly = true; # Use the subscription's final proxy catch-all.
```

The default preserves domestic DIRECT and prepends known probe/download domain overrides. An unlisted IP-echo resource can still match DIRECT and see the actual exit address. Health confirms its configured sample paths, not every domain.

With `true`, the generated profile routes application destinations reaching the core through the subscription's final proxy MATCH policy. It requires a usable proxy catch-all and overrides geographic/process splitting and profile REJECT rules. It also refuses application DIRECT/COMPATIBLE TCP/UDP dials inside the core, including direct leaves behind groups and renamed adapters. Rule-routed DNS is covered; explicit bootstrap/endpoint-resolution infrastructure and `#DIRECT` MagicDNS remain exempt.

Deploy the generation and import/restart the managed core for this policy to take effect. It increases subscription traffic, including domestic traffic. `bootMode`, A's portal access, root UDP, multicast and private host paths outside the core are independent choices.

`proxy-control-vm` retains default Rule coverage; `proxy-uniform-vm` exercises the production helper/core with `proxyOnly=true`, including fresh TCP/UDP DIRECT refusal and real dual-stack paths. UDP selects a leaf per association, so a DIRECT-refusal test starts a new association; a reused remote leaf remains a proxy path. Browser checks also include both routing policies. See the [component upgrade workflow](maintenance.md#component-review-gate) before changing upstream sources or patches.

## Domain targets

Managed sniffing is disabled in both Rule and uniform proxy modes. Explicit target names and fake-IP mappings remain the routing identity; an ECH cover SNI cannot replace the dial target. Literal-IP traffic uses IP rules. The parser also normalizes cached GUI profiles to this policy. See [browser ECH discovery](browser.md#ech-discovery-and-proxy-compatibility).

[target_guard.go](../system/programs/systemd/netsec/target_guard.go) validates application targets before TCP/UDP egress. Public names must resolve locally to nonempty, entirely public answers; one resolver error gets one retry. The chosen IP is pinned for DIRECT and remote proxy dials, so the node never performs a second target-name lookup. Internal suffixes in `privateTargetDomains` and the authenticated MagicDNS suffix permit private answers, but still require local resolution, pinning and egress authorization. They do not create a resolver route or bypass the shared domain filter.

### Local and remote authorization

Authorization runs at the actual selected leaf, including nested, fallback and load-balancing groups. A name such as `DIRECT` does not confer direct authority on a remote adapter. Rule and Global routing use the same checks; `proxyOnly` still independently refuses application DIRECT.

| Target reaching the core | Local DIRECT | Remote proxy |
|---|---|---|
| Public literal or checked public name | Allowed unless `proxyOnly` refuses DIRECT | Allowed |
| Private/CGNAT/loopback/ULA/link-local literal, or private answer for an approved internal suffix | Must match `localTargetCidrs` and a host inlet | Requires a matching `remoteTargetGrants` entry |
| Listed metadata endpoint, unspecified, multicast, selected reserved/transition range or raw fake-IP | Refused | Refused, including with a grant |
| Known NAT64 translation | Embedded IPv4 must pass public-address checks | Same; a private embedded address cannot be granted |

`localTargetCidrs` defaults to the shared local/private ranges. Host inlets are core metadata types `HTTP`, `HTTPS`, `SOCKS4`, `SOCKS5`, `REDIR` and `TUN`; guest `TPROXY` has no default local grant. Setting `localTargetCidrs=[]` removes this core permission. It does not alter native host firewall grants, guest forwarding or A's direct Gost passthrough.

Remote grants default to empty. An intentional service in a node's private network needs an exact leaf name, canonical internal CIDR, core inlet, transport and destination ports:

```nix
my.proxy.remoteTargetGrants = [{
  proxy = "Office node";
  cidr = "10.20.30.40/32";
  inlets = [ "SOCKS5" ];
  network = "tcp";
  ports = [ 443 ];
}];
```

The grant authorizes the IP as interpreted by that node. It grants no remote DNS lookup and no other node, inlet, transport or port. For a name, also declare its internal suffix and provide an answer through authorized hosts or the core's resolver. A grant matches the configured leaf name; it does not authenticate a remote server. Review authorized profile/provider sources and avoid reusing a granted name for another node. Inlets describe what the core actually receives: Gost's forwarded CONNECT is `HTTPS`, including traffic relayed from its transparent inlet. They do not authenticate an application or preserve its original UID.

Each new destination in a reused UDP association is validated and pinned against the association's actual leaf. Cached mappings retain that provenance and recheck the current inlet, destination port and egress grants on every packet; changing ports cannot reuse a narrower grant. Established TCP connections are not retroactively inspected. Rebuild and deliberately import/restart the core when changing policy.

### Address classes and modes

IPv4-mapped addresses are normalized before authorization. The non-grantable metadata list covers `169.254.169.254`, `169.254.170.2`, `100.100.100.200` and `fd00:ec2::254`. Scoped IPv6 literals with a zone are refused. The reserved/transition denylist is targeted, not the whole IANA special-purpose registry or every provider-specific metadata service.

The well-known `64:ff9b::/96` is always recognized. `nat64Prefixes` declares additional [RFC 6052](https://www.rfc-editor.org/rfc/rfc6052.html) translation prefixes of length 32, 40, 48, 56, 64 or 96; overlapping declarations use the most specific prefix. Public embedded IPv4 remains usable, while private, metadata and synthetic embedded targets fail closed. The local-use `64:ff9b:1::/48` is refused unless explicitly declared as a translator. Unknown translation/gateway behavior cannot be inferred from an address alone.

The configured fake-IP pool is a routing identity, never an egress destination. A live mapping must first recover its domain, which then passes normal filtering, resolution and authorization. Missing mappings and real DNS answers containing synthetic targets are refused, including with a customized pool.

These checks govern applications reaching the core, normally in B. A retains literal HTTP portal access through direct Gost/native sockets under A's firewall policy. C blocks ordinary application access before the core; local/remote grants do not reopen it. Explicit internal bootstrap, proxy-endpoint transport and `#DIRECT` MagicDNS retain their trusted infrastructure scope; rule-routed DNS does not inherit that exemption. Native LAN access and guest forwarding have separate policies. Root, the `NET_ADMIN` core and authorized infrastructure remain trusted, so this is not universal internal-network isolation.

## Gost lifecycle

[gost-relay.nix](../system/programs/systemd/gost-relay.nix) follows the mode's atomic state. It reaps the previous process before binding, avoids `SO_REUSEPORT`, requires exactly one listener per endpoint and limits descriptors, memory and tasks. In B it relies on the supervisor's health decision and rechecks the mixed listener identity. It uses passive socket observations rather than connecting to the transparent inlet, with a self-loop detector as a backstop.

The relay also accepts legacy bare numeric IPv6 CONNECT targets with valid ports while retaining Go's HTTP framing and buffered tunnel bytes. Prefer standard `[IPv6]:port` syntax. Gost is invoked by store path, not installed on the system PATH or in the initrd. See [Patch maintenance](maintenance.md) for all source and packaging customizations.
