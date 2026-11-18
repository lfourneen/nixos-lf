# DNS

English | [简体中文](CN/dns_CN.md) | [Documentation](README.md)

The host selects `dnsEncryption="strict"` and `localDnssec=true` in `host.nix`: A validates DNSSEC locally through Unbound and its 360 DoT endpoints; B validates through the core validators, resolving `geosite:cn` through 360 DoH over a direct connection and other names through Cloudflare/Google DoH behind the remote proxy.
The module default for `localDnssec` is `false`.
Opportunistic leases and recovery budgets described below remain module compatibility behavior.
Strict grants no public plaintext recovery or runtime force-plaintext override.
DoT failure leaves cached/local answers.
A literal-IP portal uses its separate entry.
An older recovery generation requires deliberate selection rather than automatic downgrade.

## Resolver chain

```text
Applications -> systemd-resolved -> dnsmasq 127.0.0.1:1054
                                      | local suffix blocklist / exceptions
                                      +-> B: Mihomo 127.0.0.1:1053 (fake-IP)
                                      +-> A/C: Unbound 127.0.0.1:1055 (DoT)
                                      +-> approved split DNS / leased A fallback
```

[default.nix](../system/config/network/network.nix) owns the resolver services.
[upstream.nix](../system/config/network/dns/upstream.nix) reconciles dnsmasq's `/run/dns-upstream/servers.conf` and applies changes by SIGHUP.
Failed applies stay pending.
Only root diagnostics and the dnsmasq UID may query internal ports 1053/1055. 1054 is client-facing.
In runtime proxy-only, the outer boundary blocks host DoT and split/MagicDNS paths, including while C is active; the real-address gateway below retains the core validator path.
TCP/UDP listener address, UID and cgroup are checked by the shared [listeners.py](../system/config/network/proxy/listeners.py).

Plain DNS on uplinks, including hardcoded private/portal resolvers, is redirected into dnsmasq for both IP families.
Its own upstream queries and approved MagicDNS traffic follow explicit policy.
An early port-853 guard permits root, the managed core UID plus mark, and the configured Unbound path.
It blocks independent application DoT/DoQ before auto-redirect can conceal the original port.
DoH shares HTTPS port 443 and is not distinguished by this rule.

## Responsibilities

| Path | Domain filtering | Transport and validation |
|---|---|---|
| System queries, including Gost's A passthrough | dnsmasq suffix rules before the selected upstream | A uses Unbound DoT and local DNSSEC validation. Default recovery can use leased plaintext. B uses the core |
| Core DNS listener and TUN DNS hijack | Shared suffix rules before hosts, cache and fake-IP allocation | With `localDnssec`, local validation precedes address synthesis. Otherwise, managed DoH/DoT |
| HTTP/SOCKS proxy target in B | Shared suffix rules before application TCP/UDP dial, including private-domain exceptions | Real-IP checks and pinning for public and approved internal names. `localDnssec` also validates public answers locally. The actual leaf must authorize private targets |
| Node / encrypted-resolver bootstrap | Core resolver queries use the shared filter before cache/upstream access | An independent validator with `localDnssec`, otherwise literal-address encrypted bootstrap resolvers. Rules retains its independent bootstrap transport. Runtime proxy-only starts from accepted pinned nodes and sends both validator roles through the remote group. Internal infrastructure is exempt from application target/IP checks |
| Approved split DNS / native MagicDNS | Split forwarding follows dnsmasq's domain precedence. Resolved's native MagicDNS route bypasses dnsmasq | Explicit private resolver / Tailscale overlay exception. No local public DNSSEC guarantee |

The suffix filter returns local NXDOMAIN for blocked domains.
A uses Unbound for local DNSSEC validation.
B uses local validators when `localDnssec` is enabled and the core's encrypted resolvers otherwise.
Private-address checks run separately on DNS answers and application targets.
Literal IP requests, independent application DoH, remote-side lookups outside the managed core and native MagicDNS are outside the common suffix filter's scope.

## Mode A: validation and recovery

### Encrypted upstreams and health

Unbound uses 360 DoT endpoints `101.226.4.6`, `218.30.118.6` and `123.125.81.6`, authenticated as `dot.360.cn`.
Local `validator iterator` validates DNSSEC and ignores client CD requests.
The locked `dns-root-data` seeds a persistent RFC 5011 anchor in `/var/lib/unbound`.
Startup does not fetch an unauthenticated initial anchor.
Unsigned zones remain unsigned.

