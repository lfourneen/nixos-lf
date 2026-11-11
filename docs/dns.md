# DNS

English | [简体中文](CN/dns_CN.md) | [Documentation](README.md)

## Resolver chain

```text
Applications -> systemd-resolved -> dnsmasq 127.0.0.1:1054
                                      | local suffix blocklist / exceptions
                                      +-> B: Mihomo 127.0.0.1:1053 (fake-IP)
                                      +-> A/C: Unbound 127.0.0.1:1055 (DoT)
                                      +-> approved split DNS / leased A fallback
```

[network.nix](../system/config/network.nix) owns the resolver services. [dns-upstream.nix](../system/programs/systemd/dns-upstream.nix) reconciles dnsmasq's `/run/dns-upstream/servers.conf` and applies changes by SIGHUP. Failed applies stay pending. Only root diagnostics and the dnsmasq UID may query internal ports 1053/1055; 1054 is client-facing. TCP/UDP listener address, UID and cgroup are checked by the shared [listeners.py](../system/programs/systemd/netsec/listeners.py).

Plain DNS on uplinks, including hardcoded private/portal resolvers, is redirected into dnsmasq for both IP families. Its own upstream queries and approved MagicDNS traffic follow explicit policy. An early port-853 guard permits root, the managed core UID plus mark, and the configured Unbound path; it blocks independent application DoT/DoQ before auto-redirect can conceal the original port. DoH shares HTTPS port 443 and is not distinguished by this rule.

## Responsibilities

| Path | Domain filtering | Transport and validation |
|---|---|---|
| System queries, including Gost's A passthrough | dnsmasq suffix rules before the selected upstream | A uses Unbound DoT and local DNSSEC validation; default recovery can use leased plaintext; B uses the core |
| Core DNS listener and TUN DNS hijack | Shared suffix rules before hosts, cache and fake-IP allocation | Managed DoH/DoT; B does not run A's local DNSSEC validator |
| HTTP/SOCKS proxy target in B | Shared suffix rules before application TCP/UDP dial, including private-domain exceptions | Real-IP validation and pinning for public and approved internal names; the actual leaf must authorize private targets |
| Node / encrypted-resolver bootstrap | Core resolver queries use the shared filter before cache/upstream access | Separate, literal-address encrypted bootstrap resolvers avoid depending on an already connected proxy; internal infrastructure is exempt from application target/IP and `proxyOnly` checks |
| Approved split DNS / native MagicDNS | Split forwarding follows dnsmasq's domain precedence; resolved's native MagicDNS route bypasses dnsmasq | Explicit private resolver / Tailscale overlay exception; no local public DNSSEC guarantee |

The blocklist, DNSSEC authenticity, private-address validation and transport encryption answer different questions. A local NXDOMAIN from the filter is a policy decision, not a DNSSEC-authenticated denial. B's encrypted resolvers do not establish local DNSSEC validation. Literal IP requests, independent application DoH, remote-side lookups outside the managed core and native MagicDNS are outside the common suffix filter's scope.

## Mode A: validation and recovery

Unbound uses 360 DoT endpoints `101.226.4.6`, `218.30.118.6` and `123.125.81.6`, authenticated as `dot.360.cn`. Local `validator iterator` validates DNSSEC and ignores client CD requests. The locked `dns-root-data` seeds a persistent RFC 5011 anchor in `/var/lib/unbound`; startup does not fetch an unauthenticated initial anchor. Unsigned zones remain unsigned.

Upstream private/CGNAT, guest, loopback, unspecified, link-local and ULA answers are filtered, including IPv4-mapped forms. Explicit local data and narrowly declared private domains remain usable. Validated cached data can be served for up to 900 seconds during upstream failure, with a reply TTL of 30 seconds. Split DNS and plaintext fallback bypass this Unbound path.

[dnssec_probe.py](../system/programs/systemd/netsec/dnssec_probe.py) reports local validation and encrypted transport separately. Each health round queries a fresh root name locally **and** opens a new authenticated TLS/DNS exchange with the configured upstreams. Positive, expired and aggressive NSEC caches can answer locally, including authenticated denials for names never queried before; an AD bit alone does not establish connectivity. Overall `secure` requires local validation plus reachable transport. A validation error remains a validation error, while a cached secure answer with failed transport reports `unavailable`. Each endpoint exchange has a five-second total deadline, including handshake and framed reads; the local query has eight seconds. The first usable endpoint ends the transport stage.

`dnssec_probe.local` and `dnssec_probe.transport` preserve both observations in `/run/dns-upstream/state.json` and `/var/lib/dns-upstream/history.log`. A manual invocation without configured transport endpoints is explicitly labelled `scope=validation-only`; the production coordinator always supplies them. Cold anchor priming is deferred only while transport works.

