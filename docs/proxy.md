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

## Authorizing a configuration

`clash-on` records proxy intent, enters C and starts a new import session. It clears previous authorization and opens one **single-use, 300-second** window. The GUI supplies the effective configuration; the packaged launcher imports it under a shared authorization lock before starting the core. A failed import consumes the window too. Run `clash-on` again after correcting inputs.

[clash-verge-stage.py](../system/programs/clash/clash-verge-stage.py) opens desktop-owned, bounded regular inputs without following symlinks, copies YAML and geodata into a root-owned snapshot and detects concurrent input changes. It normalizes the controller location and runs the core's `-t` validation as `clash-core` without capabilities. Snapshot directories use 0750 and files 0640, readable by the core group. Authority records and validation/helper logs are root-only.

The digest covers the effective YAML, geodata bytes and absent optional assets. Canonical assets include `Country.mmdb`, `GeoIP.dat`, `GeoSite.dat` and optional `ASN.mmdb`/`BundleMRS.7z`; supported legacy country-database names are normalized. `cache.db` points to separate writable core state and is outside the digest.

Ordinary restarts require matching GUI YAML and intact snapshot hashes, then reuse the authorized databases. GUI database updates do not replace the running authorization. Import new YAML/geodata with another `clash-on`. Managed geodata downloads are disabled, including replacement attempts for missing or invalid databases. Snapshots survive helper restarts; health checks also verify their digest. `clash-off` clears boot-scoped authorization and requests direct mode.

An import authorizes the inputs selected for that session. It does not make an already compromised desktop account trustworthy. Review subscription/configuration sources before granting a new import.

## Managed profile and controller

[proxy-profile.nix](../system/config/proxy-profile.nix) is the root policy; [cvr-merge.nix](../home/config/cvr-merge.nix) generates the matching GUI Merge configuration. The final core parser enforces listeners, mark, DNS, TUN routes/exclusions and private-target settings after GUI/subscription processing. Rule providers are pinned empty and automatic geodata updates are disabled. Unconditional `MATCH,DIRECT` and `FINAL,DIRECT` rules are refused. Subscription rules and group selection otherwise retain their authorized meaning.

The core reports a `managed-policy` digest to health consumers. This policy digest describes system invariants; the separate snapshot digest binds authorized YAML and assets.

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

With `true`, the generated profile routes application destinations reaching the core through the subscription's final proxy MATCH policy. It requires a usable proxy catch-all and overrides geographic/process splitting and profile REJECT rules. It also refuses application DIRECT/COMPATIBLE TCP/UDP dials inside the core, including direct leaves behind groups and renamed adapters. Rule-routed DNS is covered; explicit bootstrap/endpoint-resolution infrastructure and `#DIRECT` MagicDNS remain exempt. Sniffing is disabled to avoid replacing a dial target with a cover SNI.

Deploy the generation and import/restart the managed core for this policy to take effect. It increases subscription traffic, including domestic traffic. `bootMode`, A's portal access, root UDP, multicast and private host paths outside the core are independent choices.

## Domain targets

[target_guard.go](../system/programs/systemd/netsec/target_guard.go) checks domain destinations after routing/sniffing and before TCP/UDP egress. It rejects mixed public/private answers, empty results and unresolved targets after one retry. Private, CGNAT, loopback, link-local, unspecified, multicast and selected reserved/transition ranges are denied, including mapped IPv4 and embedded private IPv4 under the well-known NAT64 prefix. The list is targeted, not the entire IANA special-purpose registry.

The checked address is pinned for both DIRECT and remote proxy dials, avoiding a second unchecked DNS resolution. Each new mapping in a reused UDP association is validated; subsequent packets use that mapping.

Literal IPs, configured private suffixes, authenticated MagicDNS and core infrastructure are intentional exemptions. A literal private/loopback destination sent to a remote proxy can refer to that proxy's network. This guard therefore does not provide general internal-target isolation; `proxyOnly` separately controls application direct dialing.

## Gost lifecycle

[gost-relay.nix](../system/programs/systemd/gost-relay.nix) follows the mode's atomic state. It reaps the previous process before binding, avoids `SO_REUSEPORT`, requires exactly one listener per endpoint and limits descriptors, memory and tasks. In B it relies on the supervisor's health decision and rechecks the mixed listener identity. It uses passive socket observations rather than connecting to the transparent inlet, with a self-loop detector as a backstop.

The relay also accepts legacy bare numeric IPv6 CONNECT targets with valid ports while retaining Go's HTTP framing and buffered tunnel bytes. Prefer standard `[IPv6]:port` syntax. Gost is invoked by store path, not installed on the system PATH or in the initrd. See [Patch maintenance](maintenance.md) for all source and packaging customizations.
