# Proxy core and permissions

English | [简体中文](CN/proxy_CN.md) | [Documentation](README.md)

## Controlled subscription sources

The host selects `controlledSources=true`, `subscriptionTrust="fixed"`, `profileRefreshPolicy="auto"` and blocking content audit.
The reusable module retains the legacy GUI import path when controlled sources are disabled.
The GUI-specific import and mutable-provider descriptions below apply to that compatibility path.

`clash-source` registers sources through a root-only broker, with one source selected at a time.
Each has its own subscription, providers, revision, approvals and accepted baseline.
`use <id>` selects a registered source.
The GUI can select only sources mapped to a root-approved `gui-profile` UID.

An unprivileged worker downloads the HTTPS subscription and registered HTTP providers, pins public endpoint addresses and renders the configuration.
Nix supplies listeners, DNS, TUN settings and geodata.
Providers become inline nodes.
GUI URLs, YAML, scripts and caches cannot authorize a new source.

The Sops `proxy_source` string can hold `{"sources":[...]}` for all definitions.
`review/register FILE --id ID` selects one entry.
An unqualified review lists the catalog, while registration refuses an ambiguous multi-entry choice.
Legacy single-source JSON remains accepted.
IDs and GUI mappings must be unique.
Only the selected entry contributes to its registration digest.
Adding another definition neither changes that digest nor approves the new source.
Removing a catalog entry does not revoke its registered copy.

Downloads enforce normal TLS hostname verification, pinned public socket addresses, exact approved HTTPS redirects, 8 MiB per response, a 32 MiB combined budget and a worker deadline.
The default User-Agent is `clash.meta`.
Each endpoint may register a single-line ASCII `user-agent` of at most 256 characters.
Custom UA and credentials are removed permanently after a cross-origin redirect.
YAML aliases and duplicate keys are refused.

Node domains are pinned while retaining their TLS identity.
Unresolvable nodes may be dropped up to `max(1, node_count // 5)`.
Non-public answers and rule-referenced empty groups still refuse the candidate.
The renderer supports `select`, `url-test` and `fallback` groups.
Members must be explicit or refer to registered providers.

| Field | Accepted value |
|---|---|
| `url` | HTTPS URL |
| `interval` | Integer, 1–86400 seconds |
| `tolerance` | 0–60000 ms |
| `timeout` | 100–60000 ms |
| `max-failed-times` | 1–100 |
| `lazy` | Boolean |
| `expected-status` | HTTP status expression |

`load-balance`, dynamic filters, `rule-providers` and provider filters/overrides are unsupported.
Supported nodes are SS, VMess, VLESS, Trojan, Hysteria2, TUIC, HTTP and SOCKS5.
Remote HTTP/SOCKS plaintext authentication is refused.

A private manifest records source ID, revision, response hashes, transformer, policy, rendered YAML, dropped-node count and geodata.
The snapshot digest includes it.
Registrations and approvals live in root-private `/var/lib/clash-source/sources/<id>`.
A shared `source.lock` protects the atomic `selected` pointer.
Existing single-source state migrates on the next broker operation.

Approved snapshots survive switching and reboot.
Fetch failures preserve the selection, and parser, audit or health failures preserve the authorized runtime snapshot.
Until checks complete, `Active source` can differ from the requested selection.

The refresh service checks candidates before interrupting a ready core.
Rejected or unchanged candidates preserve that core only while its snapshot authority, integrity, full health and readiness remain valid.
An unhealthy or unauthorized runtime follows C recovery.
Applying a changed candidate enters C before restarting the core and must pass startup checks again.
Without a compatible fallback, C remains in effect.

`clash-off` revokes runtime authority while preserving the source's accepted baseline.
With automatic refresh, the next `clash-on` prefers that baseline under the current managed policy and records `restored`.
It repeats parser, content-audit and digest checks, and fresh live health still gates B.
The startup update request stays pending during C. After the coordinator verifies B, the background service consumes the request and starts fetching.
The request is coalesced so an update restarting the core does not cause repeated downloads.
A periodic attempt during C defers without reporting a download failure.
Neither waiting nor downloading blocks foreground core startup or requires waiting for the boot timer.
A first import without an accepted baseline uses the available candidate and the ordinary health observation window.
Stopping and restarting does not waive structural checks.

