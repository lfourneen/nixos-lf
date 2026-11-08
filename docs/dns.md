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

## Mode A: validation and recovery

Unbound uses 360 DoT endpoints `101.226.4.6`, `218.30.118.6` and `123.125.81.6`, authenticated as `dot.360.cn`. Local `validator iterator` validates DNSSEC and ignores client CD requests. The locked `dns-root-data` seeds a persistent RFC 5011 anchor in `/var/lib/unbound`; startup does not fetch an unauthenticated initial anchor. Unsigned zones remain unsigned.

Upstream private/CGNAT, guest, loopback, unspecified, link-local and ULA answers are filtered, including IPv4-mapped forms. Explicit local data and narrowly declared private domains remain usable. Validated cached data can be served for up to 900 seconds during upstream failure, with a reply TTL of 30 seconds. Split DNS and plaintext fallback bypass this Unbound path.

[dnssec_probe.py](../system/programs/systemd/netsec/dnssec_probe.py) separates local validation from transport diagnosis. A fresh random root name requires an authenticated denial rather than a cached repeated answer; an independent TLS query checks whether the encrypted upstream answers and supplies signature material. Cold anchor priming is deferred only while transport works. Verdicts, RCODE, EDE and failure categories appear in `/run/dns-upstream/state.json` and persistent `/var/lib/dns-upstream/history.log`.

When the encrypted path cannot supply a usable verdict, A may open a bounded plaintext window using the original DHCP4 resolvers, or `1.1.1.1`/`1.0.0.1` when none were supplied. These are a separate recovery path, not Unbound upstreams.

| Control | Default behavior |
|---|---|
| Lease | At most 120 seconds per renewal, constrained by remaining allowance |
| Session allowance | 1800 seconds; plaintext spends one second per second |
| Replenishment | Only a validated encrypted path earns one second per second; recovery does not reset the pool |
| Session identity | Boot ID + interface index + Wi-Fi ActiveConnection where applicable |
| Carrier / DHCP changes | Do not mint a fresh allowance; service restarts retain accounting |
| Optional boot deadline | `my.proxy.plaintextBootBudgetSeconds`, default `null` |
| Explicit override | Root-owned `/run/dns-upstream/force-plaintext`, active until removed or rebooted |

The override can outlast the allowance, but is still billed and renewed through bounded leases. Removing it does not restore spent time. In C, earlier output policy cuts external DNS regardless of fallback selection.

[dns_policy.py](../system/programs/systemd/netsec/dns_policy.py) is the privileged kernel-lease broker. Timed nft sets permit only the dnsmasq UID and exact upstream destinations. A stalled or dead coordinator cannot keep a lease past expiry. Sleep and connection changes revoke permissions before renewal.

## Mode B: managed Mihomo DNS

[proxy-profile.nix](../system/config/proxy-profile.nix) defines the managed DNS values. TUN mode uses fake-IP (`198.18.0.1/16` by default), a fixed blacklist-style fake-IP filter and DNS hijack. Local suffixes and selected connectivity/STUN resources receive real answers. Filtering fake-IP changes resolution, not routing. HTTP-only mode (`tunMode=false`) uses redir-host.

Public name servers use Cloudflare/Google DoH; domestic names and proxy-endpoint bootstrap use the configured domestic resolvers. `respect-rules` is enabled, additional direct resolvers and proxy-server resolver-policy maps are disabled, and the core's parser/controller policy prevents runtime weakening of managed fields. B's resolver is separate from A's local Unbound validator.

The core also validates and pins domain dial targets after routing/sniffing, for both direct and proxy TCP/UDP. New UDP destination mappings are checked; later packets reuse their checked mapping. The [target guard and its exemptions](proxy.md#domain-targets) are separate from DNS answer filtering.

## Blocklist

[dns-blocklist.nix](../system/config/dns-blocklist.nix) renders the tracked [compressed snapshot](../local-data/dns-blocklist.txt.xz) into dnsmasq suffix NXDOMAIN rules. It applies at the shared entry in A/B and to clients using gateway DNS, so mode switching does not change the list. Nothing is downloaded during a build or at runtime.

`my.proxy.blockedDomains` adds host-specific names. `blockedDomainExceptions` renders `server=/domain/#`: a more specific exception uses the current mode's standard upstream. Refresh upstream data with [refresh-dns-blocklist.py](../scripts/refresh-dns-blocklist.py); `--check` reports drift without writing. Upstream sources and snapshot metadata are recorded in the module header and [local-data guide](../local-data/README.md).

This is a local DNS filter. Explicit proxy target resolution can occur inside Gost/Mihomo without traversing dnsmasq; independent DoH and remote proxy resolution are outside the blocklist's guarantee.

## Wi-Fi, split DNS and MagicDNS

[nm_dns.py](../system/programs/systemd/netsec/nm_dns.py) disables automatically supplied DNS for both families on managed Wi-Fi, clears unwanted explicit servers/search domains and pins resolved to the local gateway with `~.`. Fallback discovery reads raw DHCP information rather than this rewritten effective DNS list. Wired dhcpcd remains separate.

`my.proxy.splitDnsProfileUuids` is empty by default. Approved profiles can supply private/ULA conditional resolvers already excluded from TUN by `allowPrivateEgress`. Dnsmasq binds queries to the managed Wi-Fi interface. Leases bind UUID, ActiveConnection, ifindex, carrier-down counter and revocation epoch, expire within 120 seconds of observation and are revoked on dispatcher transitions. Public upstreams and unreadable/unsupported profiles enter `split-error` rather than opening default forwarding. A local resolver identity failure enters `resolver-error` and cannot open plaintext fallback.

[resolved_policy.py](../system/programs/systemd/netsec/resolved_policy.py) excludes the managed TUN from resolved's default selection. Tailscale automatic DNS/routes remain disabled; only the authenticated tailnet's current MagicDNS suffix routes to its resolver with `DefaultRoute=no`. Literal peer addresses and this verified suffix retain intentional private access. External LAN/tailnet DNS service requires its own listener, client ACL and answer-routing policy; the host gateway is not a wildcard resolver.
