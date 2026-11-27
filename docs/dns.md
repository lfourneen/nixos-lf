# DNS

English | [简体中文](CN/dns_CN.md) | [Documentation](README.md)

In `host.nix`, this machine selects `dnsEncryption="strict"` and `localDnssec=true`: **A** validates DNSSEC locally through Unbound and its 360 DoT endpoints, while **B** validates through the core validators—sending `geosite:cn` directly to 360 DoH and everything else to Cloudflare/Google DoH behind the remote proxy. The module default for `localDnssec` is `false`, and the opportunistic leases and recovery budgets described below apply only when strict encryption is off: `strict` grants no public plaintext recovery or runtime `force-plaintext` override, leaving only cached/local answers if DoT goes down (literal-IP portals still work through their separate entry, and an older recovery generation has to be booted deliberately rather than downgraded to automatically).

## Resolver chain

```text
Applications -> systemd-resolved -> dnsmasq 127.0.0.1:1054
                                      | local suffix blocklist / exceptions
                                      +-> B: Mihomo 127.0.0.1:1053 (fake-IP)
                                      +-> A: Unbound 127.0.0.1:1055 (DoT)
                                      +-> approved split DNS / leased A fallback
```

[default.nix](../system/config/network/network.nix) configures the resolver services, and [upstream.nix](../system/config/network/dns/upstream.nix) reconciles `dnsmasq`'s `/run/dns-upstream/servers.conf` via `SIGHUP` (keeping failed applies pending for retry). Only root diagnostics and the `dnsmasq` UID may query internal ports 1053/1055, while 1054 faces clients. In runtime `proxy-only`, the outer boundary blocks host DoT and split/MagicDNS paths—even in **C**—while the real-address gateway below keeps the core validator path open. Shared TCP/UDP listener checks (address, UID, and cgroup) live in [listeners.py](../system/config/network/proxy/listeners.py).

Plain DNS on uplinks—including hot-plugged interfaces and hardcoded private or portal resolvers—is redirected into `dnsmasq` for both IP families, while `dnsmasq`'s own upstreams and approved MagicDNS follow explicit rules. Under strict encrypted DNS, the boot firewall blocks external TCP/UDP ports 53 and 853 before uplinks come up (allowing only the configured Unbound UID to reach declared DoT endpoints over TCP); the full policy then takes over atomically and drops any plain DNS still headed for an uplink after redirection unless covered by an active lease. An early port-853 guard permits root, the marked core UID, and the configured Unbound path, blocking standalone application DoT/DoQ before `auto-redirect` can hide the original port (DoH shares port 443 with HTTPS, so this rule cannot separate it).

## Responsibilities

| Path | Domain filtering | Transport and validation |
|---|---|---|
| System queries, including Gost's **A** passthrough | `dnsmasq` suffix rules before the selected upstream | **A** uses Unbound DoT and local DNSSEC validation (default recovery can use leased plaintext); **B** forwards to the core |
| Core DNS listener and TUN DNS hijack | Shared suffix rules before hosts, cache, and fake-IP allocation | Local validation precedes address synthesis when `localDnssec` is on; otherwise managed DoH/DoT |
| HTTP/SOCKS proxy target in **B** | Shared suffix rules before application TCP/UDP dial, including private-domain exceptions | Real-IP checks and pinning for public and approved internal names (`localDnssec` validates public answers locally); the actual leaf must authorize private targets |
| Node / encrypted-resolver bootstrap | Core resolver queries use the shared filter before cache/upstream access | Independent validator with `localDnssec`, otherwise literal-address encrypted bootstrap resolvers. `rules` keeps its independent bootstrap transport; runtime `proxy-only` starts from accepted pinned nodes and sends both validator roles through the remote group. Internal infrastructure is exempt from application target/IP checks |
| Approved split DNS / native MagicDNS | Split forwarding follows `dnsmasq`'s domain precedence; `resolved`'s native MagicDNS route bypasses `dnsmasq` | Explicit private resolver / Tailscale overlay exception; no local public DNSSEC guarantee |