Upstream private/CGNAT, guest, loopback, unspecified, link-local and ULA answers are filtered, including IPv4-mapped forms.
Explicit local data and narrowly declared private domains remain usable.
Validated cached data can be served for up to 900 seconds during upstream failure, with a reply TTL of 30 seconds.
Split DNS and plaintext fallback bypass this Unbound path.

[dnssec_probe.py](../system/config/network/dns/dnssec_probe.py) checks local DNSSEC validation and a fresh, certificate-authenticated TLS/DNS exchange with an upstream.
Cached validation alone cannot establish upstream availability.
`secure` requires both checks to pass.
If local validation succeeds but transport fails, the result is `unavailable`.
The local query has an eight-second limit.
Each upstream exchange shares a five-second deadline for TLS and the complete DNS response.

The two results appear as `dnssec_probe.local` and `dnssec_probe.transport` in `/run/dns-upstream/state.json` and `/var/lib/dns-upstream/history.log`.
A manual invocation without transport endpoints reports `scope=validation-only`.

### Plaintext recovery and allowance

When the encrypted path cannot supply a usable verdict, A may open a bounded plaintext window using the original DHCP4 resolvers, or `1.1.1.1`/`1.0.0.1` when none were supplied.
These are a separate recovery path, not Unbound upstreams.

This recovery behavior belongs to the default `my.proxy.dnsEncryption="opportunistic"`.
A network that blocks the encrypted upstream can cause plaintext recovery within the allowance.
Use the independent strict policy below when that tradeoff is unacceptable.

| Control | Default behavior |
|---|---|
| Lease | At most 120 seconds per renewal, constrained by remaining allowance |
| Session allowance | 1800 seconds. Plaintext spends one second per second |
| Replenishment | Consecutive successful validation-and-transport rounds in the same coordinator/policy/session earn at most one second per second. Recovery does not reset the pool |
| Session identity | Boot ID + interface index + Wi-Fi ActiveConnection where applicable |
| Carrier / DHCP changes | Do not mint a fresh allowance. Service restarts retain accounting |
| Optional boot deadline | `my.proxy.plaintextBootBudgetSeconds`, default `null` |
| Explicit override | Root-owned `/run/dns-upstream/force-plaintext`, active until removed or rebooted |

The override can outlast the allowance, but is still billed and renewed through bounded leases.
Removing it does not restore spent time.
In C, earlier output policy cuts external DNS regardless of fallback selection.

[dns_budget.py](../system/config/network/dns/dns_budget.py) stores the current boot's session allowance in `/run/dns-upstream/budgets/`.
Spending includes authorized time across lease expiry and coordinator restarts.
Invalid state prevents renewal.
Persistent history is diagnostic and does not restore the allowance.

Allowance is replenished only between successful observations at most 90 seconds apart in the same coordinator, policy and network session.
Restarts, failed application, sleep and unobserved time earn no allowance.
`plaintextBootBudgetSeconds`, when set, also limits replenishment.

[dns_policy.py](../system/config/network/dns/dns_policy.py) is the privileged kernel-lease broker.
Timed nft sets permit only the dnsmasq UID and exact upstream destinations.
A stalled or dead coordinator cannot keep a lease past expiry.
Sleep and connection changes revoke permissions before renewal.

### Upstream configuration and time

`my.machine.dotEndpoints` declares literal IPv4/IPv6 endpoints and their certificate `authName`.
Host Unbound, the Mode-A probe and host firewall use the same list, derived by default from `dotUpstreams` and `dotAuthName`.
Choose endpoints reachable from the intended networks.

`my.proxy.dnsStaleWaitMilliseconds` sets how long to wait for fresh data before returning eligible stale cache entries, defaulting to 300 ms.
Core DNS IPv6 follows the host's IPv6 support.

DoT and NTS require an accurate clock.
With an incorrect RTC, verify time through an independent trusted source, set the clock locally and restart the encrypted services.
Certificate time checks remain enabled.
Plaintext DNS policy does not repair time authentication.

## Optional strict encryption

```nix
my.proxy.dnsEncryption = "strict";
```