When the encrypted path cannot supply a usable verdict, A may open a bounded plaintext window using the original DHCP4 resolvers, or `1.1.1.1`/`1.0.0.1` when none were supplied. These are a separate recovery path, not Unbound upstreams.

This recovery behavior belongs to the default `my.proxy.dnsEncryption="opportunistic"`. A network that blocks the encrypted upstream can cause plaintext recovery within the allowance. Use the independent strict policy below when that tradeoff is unacceptable.

| Control | Default behavior |
|---|---|
| Lease | At most 120 seconds per renewal, constrained by remaining allowance |
| Session allowance | 1800 seconds; plaintext spends one second per second |
| Replenishment | Consecutive successful validation-and-transport rounds in the same coordinator/policy/session earn at most one second per second; recovery does not reset the pool |
| Session identity | Boot ID + interface index + Wi-Fi ActiveConnection where applicable |
| Carrier / DHCP changes | Do not mint a fresh allowance; service restarts retain accounting |
| Optional boot deadline | `my.proxy.plaintextBootBudgetSeconds`, default `null` |
| Explicit override | Root-owned `/run/dns-upstream/force-plaintext`, active until removed or rebooted |

The override can outlast the allowance, but is still billed and renewed through bounded leases. Removing it does not restore spent time. In C, earlier output policy cuts external DNS regardless of fallback selection.

[dns_budget.py](../system/programs/systemd/netsec/dns_budget.py) maintains the session ledger under `/run/dns-upstream/budgets/`. A lease requires an atomic record of the balance, settlement time and possible lease deadline. Charges equal the overlap between the unsettled time interval and the lease, including intervals spanning expiry or a coordinator restart. A failed or uncertain policy application retains the recorded deadline until revocation is confirmed. Invalid ledgers prevent renewal.

Replenishment requires two successful rounds within 90 seconds in the same coordinator, policy and network-session epoch. Restart, failed reconciliation, unobserved gaps and suspend do not earn time; boot-time and awake-time deltas distinguish sleep. An optional boot deadline also bounds credit. `/run` makes the allowance boot-scoped; persistent history is diagnostic, not the budget ledger.

[dns_policy.py](../system/programs/systemd/netsec/dns_policy.py) is the privileged kernel-lease broker. Timed nft sets permit only the dnsmasq UID and exact upstream destinations. A stalled or dead coordinator cannot keep a lease past expiry. Sleep and connection changes revoke permissions before renewal.

## Optional strict encryption

```nix
my.proxy.dnsEncryption = "strict";
```

This changes managed **public DNS transport**, independently of A/B/C, `bootMode`, browser privacy and `proxyOnly`. A keeps authenticated DoT; B keeps its managed encrypted resolver and node-bootstrap endpoints. The coordinator never opens a plaintext recovery window and ignores `force-plaintext`, the broker refuses plaintext grants, and the firewall omits the accept rule for the fallback set. An old budget, override or leftover set element cannot reopen that path. The early DNS guard also blocks the core's external TCP/UDP port 53, except its configured MagicDNS path over the tailnet interface.