The suffix filter returns local NXDOMAIN for blocked domains. **A** validates DNSSEC locally with Unbound, and **B** uses local validators when `localDnssec` is on or the core's encrypted resolvers otherwise; private-address checks run separately on DNS answers and application targets. Literal IP requests, standalone application DoH, remote lookups outside the managed core, and native MagicDNS sit outside the common suffix filter's reach.

## Mode A: validation and recovery

### Encrypted upstreams and health

Unbound uses 360 DoT endpoints (`101.226.4.6`, `218.30.118.6`, and `123.125.81.6`, authenticated as `dot.360.cn`). Its local `validator iterator` enforces DNSSEC and ignores client CD flags, seeded from locked `dns-root-data` into a persistent RFC 5011 anchor under `/var/lib/unbound` so startup never fetches an unauthenticated initial anchor (unsigned zones remain unsigned).

Upstream private/CGNAT, guest, loopback, unspecified, link-local, and ULA answers (including IPv4-mapped forms) are stripped, while explicit local data and narrow `private-domain` declarations stay usable. During upstream outages, validated cache entries can be served for up to 900 seconds with a 30-second reply TTL; split DNS and plaintext fallback bypass this Unbound path.

[dnssec_probe.py](../system/config/network/dns/dnssec_probe.py) verifies both local DNSSEC validation (8-second limit) and a fresh certificate-authenticated TLS/DNS exchange with an upstream (5-second shared deadline per exchange)—cached validation alone never proves upstream health. `secure` requires both to pass; if local validation passes while transport fails, it reports `unavailable`. Both verdicts appear as `dnssec_probe.local` and `dnssec_probe.transport` in `/run/dns-upstream/state.json` and `/var/lib/dns-upstream/history.log`. In **A**, two consecutive local timeouts or bare `SERVFAIL` replies while authenticated DoT is reachable trigger one restart of the host validator (at most once per minute), with health still judged under the active DNS policy. Manual runs without transport endpoints report `scope=validation-only`.

### Plaintext recovery and allowance

When the encrypted path cannot return a usable verdict under the default `my.proxy.dnsEncryption="opportunistic"`, **A** can open a bounded plaintext window using the raw DHCP4 resolvers (or `1.1.1.1`/`1.0.0.1` if none were provided) on a separate recovery path outside Unbound. Because a network that blocks encrypted upstreams can trigger plaintext fallback within this allowance, switch to the strict policy below if that tradeoff doesn't suit you.

| Control | Default behavior |
|---|---|
| Lease | At most 120 seconds per renewal, constrained by remaining allowance |
| Session allowance | 1800 seconds; plaintext spends one second per second |
| Replenishment | Consecutive successful validation-and-transport rounds in the same coordinator/policy/session earn at most one second per second (recovery does not reset the pool) |
| Session identity | Boot ID + interface index + Wi-Fi ActiveConnection where applicable |
| Carrier / DHCP changes | Do not mint a fresh allowance; service restarts retain accounting |
| Optional boot deadline | `my.proxy.plaintextBootBudgetSeconds`, default `null` |
| Explicit override | Root-owned `/run/dns-upstream/force-plaintext`, active until removed or rebooted |