Node identity, authentication and transport changes are compared against that source's own accepted baseline, including after switching sources or rebooting.
This host automatically approves `node_identity`, `node_authentication` and `node_transport` changes, including logical-server and TLS-name rotation.
The same policy applies to the first `clash-on` after reboot and to the root refresh timer.
Routine updates require no manual review.

The reusable `my.proxy.subscriptionAuditAutoApprove` option defaults to an empty list, which disables automatic approval.
Automatic approval requires controlled sources, `profileRefreshPolicy="auto"`, `subscriptionAudit="enforce"` and an accepted baseline.
Every deny category must belong to the configured list, and there must be no unresolved findings.
Private or metadata addresses, certificate-validation bypasses, plaintext credentials, unauthorized hosts overrides, protected-route changes and malformed input continue to block import.
Sensitive-route changes in either direction are also excluded from automatic approval.

Each automatic record expires after five minutes and binds the candidate, baseline, source revision, managed policy, validator policy and audit policy.
Import repeats the normal parser, approval checks and blocking audit before health acceptance or rollback.
Changing the option to `[]` disables automatic approval without weakening checks on existing records.
See [source registration](operations.md#source-registration).

Source downloads and node-address pinning use the filtered real-address gateway on `127.0.0.1:1056`, independently of B's fake-IP answers.
Only the dedicated `clash-fetch` account and root can query that port directly.
The worker still rejects non-public answers and verifies HTTPS certificates.
A usable DNS family can be used when the other family is unavailable.
Downloads validate the entire address set before connecting and retry public addresses within one deadline, preserving HTTPS hostname and certificate checks.
If no usable answer remains, refresh fails without falling back to system DNS.
Fresh lookups therefore require the Unbound DoT path in B as well; `unboundDotInProxy=false` leaves only eligible cached answers.

## Visibility and trust

| Participant | Information it can observe |
|---|---|
| Browser and destination | Application content. The destination also knows accounts/cookies and exit address |
| Local HTTP proxy/core | CONNECT names, DNS and routing metadata. Plaintext HTTP content |
| Access network / DNS service | Transport addresses and timing. The resolver knows queries it answers |
| Entry/exit operators | Their adjacent addresses and flow timing. An exit sees destination metadata and plaintext HTTP |
| Colluding operators | Additional correlations. Local health probes cannot establish their honesty |

Status reports `controlled-fetch`, `legacy-unverified` or unknown provenance separately from mode, routing and availability.
The panel shows the latest refresh result, audit categories and counts, and the last automatic approval with its unique node count and time.
HTTPS certificate checks authenticate the download connection.
Publisher content signatures remain unchecked because this configuration has no publisher signature verifier.
Automatic content checks can reject identifiable risks, but cannot establish who initiated a rotation or authenticate the publisher.
A domain comparison cannot distinguish an operator rotation from a malicious replacement.
TLS protects authenticated content, while ECH and separate profiles do not hide accounts, destination IPs or timing.
An operator can still log or collude.

## Components and identities

| Component | Default endpoint / state | Identity |
|---|---|---|
| Mihomo | Mixed `127.0.0.1:7897`, DNS `:1053`, TUN `Mihomo` | `clash-core`, UID 982. `NET_ADMIN` and `NET_RAW` inside the core network namespace |
| Clash Verge helper | On-demand `clash-verge.service` | Root with bounded launch/file capabilities. Network capabilities confined to the core namespace |
| GUI controller forwarder | `/run/user/<desktop-uid>/clash-verge-rev/verge-mihomo.sock` | Desktop UID, core group, no capabilities. Socket 0600 |
| Gost | HTTP `127.0.0.1:33332`, transparent `127.0.0.1:33333` and `[::1]:33333` | UID 987, no capabilities |
| dnsmasq / Unbound / dhcpcd | Local DNS / wired DHCP | UIDs 985 / 983 / 986 |

[identity.nix](../system/programs/clash/service/identity.nix) and [runtime.nix](../system/programs/clash/service/runtime.nix) define the core identity and service contract.
The root launcher starts the core via `setpriv`, retaining only the two network capabilities in its permitted, effective, inheritable, ambient and bounding sets.
The real controller is `/run/clash-verge-rev/core/mihomo.sock`.
Supervisors use it directly.
A capless desktop-UID `socat` service in the host namespace supplies the GUI path over Unix byte streams, without passing file descriptors.

The helper retains the privileges needed for identity changes, directory ownership and child management.
It accepts only the packaged, root-owned launcher as an executable.
Managed logs use `/var/lib/clash-verge-service/logs`, with fixed rotation.
Caller-supplied IPC paths do not drive root chmod/unlink or startup reconciliation.
Systemd owns child cleanup.
The helper's private state is separate from the writable core cache in `/var/lib/clash-core`.

The helper enforces fixed execution paths and file-operation rules at its privileged boundary.
Systemd places the helper and core in `lf-clash-core` before execution.
The core cannot administer host interfaces, routes or firewall tables.
Host setup and policy changes belong to fixed root services.
Desktop programs sharing the GUI UID can use its permitted GUI/controller operations.

An explicitly enabled IPv4 guest TPROXY inlet binds `0.0.0.0:7896`.
The ordinary mixed/DNS/controller endpoints remain local under `allow-lan=false`.
The packaged listener handles this bind separately and selects IPv4 sockets.
Its TCP/UDP UID and service cgroup are checked before B.
Guest interface/source authorization remains in the firewall.
Later core auto-redirect preserves the owned guest mark instead of replacing its transparent socket.
See [guest networking](network.md#guests-and-containers).

## Compatibility GUI imports

`proxy-control preflight` checks the desktop profile, required assets and the managed parser without changing network intent.
`clash-on` requires this preparation to succeed, records proxy intent and enters C before starting the helper.
It clears refresh pins and pending health checks, preserves intact snapshot authority, and opens a single-use import window for 300 seconds.
A failed import consumes the window.

[clash-verge-stage.py](../system/programs/clash/service/clash-verge-stage.py) accepts bounded regular files owned by the desktop user, rejects symlinks and detects concurrent input changes.
It copies YAML and geodata into a root-owned snapshot, normalizes the controller location and runs `-t` as the separate `clash-validate` identity without capabilities.
Snapshot directories use 0750 and files use 0640.
Authority records and parser diagnostics remain private to root.

Authorized `127.0.0.1` and `::1` TCP/UDP targets reach host loopback through a separate capless `clash-loopback` broker.
Its Unix channels check the peer UID and reject reserved proxy/DNS ports.
UDP interception preserves the original destination port.
Opportunistic DNS port 53 has a fixed host-gateway mapping.
Strict DNS disables it.
These channels carry bytes and datagrams, never host socket descriptors.
Other loopback aliases are not forwarded.

The snapshot digest covers YAML, geodata bytes and absent optional assets.
Assets include `Country.mmdb`, `GeoIP.dat`, `GeoSite.dat`, `ASN.mmdb` and `BundleMRS.7z`.
Legacy country-database names are normalized.
`cache.db` and the `proxies/` provider cache point to separate writable core state and are outside this digest.

### Refresh policies

`my.proxy.profileRefreshPolicy` defaults to `strict`.
A changed GUI YAML requires another `clash-on` window.
With `auto`, changed YAML can be imported while proxy intent remains active, after the same input, parser, content-audit and digest checks.
The host configuration selects `auto`.

Unchanged YAML follows the ordinary restart path and reuses the authorized geodata.
GUI database updates alone do not trigger an import.
Managed geodata downloads remain disabled, including replacement of a missing or corrupt database.
`clash-off` revokes authorization and requests direct mode.
GUI updates cannot restart the core while intent is direct.

A validated candidate enters `verifying`.
`profile-refresh-verify.timer` checks every ten seconds and requires at least 60 seconds of observation before accepting a fresh health result for that candidate digest.
Success records `accepted` and establishes the settled baseline.
Consecutive imports during observation retain that baseline.
Failure restores it and its authority, restarts the helper and records `rolled_back`.

With a settled fallback, at least two consecutive fresh full-health failures for the same candidate and policy allow rollback after `my.proxy.profileRefreshFailureGraceSeconds`.
This option defaults to 20 seconds and accepts 20–60 seconds; rollback occurs on the next verifier run.
Missing or stale failure proofs retain the 60-second window, and successful acceptance always requires that full window.
If no settled baseline exists, the failure is recorded as `refused` and the health gate continues protecting the host.
Use `proxy-status` to confirm service recovery.
The helper unit being active alone does not prove core readiness.
GUI delay history can be empty after a core restart, and C also blocks GUI latency requests.
Readiness uses fresh mixed-port and TUN requests, independently of the GUI delay list.

At startup, candidate read or validation failures under `auto` preserve an intact authorized snapshot, record `fallback` and start the core from that snapshot.
Without a valid fallback, startup fails with exit 78.
The launcher always runs the effective snapshot.
Rejected candidate digests are pinned for one hour, so ordinary restarts reuse the rejection without extending its lifetime.
Controlled sources with automatic approval enabled recheck audit rejections against the current policy.
Within the current boot, controlled sources retain health-failure pins across `clash-on` and `clash-off` for the remaining TTL, preventing the same failed candidate from being approved again.
Both commands cancel pending health checks. Compatibility GUI imports also clear their pins.

`/run/proxy-mode/profile-refresh.json` is published atomically with mode 0644.
It contains the policy, latest result, candidate and previous digests, fixed failure reason, snapshot time, last acceptance time, content-audit categories, last automatic approval and up to 20 history entries.
Displayed digests use 12 characters.
The record excludes subscription URLs, credentials, node names, private paths and raw parser output.

### Content audit

`my.proxy.subscriptionAudit` supports `off`, `warn` and `enforce`, with `warn` as the module default.
The host configuration selects `enforce`.
The worker runs offline as `clash-validate`, without capabilities, after `-t` and before snapshot commit.
`off` disables only this additional check.
The managed parser and digest checks always apply.

The audit checks node server addresses, unauthorized `skip-cert-verify`, empty groups, invalid group references, YAML key structure, hosts mappings and provider configuration changes.
Historical comparisons use the last settled YAML.
Mutable provider contents retain the separate trust model described below.
Named TLS exceptions in `my.proxy.insecureProxyExceptions` remain valid.
Intentional non-public node endpoints require `my.proxy.subscriptionAuditServerCidrs`.
This audit exception grants no application access, and multicast endpoints remain refused.

`my.proxy.remoteOnlyCatchAll` defaults to `false` and is enabled on this host.
The Merge script follows the final catch-all policy and its group references, removing DIRECT and other non-proxy members.
It supports arbitrary subscription group names and refuses empty or cyclic groups.
Shared groups lose those manual choices.
Explicit DIRECT rules and unrelated groups remain available.
Dynamic provider routing still requires a conclusive audit under `enforce`.
The constraint applies when the GUI generates YAML.
Reapply the current profile after changing Merge policy to regenerate it before requesting a fresh import.

Hosts mappings require an explicit root allowlist on every import, including the first.
Each supplied name and value must match `my.proxy.subscriptionAuditAllowedHosts`.
Entries may be omitted.
The default empty allowlist permits no subscription hosts overrides.

```nix
my.proxy.subscriptionAuditAllowedHosts = {
  "service.internal" = "192.168.1.10";
};
```

`my.proxy.sensitiveDomains` enables targeted checks for user-selected domain suffixes and defaults to an empty list.
The checker supports ordered `DOMAIN`, `DOMAIN-SUFFIX`, `DOMAIN-KEYWORD` and `MATCH` rules with statically resolvable groups.
It identifies proven DIRECT-to-proxy or DIRECT-to-reject changes.
Mixed routes below a suffix conservatively produce `unknown`, as can GEOSITE, GEOIP, logical rules and dynamic groups.
This is not a complete Mihomo routing evaluator.
Infrastructure targets are not automatically added to this list.

`my.proxy.subscriptionAuditProtectedRoutes` declares the expected `direct`, `proxy` or `reject` route for exact domains.
Health and delay-test domains default to `proxy`.
Declare subscription and update domains here with their intended routes.
Indeterminate rules or groups cannot establish compliance.

`enforce` rejects deny results and unknown routing for sensitive domains or protected targets, using `blocked by audit` or `audit indeterminate`.
A worker failure also prevents acceptance under `enforce`.
`warn` records findings and continues the import.
Structural warnings cover large rule or node count changes, concentration on one host, new plaintext credential nodes and broad geographic routing changes.
Public findings contain fixed categories and counts.

## Managed profile and controller

[profile.nix](../system/config/network/proxy/profile.nix) is the root policy.
[cvr-merge.nix](../home/config/clash/cvr-merge.nix) generates the matching GUI Merge configuration.
The final core parser enforces listeners, mark, DNS, TUN routes/exclusions and private-target settings after GUI/subscription processing.
Rule providers are pinned empty and automatic geodata updates are disabled.
Unconditional `MATCH,DIRECT` and `FINAL,DIRECT` rules are refused.
Subscription rules and group selection otherwise retain their authorized meaning.

The core reports a `managed-policy` digest to health consumers.
This policy digest describes system invariants.
The separate snapshot digest binds authorized YAML and assets.

The root policy also supplies the shared domain-filter store path, additions and exceptions.
The core checks DNS before hosts/cache/fake-IP and application targets before TCP/UDP dialing, so delegated HTTP/SOCKS resolution uses the same list as system DNS.
Internal bootstrap resolver queries use the filter too.
Their transport and target-validation scope remain separate.
See [DNS](dns.md) for split DNS, native MagicDNS and literal-IP boundaries.

| Controller operation | Managed behavior |
|---|---|
| GET / HEAD / OPTIONS | Allowed, including GUI delay tests |
| PUT / DELETE `/proxies/<name>` | Select or release a proxy choice |
| DELETE `/connections` or `/connections/<id>` | Close connections |
| PATCH `/configs` | Only `mode=rule`, `mode=global` or no change |
| Other writes | Forbidden by default |

Configuration loads, rule disabling, provider/geodata/storage changes, restart and upgrade APIs are forbidden.
PATCH validates canonical upstream field names and duplicate keys before applying the same decoded schema.
New controller write endpoints require an explicit policy decision when upgrading the core.

## Subscription trust in compatibility mode

`my.proxy.subscriptionTrust="source"` accepts proxy providers as trusted, mutable sources.
`"fixed"` refuses providers so inline node contents belong to the authorized YAML snapshot.
`proxy-control preflight` reports inline-node and provider counts, source hosts and requested certificate exceptions without subscription URLs or credentials.

The global merge script filters inline nodes with `skip-cert-verify` enabled and removes their group references.
Exact names in `my.proxy.insecureProxyExceptions` are kept.
An emptied group without provider members stops configuration generation.
The core applies the same exception list after per-profile extensions and provider overrides.

Certificate exceptions match the leaf name.
A provider replacing a node's server under the same name retains that exception.
`remoteTargetGrants` with mutable providers additionally requires `allowDynamicRemoteGrants=true`.
Fixed mode binds inline node contents to each authorized import.

Preflight checks the generated profile, assets and managed parser.
It reports certificate-policy rejections separately and records a sanitized preparation summary.
Provider contents are checked by the core during loading and refresh.
The snapshot's root-owned `proxies/` link points to writable `/var/lib/clash-core/proxies/`.
HTTP providers use that directory by default.
Explicit provider paths must remain relative to `proxies/`.

HTTP providers try their cache first, fetch remotely after a cache rejection, and retain their last accepted nodes and cache on a rejected refresh.
Configured fallback payloads remain available during initialization failures.
Providers with a positive update interval retry automatically.

Fast snapshot checks compare protected file identities, permissions, sizes and ctime/mtime against the last full verification.
Authorization changes, changed metadata or a 60-second interval require a complete SHA-256 pass.
Startup/import always performs full verification.
A sandbox without an observable boot identity also requires full verification.
Snapshot files and the verification record are writable only by trusted root.

## Routing policy

```nix
my.proxy.proxyOnly = false; # Default: subscription Rule routing.
# my.proxy.proxyOnly = true; # Use the subscription's final proxy catch-all.
```

The default preserves domestic DIRECT and prepends proxy rules for the configured probe/download domains and the registered IP-echo/split-routing domains (`my.proxy.probeDomains`), so a routing check does not see a direct connection.
Other destinations follow the subscription rules.
Health probes use the configured sample targets.

With `true`, the generated profile routes application destinations reaching the core through the subscription's final proxy MATCH policy.
It requires a usable proxy catch-all and overrides geographic/process splitting and profile REJECT rules.
It also refuses application DIRECT/COMPATIBLE TCP/UDP dials inside the core, including direct leaves behind groups and renamed adapters.
Rule-routed DNS is covered.
Explicit bootstrap/endpoint-resolution infrastructure and `#DIRECT` MagicDNS remain exempt.

Deploy the generation and import/restart the managed core for this policy to take effect.
It increases subscription traffic, including domestic traffic.
`bootMode`, A's portal access, root UDP, multicast and private host paths outside the core are independent choices.
For routing-policy upgrade checks, see [maintenance](maintenance.md#source-compatibility-checks).

## Domain targets

Managed sniffing is disabled in both Rule and uniform proxy modes.
Explicit target names and fake-IP mappings remain the routing identity.
An ECH cover SNI cannot replace the dial target.
Literal-IP traffic uses IP rules.
The parser also normalizes cached GUI profiles to this policy.
See [browser ECH discovery](browser.md#ech-discovery-and-proxy-compatibility).

[target_guard.go](../system/programs/clash/mihomo/guard/target_guard.go) validates application targets before TCP/UDP egress.
Public names must resolve locally to nonempty, entirely public answers.
One resolver error gets one retry.
TCP interleaves validated IPv4/IPv6 candidates and attempts at most eight literal addresses at the selected leaf, with a two-second attempt ceiling and six-second total deadline.
Each candidate requires that leaf’s egress authorization.

The node receives a pinned IP and performs no target-name lookup.
UDP pins one target for its association.
Internal suffixes in `privateTargetDomains` and the authenticated MagicDNS suffix permit private answers, but still require local resolution, pinning and egress authorization.
They do not create a resolver route or bypass the shared domain filter.

The core tries the next candidate when dialing returns an error.
A successful dial hands the connection to the application, which sends its data through that connection.
For optimistic or one-way proxy protocols, tunnel establishment is the dial success point.
Candidate fallback ends there.

### Local and remote authorization

Authorization runs at the actual selected leaf, including nested, fallback and load-balancing groups.
A name such as `DIRECT` does not confer direct authority on a remote adapter.
Rule and Global routing use the same checks.
`proxyOnly` still independently refuses application DIRECT.

| Target reaching the core | Local DIRECT | Remote proxy |
|---|---|---|
| Public literal or checked public name | Allowed unless `proxyOnly` refuses DIRECT | Allowed |
| Private/CGNAT/loopback/ULA/link-local literal, or private answer for an approved internal suffix | Must match `localTargetCidrs` and a host inlet | Requires a matching `remoteTargetGrants` entry |
| Listed metadata endpoint, unspecified, multicast, selected reserved/transition range or raw fake-IP | Refused | Refused, including with a grant |
| Known NAT64 translation | Embedded IPv4 must pass public-address checks | Same. A private embedded address cannot be granted |

`localTargetCidrs` defaults to the shared local/private ranges.
Host inlets are core metadata types `HTTP`, `HTTPS`, `SOCKS4`, `SOCKS5`, `REDIR` and `TUN`.
Guest `TPROXY` has no default local grant.
Setting `localTargetCidrs=[]` removes this core permission.
It does not alter native host firewall grants, guest forwarding or A's direct Gost passthrough.

Remote grants default to empty.
An intentional service in a node's private network needs an exact leaf name, canonical internal CIDR, core inlet, transport and destination ports:

```nix
my.proxy.remoteTargetGrants = [{
  proxy = "Office node";
  cidr = "10.20.30.40/32";
  inlets = [ "SOCKS5" ];
  network = "tcp";
  ports = [ 443 ];
}];
```

The grant authorizes the IP as interpreted by that node.
It grants no remote DNS lookup and no other node, inlet, transport or port.
For a name, also declare its internal suffix and provide an answer through authorized hosts or the core's resolver.
A grant matches the configured leaf name.
It does not authenticate a remote server.
Use trusted profile/provider sources and avoid reusing a granted name for another node.
Inlets describe what the core actually receives: Gost's forwarded CONNECT is `HTTPS`, including traffic relayed from its transparent inlet.
They do not authenticate an application or preserve its original UID.

Each new destination in a reused UDP association is validated and pinned against the association's actual leaf.
Cached mappings retain that provenance and recheck the current inlet, destination port and egress grants on every packet.
Changing ports cannot reuse a narrower grant.
Established TCP connections are not retroactively inspected.
Rebuild and deliberately import/restart the core when changing policy.

### Address classes and modes

IPv4-mapped addresses are normalized before authorization.
The non-grantable metadata list covers `169.254.169.254`, `169.254.170.2`, `100.100.100.200` and `fd00:ec2::254`.
Scoped IPv6 literals with a zone are refused.

The well-known `64:ff9b::/96` is always recognized.
`nat64Prefixes` declares additional [RFC 6052](https://www.rfc-editor.org/rfc/rfc6052.html) translation prefixes of length 32, 40, 48, 56, 64 or 96.
Overlapping declarations use the most specific prefix.
Public embedded IPv4 remains usable, while private, metadata and synthetic embedded targets fail closed.
The local-use `64:ff9b:1::/48` is refused unless explicitly declared as a translator.

The configured fake-IP pool is a routing identity, never an egress destination.
A live mapping must first recover its domain, which then passes normal filtering, resolution and authorization.
Missing mappings and real DNS answers containing synthetic targets are refused, including with a customized pool.

These rules apply to application traffic reaching the core, usually in B.
In A, Gost and native sockets follow the A firewall policy and retain literal HTTP portal access.
C blocks ordinary applications before the core, so local and remote grants cannot reopen access.

Internal bootstrap, proxy-endpoint transport and `#DIRECT` MagicDNS have separate infrastructure permissions.
Rule-routed DNS does not inherit them.
Native LAN and guest forwarding also have separate policies.
The core can administer only its private network namespace.
Root services administer the host.

## Gost lifecycle

[default.nix](../system/programs/gost/default.nix) follows the mode's atomic state.
It reaps the previous process before binding, avoids `SO_REUSEPORT`, requires exactly one listener per endpoint and limits descriptors, memory and tasks.
In B it relies on the supervisor's health decision and rechecks the mixed listener identity.
It uses passive socket observations rather than connecting to the transparent inlet, with a self-loop detector as a backstop.

The relay also accepts legacy bare numeric IPv6 CONNECT targets with valid ports while retaining Go's HTTP framing and buffered tunnel bytes.
Prefer standard `[IPv6]:port` syntax.
Gost is invoked by store path, not installed on the system PATH or in the initrd.
See [Patch maintenance](maintenance.md) for all source and packaging customizations.