This changes managed **public DNS transport**, independently of A/B/C, `bootMode`, browser privacy and `proxyOnly`.
A keeps authenticated DoT.
B keeps its managed encrypted resolver and node-bootstrap endpoints.
The coordinator never opens a plaintext recovery window and ignores `force-plaintext`, the broker refuses plaintext grants, and the firewall omits the accept rule for the fallback set.
An old budget, override or leftover set element cannot reopen that path.
The early DNS guard also blocks the core's external TCP/UDP port 53, except its configured MagicDNS path over the tailnet interface.

Approved private split DNS and MagicDNS remain intentional exceptions.
Loopback DNS is local IPC.
This setting does not encrypt every application protocol, police arbitrary HTTPS/DoH payloads, authenticate unsigned zones or constrain privileged root diagnostics.
[Mihomo's resolver roles](https://wiki.metacubex.one/config/dns/) distinguish application DNS from proxy-endpoint and resolver bootstrap.

When encrypted DNS fails, fresh public names fail.
Local and eligible cached answers remain available.
State reports `encryption_policy=strict`, `degraded-dot-only`, a zero window and no active plaintext override.
Readiness remains unavailable until the encrypted path recovers.
Use a different uplink, a literal HTTP portal address in A, or repair the declared authenticated upstreams.
If a portal requires unavailable public DNS, deliberately select a prepared generation using `opportunistic`.
Neither `clash-off` nor a runtime override relaxes strict encryption.
See [offline recovery](operations.md#offline-recovery).

## Mode B: managed Mihomo DNS

[profile.nix](../system/config/network/proxy/profile.nix) defines the managed DNS values.
TUN mode uses fake-IP (`198.18.0.1/16` by default), a fixed blacklist-style fake-IP filter and DNS hijack.
Local suffixes and selected connectivity/STUN resources receive real answers.
Filtering fake-IP changes resolution, not routing.
HTTP-only mode (`tunMode=false`) uses redir-host.

With `localDnssec=true`, application DNS goes to a private Unbound validator at `127.0.0.1:1055`, while node and resolver bootstrap use a separate validator at `127.0.0.1:1056`.
Both live inside the core namespace, independently of the host services using the same port numbers.
They maintain separate persistent RFC 5011 anchors seeded from locked `dns-root-data` and ignore client CD requests.
Each validator forwards over TCP loopback to a private Mihomo transport listener: application queries use port 1057 and bootstrap queries use 1058.
These listeners carry raw DNSSEC material without hosts substitution, fake-IP synthesis or reuse of ordinary resolver cache entries.

In `rules`, application transport keeps a domestic split: `geosite:cn` uses 360 DoH over a direct connection, and other names use Cloudflare/Google DoH.
With controlled sources, the foreign queries bind to the `lf-remote` group and travel through a remote proxy.
Configurations without controlled sources retain `respect-rules` selection.
Node bootstrap retains its independent encrypted proxy-server and literal-address bootstrap resolvers.
Ordinary application names are not sent to that bootstrap path.
The domestic DoH endpoints come from `my.machine.mihomoDomesticValidatingDnsUpstreams` and must return complete DNSSEC material; the default 360 endpoints do, while the non-validating AliDNS path does not.
Cold foreign lookups can require several remote exchanges for validation, while domestic lookups stay on a domestic round trip.
Mode A's `my.machine.dotEndpoints` list is independent of both B roles.
TLS certificate checks remain enabled in Mihomo, and upstream failure grants no plaintext fallback.

Runtime `proxy-only` keeps both local validators and their independent budgets, but sends both roles through authenticated literal-address DoH endpoints bound to the same fixed remote leaf as application traffic.
With the [WireGuard exit](proxy.md#wireguard-exit-with-real-ping), system DNS also uses the application validator's real-address gateway so hostname pings can reach the destination over native ICMP.
It removes domestic resolver-policy maps, direct bootstrap and MagicDNS transport.
The core starts from accepted, pinned node addresses, preserving TLS server names; switching does not require a network fetch or direct name lookup.
An unavailable node or validator keeps C active.

Host-side source downloads and restricted socket backends still need real addresses.
With runtime routing, their port 1056 dnsmasq forwards to a fixed loopback gateway on 1059.
For proxy-only intent, the gateway crosses a root-owned Unix socket to the application validator in the core namespace; it never falls back to host DoT.
Rules and A use host Unbound 1055.
Only root and dnsmasq may query 1059; the Unix bridge has no arbitrary destination option.
Host DoT, split DNS and native MagicDNS cannot cross the proxy-only outer firewall.
Switching flushes host resolver caches and recreates the core validators; managed fake-IP mappings persist so addresses cached by applications retain their domain identity.

With `localDnssec=false`, public name servers use Cloudflare/Google DoH, and domestic names and proxy-endpoint bootstrap use the configured domestic resolvers.
That compatibility path does not locally validate B's DNSSEC answers.
In both configurations, `respect-rules` is enabled, additional direct resolvers and proxy-server resolver-policy maps are disabled, and the parser/controller prevents runtime weakening of managed fields.

The core validates and pins domain dial targets after routing, including approved internal suffixes.
With local validation enabled, public dial targets use the local validator's checked answers before their real addresses are pinned.
Subscription hosts and system hosts cannot substitute an unvalidated public answer.
Literal and resolved private targets then need local DIRECT or scoped remote-leaf authorization.
New UDP mappings are validated.
Cached mappings recheck egress and port grants without another DNS lookup.
The [target authorization policy](proxy.md#domain-targets) is separate from DNS answer filtering.
A DNS or suffix exception grants no remote internal access.

Public dial-target checks share concurrent lookups of the same name and query A/AAAA in parallel within a three-second deadline.
A usable validated family does not wait for a stalled family's full timeout.
Successful results are reused for at most ten seconds and never beyond the validator's TTLs, including CNAMEs.
Zero-TTL results and failures are not cached.
Each policy owns its cache and outstanding work, so a replacement policy cannot reuse old answers or pending queries.

In managed fake-IP mode, address-record behavior stays synthetic while HTTPS/SVCB queries use the configured resolver and retain their service parameters.
The common filter runs before both.
Local validation runs before public fake-IP allocation, preserving bogus failures, signed NXDOMAIN and NODATA, including a CNAME chain without the requested address type.
Synthetic addresses clear AD because the fake mapping itself is not signed.
Unsigned answers remain unauthenticated, and explicit private-domain exceptions retain their separate scope.
Managed DNS refuses queries with more than one question.
Firefox's use of these records, its CONNECT limitation and the optional native browser path are explained in [the browser guide](browser.md#ech-discovery-and-proxy-compatibility).

### Local validation and role budgets

```nix
my.proxy.dnsEncryption = "strict";
my.proxy.localDnssec = true;
my.proxy.dnsQueryBudget = {
  application = 256;
  infrastructure = 64;
};
```

Local validation requires strict encryption and `coreNetns=true`.
The query budgets shown are the module defaults, with allowed ranges of 32–4096 and 8–1024 respectively.
Each validator has its own request list, cache, descriptor limit and memory limit, so application overload does not consume the bootstrap validator's budget.
Excess requests can be refused.
Capacity becomes available again as in-flight work finishes.
A's host validator and restricted socket guest DNS remain separate.
These controls isolate DNS roles.
Per-application conntrack, UDP-association and proxy-queue quotas are outside these budgets.

Separately, the dial-target guard holds at most 256 cached names and 256 in-flight names.
These fixed limits do not change with the validator query budgets.

Only the validator UID is excluded from the private TUN, and its namespace firewall permits queries solely to the private TCP transport listeners plus local replies.
The validators cannot send external DNS traffic themselves.
Application DIRECT targets cannot borrow the validator or transport ports.
Validator failure makes fresh public lookups fail without a plaintext or direct-access fallback.

Enabling local validation requires rebuilding the generation and restarting the managed services.
Known previous managed DNS and TUN values are migrated to the new root policy.
Foreign values and controller changes that weaken it remain rejected.
Returning to the compatibility path requires explicitly selecting `localDnssec=false` in a rebuilt generation.

## Restricted socket guest DNS

When `my.hardening.socketGuestUsers` is nonempty or controlled sources are enabled, [socket-guest.nix](../system/config/network/dns/socket-guest.nix) starts an additional loopback dnsmasq on port 1056 as UID 985.
It shares the immutable suffix filter, additions and exceptions and returns real addresses.
With runtime routing it uses the selected gateway path described above; otherwise it forwards to host Unbound 1055.
It has no plaintext lease, split-DNS or native MagicDNS path.
Unavailable DoT, including `unboundDotInProxy=false` in B, makes fresh public names fail.
Eligible cached/local answers remain available.

The declared primary and subordinate UIDs have TCP/UDP port 53 redirected here before the core's auto-redirect.
A post-NAT check rejects old conntrack associations that still point at a previous resolver.
Root can query 1056 directly, as can the dedicated `clash-fetch` source worker when controlled sources are enabled.
The private-destination guard refuses direct fake-IP/known translation targets and host proxy inlets.
Independent HTTPS/DoH payloads remain outside suffix filtering, while their resulting literal socket targets still face the account policy.

C cuts these accounts' tracked replies as well as public egress.
This path belongs to [host-socket guest authorization](network.md#guests-and-containers).
The normal desktop resolver and bridge gateway DNS retain their separate paths.

## Blocklist

[blocklist.nix](../system/config/network/dns/blocklist.nix) decompresses the tracked [snapshot](../local-data/dns-blocklist.txt.xz) once at build time.
The same immutable bare-domain file supplies dnsmasq's suffix NXDOMAIN rules and the core's in-memory filter.
Nothing is downloaded during a build or at runtime.

`my.proxy.blockedDomains` adds host-specific names.
`blockedDomainExceptions` renders `server=/domain/#` and the matching core exception.
Both compare suffixes case-insensitively at label boundaries: the most specific entry wins, and an exception wins at equal specificity.
A narrower block can therefore block part of an excepted subtree again.
An exception permits resolution.
It does not disable routing or the private-IP guard.

Explicit split-server entries also participate in [dnsmasq's domain precedence](https://dnsmasq.org/docs/dnsmasq-man.html), so an approved private namespace can supersede a broader block.

The root managed policy owns the core's stable `/etc/netsec/dns-blocklist.txt` path, additions and exceptions.
The path resolves to the immutable build-validated snapshot. Its content checks remain independent of subscription policy identity.
Cached GUI profiles inherit them during parsing, and controller writes cannot replace them.
Missing or invalid list data rejects a new configuration while preserving the active filter.
Application dial checks run before internal-suffix permissions.
Those permissions cannot bypass a blocked name.
Internal resolver queries share the filter, while explicit hosts/IP infrastructure mappings retain their separate role.
A listed proxy-endpoint name can also lose DNS resolution.
Declare a narrow `blockedDomainExceptions` entry when that endpoint is intentionally trusted.

Refresh upstream data with [refresh-dns-blocklist.py](../scripts/refresh-dns-blocklist.py).
`--check` verifies the committed snapshot, manifest and header without writing; add `--fetch` to compare the upstream sources.
A mismatch fails the configuration build instead of producing a partial filter.
Rebuild and restart the resolver/core generation to apply new data, and restart clients that retain DNS or connections.
Existing connections are not retroactively inspected.
Upstream sources and snapshot metadata are recorded in the module header and [local-data guide](../local-data/README.md).

## Wi-Fi, split DNS and MagicDNS

Wi-Fi DNS edits save only the six DNS properties in root-private `/var/lib/netsec-dns-profiles/`.
`sudo proxy-dns-profile plan <UUID>` reports backup/restoration readiness.
`restore <UUID>` restores those fields without reconnecting.
Restoration refuses intervening manual edits.
Approving a backed-up profile for split DNS restores its original DNS values.
Credentials, routes and identity fields are outside the backup.

[nm_dns.py](../system/config/network/dns/nm_dns.py) disables automatically supplied DNS for both families on managed Wi-Fi, clears unwanted explicit servers/search domains and pins resolved to the local gateway with `~.`.
Fallback discovery reads raw DHCP information rather than this rewritten effective DNS list.
Wired dhcpcd remains separate.

`my.proxy.splitDnsProfileUuids` is empty by default.
Approved profiles can supply private/ULA conditional resolvers already excluded from TUN by the shared [direct-egress routing policy](egress.md), including restricted B.
Dnsmasq binds queries to the managed Wi-Fi interface.
Leases bind UUID, ActiveConnection, ifindex, carrier-down counter and revocation epoch, expire within 120 seconds of observation and are revoked on dispatcher transitions.

Public upstreams and unreadable/unsupported profiles enter `split-error` rather than opening default forwarding.
A local resolver identity failure enters `resolver-error` and cannot open plaintext fallback.

[resolved_policy.py](../system/config/network/dns/resolved_policy.py) excludes the managed TUN from resolved's default selection.
Tailscale automatic DNS/routes remain disabled.
Only the authenticated tailnet's current MagicDNS suffix routes to its resolver with `DefaultRoute=no`.
Literal peer addresses and this verified suffix retain intentional private access.
The host gateway binds local resolver endpoints.
Serving LAN/tailnet clients requires a separate listener, client ACL and answer-routing policy.