The override can outlast the allowance, though it is still billed and renewed in bounded leases (removing it doesn't refund spent time), and in **C** earlier output rules cut external DNS regardless of fallback settings.

[dns_budget.py](../system/config/network/dns/dns_budget.py) tracks the current boot's allowance under `/run/dns-upstream/budgets/`—counting authorized time across lease expiries and coordinator restarts, blocking renewal if state is corrupt, and treating persistent history strictly as diagnostics. Replenishment only accrues between successful observations at most 90 seconds apart in the same coordinator, policy, and network session (restarts, failed applies, sleep, and unobserved gaps earn nothing, and `plaintextBootBudgetSeconds` caps replenishment when set). Meanwhile, [dns_policy.py](../system/config/network/dns/dns_policy.py) acts as the privileged kernel-lease broker using expiring `nft` sets scoped to the `dnsmasq` UID and exact upstreams, so a stalled coordinator can't hold a lease past expiry, and sleep/link changes revoke grants before renewal.

### Upstream configuration and time

`my.machine.dotEndpoints` (defaulting from `dotUpstreams` and `dotAuthName`) declares literal IPv4/IPv6 endpoints and their certificate `authName` for host Unbound, the Mode-A probe, and the host firewall—pick endpoints reachable from the networks you use. `my.proxy.dnsStaleWaitMilliseconds` (default 300 ms) sets how long to wait for fresh data before serving eligible stale cache, and core DNS IPv6 follows host IPv6 support.

Both DoT and NTS depend on an accurate clock: if your RTC is off, check the time via an independent trusted source, set the clock locally, and restart the encrypted services. Certificate time checks always stay on, and plaintext DNS policy never substitutes for time authentication.

## Optional strict encryption

```nix
my.proxy.dnsEncryption = "strict";
```

This governs managed **public DNS transport** independently of A/B/C, `bootMode`, browser privacy, and `proxyOnly`. **A** keeps authenticated DoT, and **B** keeps its managed encrypted resolver and node-bootstrap endpoints, while the coordinator never opens a plaintext recovery window and ignores `force-plaintext`, the broker refuses plaintext grants, and the firewall omits the fallback accept rule (old budgets, overrides, and leftover set elements can never reopen it). The early DNS guard also blocks the core's external TCP/UDP port 53 except for its configured tailnet MagicDNS path.

Approved private split DNS and MagicDNS stay open as intentional exceptions, and loopback DNS remains local IPC. Note that this setting does not encrypt every application protocol, inspect arbitrary HTTPS/DoH payloads, authenticate unsigned zones, or constrain privileged root diagnostics ([Mihomo's resolver roles](https://wiki.metacubex.one/config/dns/) separate application DNS from proxy-endpoint and resolver bootstrap).

When encrypted DNS goes down, fresh public lookups fail while local and eligible cached answers keep working; state reports `encryption_policy=strict`, `degraded-dot-only`, a zero window, and no active plaintext override until the encrypted path recovers. You can switch uplinks, use a literal HTTP portal address in **A**, or fix the declared authenticated upstreams—if a portal truly depends on broken public DNS, boot into a pre-built `opportunistic` generation deliberately (neither `clash-off` nor a runtime override relaxes strict mode; see [offline recovery](operations.md#offline-recovery)).

## Mode B: managed Mihomo DNS

[profile.nix](../system/config/network/proxy/profile.nix) defines the managed DNS setup. TUN mode uses fake-IP (`198.18.0.1/16` by default), a fixed blacklist-style fake-IP filter, and DNS hijack—local suffixes and selected connectivity/STUN names get real addresses (filtering fake-IP changes resolution only, not routing). HTTP-only mode (`tunMode=false`) uses `redir-host`.

When `localDnssec=true`, application DNS goes to a private Unbound validator on `127.0.0.1:1055`, and node/resolver bootstrap goes to a separate validator on `127.0.0.1:1056`. Both live inside the core namespace—completely separate from the host services on those same ports—maintaining their own persistent RFC 5011 anchors from locked `dns-root-data` and ignoring client CD requests. Each forwards over TCP loopback to a private Mihomo transport listener (port 1057 for application queries, 1058 for bootstrap), passing raw DNSSEC material without hosts substitution, fake-IP synthesis, or ordinary cache reuse.

Before accepting any name, the application validator also needs `DS`, `DNSKEY`, `NSEC`/`NSEC3`, and RFC 5011 key-tag responses for parent and target zones. Because those queries don't match `geosite:cn`, a name-only policy sends them over proxied DoH, meaning a dead DNS leaf would break domestic `DIRECT` validation too. To avoid that, the transport tries the current leaf first and, if it fails to return usable material (including `SERVFAIL`, `REFUSED`, or truncation) within one second, falls back to the 360 literal DoH endpoints over `#DIRECT` and skips that leaf for 30 seconds while keeping normal domestic/proxied splits for application A/AAAA/HTTPS records. (That fallback can reveal the queried zone name to the domestic validating resolver, though DNSSEC still decides validity; runtime `proxy-only` disables this fallback and keeps all DNS roles on the fixed remote leaf.)

In `rules`, application transport keeps a domestic split (`geosite:cn` via 360 DoH directly, other names via Cloudflare/Google DoH). With controlled sources, foreign queries use `lf-dns`, a dedicated selector of authorized `lf-remote` leaves keeping one leaf per protocol, pinned server address, and port (prioritizing distinct server addresses and protocols). If the default `rules` upstream doesn't answer within 1.5 seconds, the transport retries the same DoH endpoints through `lf-remote`, while domestic queries keep their policy resolver (in fixed-exit mode, all upstreams use the authorized exit). The application and DNS selectors pick independently, `profile.store-selected` preserves eligible picks across core restarts, and if the managed resolver fails health checks, the supervisor samples a bounded batch of authorized DNS leaves across probes—giving a working session time to warm up DNSSEC validation. (Setups without controlled sources keep `respect-rules` selection.) The separate bootstrap validator on port 1056 handles node and resolver bootstrap only, while the application validator handles app names and public dial checks. Domestic DoH endpoints come from `my.machine.mihomoDomesticValidatingDnsUpstreams` and must return full DNSSEC material (360 does; non-validating AliDNS does not). Cold foreign lookups can take several remote round trips to validate, whereas domestic ones stay domestic. Mode **A**'s `my.machine.dotEndpoints` is independent of both **B** roles; Mihomo keeps TLS cert verification on, and upstream failures never fall back to plaintext. Keep in mind that the DNS leaf operator can see the DoH endpoint and timing, while the DoH resolver sees foreign queries and the DNS leaf's exit IP.

Runtime `proxy-only` keeps both local validators and their independent budgets, but sends both roles through authenticated literal-address DoH endpoints bound to the same fixed remote leaf as application traffic. With the [WireGuard exit](proxy.md#wireguard-exit-with-real-ping), system DNS also uses the application validator's real-address gateway so hostname pings go out over native ICMP. It strips domestic resolver-policy maps, direct bootstrap, and MagicDNS transport—the core starts from accepted, pinned node addresses while preserving TLS server names, so switching needs no network fetch or direct name lookup, and an unavailable node or validator holds **C**.

During **B** DNS startup (`proxy-priming`) and probe failure (`degraded-proxy`), the ordinary resolver keeps routing public queries to the selected core path (so fresh queries can fail if the managed path is down), and switching into **B** cuts any in-flight DoT and loopback replies from **A**'s application Unbound on port 1055.

The real-address filtering resolver for subscription/provider hostnames, node endpoint pinning, and restricted socket backends listens on port 1056 (using the infrastructure gateway on port 1062 with runtime routing). `rules` and **A** use a dedicated host Unbound on port 1061 with its own DoT connections and cache; for `proxy-only` intent, the gateway reaches the core application validator over a root-owned Unix socket. Port 1059 serves ordinary real-address queries when the WireGuard exit is active and refuses them in **A** and `rules` (host DoT, split DNS, and native MagicDNS cannot cross the `proxy-only` outer firewall). Switching flushes host resolver caches and recreates core validators, while managed fake-IP mappings persist so cached app addresses keep their domain identity.

With `localDnssec=false`, public servers use Cloudflare/Google DoH while domestic names and proxy-endpoint bootstrap use the configured domestic resolvers—this compatibility path does not validate DNSSEC locally in **B**. Both configurations enable `respect-rules`, disable extra direct resolvers and proxy-server resolver-policy maps, and lock managed fields against runtime weakening in the parser/controller.

After routing, the core validates and pins domain dial targets—including approved internal suffixes. With local validation on, public dial targets use locally validated answers before pinning real IPs (subscription or system hosts can never swap in an unvalidated public answer), and literal or resolved private targets still need local `DIRECT` or scoped remote-leaf grants. New UDP mappings are validated; cached mappings skip re-resolution but recheck egress and port grants on every packet. Remember that the [target authorization policy](proxy.md#domain-targets) is separate from DNS filtering—a DNS or suffix exception never grants remote internal access.

Public dial-target checks coalesce concurrent lookups for the same name and query A/AAAA in parallel within a 3-second deadline, returning as soon as one family validates rather than waiting on a stalled family. Validated answers are cached for up to 10 seconds (capped by upstream/CNAME TTLs), zero-TTL answers and failures are never cached, and each policy owns its cache and in-flight work so a new policy never reuses stale results.

In managed fake-IP mode, address records stay synthetic while HTTPS/SVCB queries go through the configured resolver and preserve service parameters—both after passing the shared filter. Local validation runs before public fake-IP allocation, preserving bogus failures, signed NXDOMAIN, and NODATA (including CNAME chains lacking the requested type), while synthetic answers clear AD since the fake mapping itself isn't signed. Unsigned answers remain unauthenticated, explicit private-domain exceptions keep their own scope, and managed DNS rejects multi-question queries. See [the browser guide](browser.md#ech-discovery-and-proxy-compatibility) for how Firefox uses these records, its `CONNECT` limitation, and the optional native path.

### Local validation and role budgets

```nix
my.proxy.dnsEncryption = "strict";
my.proxy.localDnssec = true;
my.proxy.dnsQueryBudget = {
  application = 256;
  infrastructure = 64;
};
```

Local validation requires strict encryption and `coreNetns=true`; the query budgets above are the module defaults (allowed ranges: 32–4096 and 8–1024). Each validator gets its own request list, cache, file-descriptor limit, and memory limit so application load can't starve bootstrap validation. The application validator reserves 16 MiB for DNS messages and 32 MiB for RRsets, prefetches popular answers near expiry, and can serve eligible expired answers for up to 300 seconds (waiting up to `my.proxy.dnsStaleWaitMilliseconds`, default 300 ms, for a fresh answer; stale replies carry at most a 30-second TTL, while fresh records respect upstream TTLs). The bootstrap validator gets 4 MiB for messages and 8 MiB for RRsets and requires fresh answers. Excess requests may be rejected, freeing capacity as in-flight work finishes; **A**'s host validator and restricted socket guest DNS remain separate, and per-application conntrack, UDP-association, and proxy-queue quotas sit outside these budgets.

Separately, the dial-target guard caps itself at 256 cached names and 256 in-flight names regardless of validator query budgets. Only the validator UID is excluded from the private TUN, and its namespace firewall allows queries solely to the private TCP transport listeners plus local replies—the validators cannot send external DNS traffic themselves, application `DIRECT` targets cannot borrow validator/transport ports, and validator failure fails fresh public lookups closed with no plaintext or direct fallback.

Enabling local validation requires rebuilding and restarting managed services (legacy managed DNS/TUN values migrate to the new root policy, while foreign values and weakening controller writes are rejected); going back to the compatibility path requires explicitly setting `localDnssec=false` in a rebuilt generation.

## Restricted socket guest DNS

Whenever `my.hardening.socketGuestUsers` is non-empty or controlled sources are enabled, an extra loopback `dnsmasq` runs on port 1056 under the dedicated `socket-guest-dns` user (UID 978)—see the [resolver service](../system/config/network/dns/socket-guest.nix). It shares the immutable suffix filter, additions, and exceptions and returns real addresses, forwarding directly to infrastructure Unbound on port 1061 when runtime routing is off, with no plaintext lease, split DNS, or native MagicDNS. In **A** and `rules`, unavailable infrastructure DoT fails fresh public lookups (setting `unboundDotInProxy=false` disables that transport in `rules`), while eligible cached and local answers still work.

TCP/UDP port 53 for declared primary and subordinate UIDs is redirected here ahead of the core's `auto-redirect`, with a post-NAT check rejecting stale conntrack associations pointing at a previous resolver. Root—and `clash-fetch` when controlled sources are enabled—can query 1056 directly. The private-destination guard blocks direct fake-IP/known translation targets and host proxy inlets, and while standalone HTTPS/DoH payloads sit outside suffix filtering, their resulting literal socket targets still hit the account policy. **C** cuts these accounts' tracked replies as well as public egress; this belongs to [host-socket guest authorization](network.md#guests-and-containers), leaving ordinary desktop resolution and bridge gateway DNS separate.

## Blocklist

[blocklist.nix](../system/config/network/dns/blocklist.nix) decompresses the tracked [snapshot](../local-data/dns-blocklist.txt.xz) once at build time—nothing is downloaded during builds or at runtime—and feeds the exact same bare-domain list to `dnsmasq`'s suffix NXDOMAIN rules and the core's in-memory filter.

`my.proxy.blockedDomains` adds host-specific names, while `blockedDomainExceptions` emits `server=/domain/#` and the matching core exception. Both match suffixes case-insensitively at label boundaries, where the most specific entry wins and exceptions break ties—so you can still re-block a narrower subdomain inside an excepted subtree. Exceptions only permit resolution without disabling routing or the private-IP guard, and explicit split-server entries also participate in [dnsmasq's domain precedence](https://dnsmasq.org/docs/dnsmasq-man.html) so an approved private namespace can override a broader block.

The root managed policy owns `/etc/netsec/dns-blocklist.txt`, additions, and exceptions, pointing to the immutable build-validated snapshot independently of subscription policy identity; cached GUI profiles inherit them during parsing and controller writes can't swap them out (missing or invalid list data rejects a new config while preserving the active filter). Application dial checks run before internal-suffix permissions so those permissions can't bypass a block, internal resolver queries share the filter (explicit hosts/IP infra mappings keep their own role), and if a proxy-endpoint hostname itself lands on the list, declare a narrow `blockedDomainExceptions` entry once you've verified you trust it.

Update upstream data with [refresh-dns-blocklist.py](../scripts/refresh-dns-blocklist.py): `--check` validates the committed snapshot, manifest, and header without writing, and `--fetch` compares against upstream sources (any mismatch fails the build rather than shipping a partial filter). Rebuild and restart the resolver/core generation to apply updates, and restart clients that cache DNS or connections (existing connections aren't retroactively checked). Upstream sources and snapshot metadata are in the module header and [local-data guide](../local-data/README.md).

## Wi-Fi, split DNS and MagicDNS

Before NetworkManager edits uplink DNS, the dispatcher saves the six original DNS properties into root-private `/var/lib/netsec-dns-profiles/` and syncs a full backup. Run `sudo proxy-dns-profile plan <UUID>` to inspect backup path, status, and restorability, or `sudo proxy-dns-profile restore <UUID>` to restore those fields without reconnecting (refusing to overwrite intervening manual edits). If `plan` shows `backup_status: invalid`, keep the file for inspection and cross-check against a known-good NetworkManager backup—both dispatcher and restore refuse profile edits while a backup is invalid. Approving a backed-up profile for split DNS restores its original DNS values (credentials, routes, and identity fields aren't touched).

[nm_dns.py](../system/config/network/dns/nm_dns.py) disables automatic DNS for both IP families on managed NetworkManager uplinks, clears unwanted explicit servers and search domains, and pins `resolved` to the local gateway with `~.`. Fallback discovery reads raw DHCP info rather than this rewritten list, and wired `dhcpcd` stays separate.

`my.proxy.splitDnsProfileUuids` is empty by default. Approved profiles can provide private/ULA conditional resolvers already excluded from TUN by the shared [direct-egress routing policy](egress.md) (including restricted **B**), with `dnsmasq` binding queries to the managed Wi-Fi interface; leases bind UUID, `ActiveConnection`, `ifindex`, carrier-down counter, and revocation epoch, expire within 120 seconds of observation, and are revoked on dispatcher transitions. Public upstreams or unreadable/unsupported profiles enter `split-error` instead of opening default forwarding, while local resolver identity failures enter `resolver-error` without opening plaintext fallback.

[resolved_policy.py](../system/config/network/dns/resolved_policy.py) keeps the managed TUN out of `resolved`'s default selection, and Tailscale automatic DNS/routes stay off. `resolved` installs the active authenticated tailnet's suffix on its link with `DefaultRoute=no`, and in Rules **B** the core uses that same authenticated suffix to pick MagicDNS ahead of public DNS caching or fake-IP synthesis (other `.ts.net` names follow normal managed DNS). The core returns MagicDNS records without public DNSSEC validation, tying the route to the authenticated account, tailnet, and interface instance—a confirmed switch or logout revokes the old route, while a brief status timeout can ride out the remainder of the current 135-second authorization (on a suspend-inclusive clock, requiring a fresh observation on resume). The firewall binds MagicDNS resolver queries to that interface instance and deadline, and the managed resolver keeps previously authenticated suffixes local for the rest of the boot across logout, expiry, and interface replacement. Literal peer addresses and the verified suffix preserve intentional private access, the host gateway binds local resolver endpoints, and serving LAN/tailnet clients requires a separate listener, client ACL, and answer-routing policy.