Approved private split DNS and MagicDNS remain intentional exceptions. Loopback DNS is local IPC. This setting does not encrypt every application protocol, police arbitrary HTTPS/DoH payloads, authenticate unsigned zones or constrain privileged root diagnostics. [Mihomo's resolver roles](https://wiki.metacubex.one/config/dns/) distinguish application DNS from proxy-endpoint and resolver bootstrap.

When encrypted DNS fails, fresh public names fail; local and eligible cached answers may still work. State reports `encryption_policy=strict`, `degraded-dot-only`, a zero window and no active plaintext override. Readiness remains unavailable until the encrypted path recovers. Use a different uplink, a literal HTTP portal address in A, or repair the declared authenticated upstreams. If a portal requires unavailable public DNS, deliberately select a prepared generation using `opportunistic`; neither `clash-off` nor a runtime override relaxes strict encryption. See [offline recovery](operations.md#offline-recovery).

## Mode B: managed Mihomo DNS

[proxy-profile.nix](../system/config/proxy-profile.nix) defines the managed DNS values. TUN mode uses fake-IP (`198.18.0.1/16` by default), a fixed blacklist-style fake-IP filter and DNS hijack. Local suffixes and selected connectivity/STUN resources receive real answers. Filtering fake-IP changes resolution, not routing. HTTP-only mode (`tunMode=false`) uses redir-host.

Public name servers use Cloudflare/Google DoH; domestic names and proxy-endpoint bootstrap use the configured domestic resolvers. `respect-rules` is enabled, additional direct resolvers and proxy-server resolver-policy maps are disabled, and the core's parser/controller policy prevents runtime weakening of managed fields. B's resolver is separate from A's local Unbound validator.

The core validates and pins domain dial targets after routing, including approved internal suffixes. Literal and resolved private targets then need local DIRECT or scoped remote-leaf authorization. New UDP mappings are validated; cached mappings recheck egress and port grants without another DNS lookup. The [target authorization policy](proxy.md#domain-targets) is separate from DNS answer filtering; a DNS or suffix exception grants no remote internal access.

In managed fake-IP mode, address-record behavior stays synthetic while HTTPS/SVCB queries use the configured resolver and retain their service parameters. The common filter runs before both. Firefox's use of these records, its CONNECT limitation and the optional native browser path are explained in [the browser guide](browser.md#ech-discovery-and-proxy-compatibility).

## Restricted socket guest DNS

When `my.hardening.socketGuestUsers` is nonempty, [socket-guest-dns.nix](../system/config/socket-guest-dns.nix) starts an additional loopback dnsmasq on port 1056 as UID 985. It shares the immutable suffix filter, additions and exceptions, but always forwards to Unbound 1055 and returns real addresses. It has no plaintext lease, split-DNS or native MagicDNS path. Unavailable DoT, including `unboundDotInProxy=false` in B, makes fresh public names fail; eligible cached/local data may remain.

The declared primary and subordinate UIDs have TCP/UDP port 53 redirected here before the core's auto-redirect. A post-NAT check rejects old conntrack associations that still point at a previous resolver, and direct calls to 1056 remain reserved for root. The private-destination guard refuses direct fake-IP/known translation targets and host proxy inlets. Independent HTTPS/DoH payloads remain outside suffix filtering, while their resulting literal socket targets still face the account policy. C cuts these accounts' tracked replies as well as public egress. This path belongs to [host-socket guest authorization](network.md#guests-and-containers); the normal desktop resolver and bridge gateway DNS retain their separate paths.

## Blocklist

[dns-blocklist.nix](../system/config/dns-blocklist.nix) decompresses the tracked [snapshot](../local-data/dns-blocklist.txt.xz) once at build time. The same immutable bare-domain file supplies dnsmasq's suffix NXDOMAIN rules and the core's in-memory filter. Nothing is downloaded during a build or at runtime.

`my.proxy.blockedDomains` adds host-specific names. `blockedDomainExceptions` renders `server=/domain/#` and the matching core exception. Both compare suffixes case-insensitively at label boundaries: the most specific entry wins, and an exception wins at equal specificity. A narrower block can therefore block part of an excepted subtree again. An exception permits resolution; it does not disable routing or the private-IP guard. Explicit split-server entries also participate in [dnsmasq's domain precedence](https://dnsmasq.org/docs/dnsmasq-man.html), so an approved private namespace can supersede a broader block.

The root managed policy owns the core's file path, additions and exceptions; cached GUI profiles inherit them during parsing, and controller writes cannot replace them. Missing or invalid list data rejects a new configuration while preserving the active filter. Application dial checks run before internal-suffix permissions; those permissions cannot bypass a blocked name. Internal resolver queries share the filter, while explicit hosts/IP infrastructure mappings retain their separate role. A listed proxy-endpoint name can also lose DNS resolution; declare a narrow `blockedDomainExceptions` entry when that endpoint is intentionally trusted.

Refresh upstream data with [refresh-dns-blocklist.py](../scripts/refresh-dns-blocklist.py); `--check` reports drift without writing. Rebuild and restart the resolver/core generation to apply new data, and restart clients that retain DNS or connections. Existing connections are not retroactively inspected. Upstream sources and snapshot metadata are recorded in the module header and [local-data guide](../local-data/README.md).

## Wi-Fi, split DNS and MagicDNS

[nm_dns.py](../system/programs/systemd/netsec/nm_dns.py) disables automatically supplied DNS for both families on managed Wi-Fi, clears unwanted explicit servers/search domains and pins resolved to the local gateway with `~.`. Fallback discovery reads raw DHCP information rather than this rewritten effective DNS list. Wired dhcpcd remains separate.

`my.proxy.splitDnsProfileUuids` is empty by default. Approved profiles can supply private/ULA conditional resolvers already excluded from TUN by the shared [direct-egress routing policy](egress.md), including restricted B. Dnsmasq binds queries to the managed Wi-Fi interface. Leases bind UUID, ActiveConnection, ifindex, carrier-down counter and revocation epoch, expire within 120 seconds of observation and are revoked on dispatcher transitions. Public upstreams and unreadable/unsupported profiles enter `split-error` rather than opening default forwarding. A local resolver identity failure enters `resolver-error` and cannot open plaintext fallback.

[resolved_policy.py](../system/programs/systemd/netsec/resolved_policy.py) excludes the managed TUN from resolved's default selection. Tailscale automatic DNS/routes remain disabled; only the authenticated tailnet's current MagicDNS suffix routes to its resolver with `DefaultRoute=no`. Literal peer addresses and this verified suffix retain intentional private access. External LAN/tailnet DNS service requires its own listener, client ACL and answer-routing policy; the host gateway is not a wildcard resolver.
