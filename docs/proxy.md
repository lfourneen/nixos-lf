# Proxy core and permissions

English | [简体中文](CN/proxy_CN.md) | [Documentation](README.md)

## Controlled subscription sources

The host selects `controlledSources=true`, `subscriptionTrust="fixed"`, `profileRefreshPolicy="auto"` and blocking content audit.
The reusable module retains the legacy GUI import path when controlled sources are disabled.
The GUI-specific import and mutable-provider descriptions below apply to that compatibility path.

`clash-source` registers sources through a root-only broker, with one source selected at a time.
Each has its own subscription, providers, revision, approvals and accepted baseline.
`add <id>` reviews and registers a catalog entry, then downloads, selects and verifies it.
`use <id>` performs the same immediate download and verification for an existing registration.
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
| `url` | Latency-test URL: HTTPS, or HTTP on its default port 80, without credentials or fragment |
| `interval` | Integer, 1–86400 seconds |
| `tolerance` | 0–60000 ms |
| `timeout` | 100–60000 ms |
| `max-failed-times` | 1–100 |
| `lazy` | Boolean |
| `expected-status` | HTTP status expression |

`load-balance`, dynamic filters, `rule-providers` and provider filters/overrides are unsupported.
Supported nodes are SS, VMess, VLESS, Trojan, Hysteria2, TUIC, HTTP, SOCKS5 and AnyTLS.
Remote HTTP/SOCKS plaintext authentication is refused.
AnyTLS participates in Rule routing only and is not offered as a fixed egress; the single-leaf validator keeps its existing protocol list.
Nodes that must query an ECH configuration stay out of the system `lf-remote` leaf and out of the fixed-egress candidates: the managed transport DNS travels through that leaf, so asking the leaf to resolve its own ECH loops back and fails with SERVFAIL.
Such nodes remain in the subscription's groups and work as ordinary selections because a non-ECH leaf resolves their ECH. Inline `ech-opts.config` nodes are not affected.

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
The coordinator checks the transport firewall against the staged digest before a health round. In `proxy-only`, this installs the candidate's pinned endpoint tuples while the application lease is revoked.
Without a compatible fallback, C remains in effect.

When the mixed HTTP probe fails, the health check concurrently delay-tests every `lf-remote` member through the core group-delay API (the same path the GUI uses to display latency), ranks the usable results, switches to the best leaf and retries. It refuses only when every member times out. Testing and retries stay inside the health budget; the audit and authorization are unchanged.

`clash-off` revokes runtime authority while preserving the source's accepted baseline.
With automatic refresh, the next `clash-on` prefers that baseline under the current managed policy and records `restored`.
It repeats parser, content-audit and digest checks, and fresh live health still gates B.
The shared blocklist has a stable policy path, so a validated blocklist update does not invalidate the accepted subscription baseline.
The startup update request stays pending during C. After verified B remains healthy for 90 seconds under the same boot, snapshot and policy, the background service consumes the request and starts fetching.
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
The packaged upstream GUI is used without source changes; the Nix package keeps its upstream unwrapped output and substitutes only the launch wrapper, helper and core.
Its System Proxy, TUN and core-mode controls remain clickable and may update local preferences.
They cannot change managed runtime state:

- The launch wrapper starts the GUI with write-discarding helper shims.
  A private mount namespace replaces the absolute gsettings and KDE helper paths that nixpkgs substitutes into the binary; `dconf` and the version-5 KDE helpers are replaced by path shims.
  A memory GSettings backend and the version-5 helper names remain as a fallback when a private namespace is unavailable.
  System-proxy, PAC and proxy-guard writes therefore return success without changing the OS.
  Reads still use the real helpers where the shim can forward them.
- The controller discards `PATCH /configs` for `mode` and `tun` with 204, and discards `PUT /configs` profile reloads with 204 instead of applying them.
  Other configuration fields and all other writes remain forbidden, so a discarded reload never triggers the GUI's restart fallback.
- The managed parser shadows the generated profile's core mode to `rule` and its whole `tun` section with the root policy before final validation.
  A GUI preference cannot make a restart fail validation or change the live TUN.

The GUI may keep and display a different local preference; it is not authoritative.
Use the controller, `proxy-status` or the managed core for actual state.
Missing or corrupt runtime policy cannot grant a control write.
Status reads, managed proxy-group selection, connection closing, delay tests and logs remain available.
The upstream "close connections on mode change" preference can still close connections when clicking a mode control. Disable that preference to preserve sessions; the core-mode request itself is discarded.
The helper uses kernel Unix peer credentials for lifecycle requests. GUI stop requests and starts of an already running core return success without acting.
With controlled sources, only root may request a core start. Closing the GUI leaves the managed core running; use `clash-off` to leave B.

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
Failure restores it and its authority, replaces the core through helper IPC and records `rolled_back`.
The helper listener remains available during replacement. Only an unavailable helper transport triggers a unit restart.

Startup imports retain the full 60-second observation window without early rollback.
For subsequent imports with a settled fallback, at least two consecutive fresh full-health failures for the same candidate and policy allow rollback after `my.proxy.profileRefreshFailureGraceSeconds`.
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
Leaves that enable `skip-cert-verify` without a named exception are dropped only when their source is listed in the Sops-encrypted [system/secrets/whitelist.yaml](../../system/secrets/whitelist.yaml) (`insecure_sources`, one registered source ID per line), sharing the 20% bound with unresolvable nodes. Unlisted sources keep them and the audit or core guard refuses the candidate, so the default tolerance is zero. A missing or malformed whitelist authorizes no sanitizing.
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
Mihomo races eligible addresses when opening an outbound TCP connection. In the core namespace it binds outbound transport to the host-facing veth, `lf-host`.
The managed parser applies both settings to cached profiles, and the GUI cannot change them through the controller.
Rule providers are pinned empty and automatic geodata updates are disabled.
Unconditional `MATCH,DIRECT` and `FINAL,DIRECT` rules are refused.
Subscription rules and group selection otherwise retain their authorized meaning.

The core reports a `managed-policy` digest to health consumers.
This policy digest describes system invariants.
The separate snapshot digest binds authorized YAML and assets.

On restart, a generated profile that differs only in the system-owned `mode` and `tun` fields reuses the accepted snapshot; any other change still needs `clash-on`.
The root policy also supplies the shared domain-filter store path, additions and exceptions.
The core checks DNS before hosts/cache/fake-IP and application targets before TCP/UDP dialing, so delegated HTTP/SOCKS resolution uses the same list as system DNS.
Internal bootstrap resolver queries use the filter too.
Their transport and target-validation scope remain separate.
See [DNS](dns.md) for split DNS, native MagicDNS and literal-IP boundaries.

| Controller operation | Managed behavior |
|---|---|
| GET / HEAD / OPTIONS | Allowed, including GUI delay tests |
| PUT / DELETE `/proxies/<name>` | Select or release a proxy choice in rules. Runtime proxy-only returns 403; root controls exit changes |
| DELETE `/connections` or `/connections/<id>` | Close connections |
| PATCH `/configs` | An empty patch, `mode`, or `tun` is discarded with 204 and never applied. A patch containing any other field returns 403 |
| PUT `/configs` | Discarded with 204; the GUI's reload path cannot replace the system-rendered profile |
| Other writes | Forbidden by default |

Rule disabling, provider/geodata/storage changes, restart and upgrade APIs are forbidden.
Core mode is fixed by the system-rendered profile; the managed parser also pins it to `rule`.
Both the GUI forwarder and the direct controller socket cross the same unconditional gate, independent of policy state.
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

This host enables `my.proxy.runtimeRouting=true`. Select either B routing mode without rebuilding:

```sh
clash-rules
clash-proxy-only
```

Both commands request proxy access, including from A or C. The terminal reports success with the selected mode, or failure with its cause.
They do not open the GUI, send desktop notifications or print the network overview.
Use `proxy-status` for the overview; B includes the requested/applied routing mode, transaction epoch, actual core mode and protocol coverage.
The root interfaces are `sudo proxy-control routing rules` and `sudo proxy-control routing proxy-only`.
Both routing submodes render core `mode: rule`.
That core setting tells Mihomo to evaluate the rendered rule list; it does not select `rules` versus `proxy-only`.
The GUI's Rule/Global/Direct labels are core-mode requests, not routing submode selectors.
Those requests are discarded, so the GUI cannot switch the core away from `rule` or select a routing submode.

`rules` preserves subscription conditions, including domestic DIRECT and profile REJECT, plus managed probe overrides.
Runtime `proxy-only` sends all supported application traffic through one fixed leaf from the accepted source's remote catch-all.
The effective snapshot contains that node and a single-member `lf-remote` selector. Native TCP/UDP, explicit proxies, strict Firefox and both DNS validator transports use this same leaf.
Automatic testing, fallback and GUI selection cannot replace it. Final adapter checks retain DIRECT/COMPATIBLE refusal.
The original rules, groups and all pinned source nodes remain separate, so returning to rules restores subscription routing.

Use `clash-egress` to list candidates, then `clash-egress APPROVED_NODE_ID` with a complete ID from that list.
The root interfaces are `sudo proxy-control egress list` and `sudo proxy-control egress APPROVED_NODE_ID`.
The first strict selection uses the first reachable remote leaf in the catch-all graph unless an exit was already selected this boot.
An exit change runs the same C transaction, closes old TCP streams and UDP associations, and requires fresh complete health and authorization.
A ready selection of the same ID is idempotent. Node failure stays in C without selecting another node.

Exit IDs bind the complete pinned node definition. Health, leases, refreshes and rollback bind `egress_requested` and `egress_epoch` as well as the routing epoch and snapshot.
Off/on and switching to rules retain the strict exit choice for this boot; reboot clears it.
If a source changes the chosen node's address, credentials or transport, the old ID cannot authorize its replacement. Accept the updated source in rules before selecting its new ID.

A system generation can replace the active routing policy or its health artifacts. If the active selection belongs to a previous generation, the proxy stays blocked. Run `clash-rules` to revoke the old selection and start a new rules transaction. Then use `clash-egress` to select a strict exit for the new policy.

`proxy-status --json` reports `capabilities.egress="fixed-leaf"` for the strict selection.
`capabilities.udp_declared` reports the selected adapter's UDP flag when available. UDP is `unsupported` when the adapter lacks it. Continuous UDP coverage otherwise remains `not-probed`. IPv6 targets remain node-dependent, and unsupported requests cannot use another leaf or DIRECT.
`capabilities.remote_nat="not-measured"` keeps the remote boundary explicit: one node can still use different NAT addresses for TCP/UDP or destinations.
A single NAT guarantee requires independent target observations. IPv4 and IPv6 may have separate declared exit addresses.

A switch first installs C and revokes B authorization, stops the old core and relay flows, installs the selected immutable policy, and recreates the core namespace.
Parser, content audit, listener, routing, DNS/TUN and full health checks must pass before B can return.
The command waits for shared readiness and verification. A failed switch retains its requested mode and C; it never chooses A or rules automatically.
Repeating an already ready selection preserves the core.

`clash-off` selects A and keeps the current routing selection. `clash-on` reuses that selection.
A reboot resets the selection to rules; `bootMode` independently controls whether the boot requests A or proxy access.

Proxy-only requires an accepted local source with pinned literal node addresses. A source's first import must complete its ordinary health acceptance in rules before offline strict startup is available.
Node TLS identities remain intact. Neither switching nor strict startup needs a direct DNS lookup or a subscription download.
Application and source-worker DNS use locally validated answers over remote encrypted transports; see [DNS](dns.md#mode-b-managed-mihomo-dns).
LAN, multicast, time synchronization, Tailscale and inbound service replies are disabled in proxy-only. DHCP, NDP and necessary transport errors remain scoped exceptions.
See [strict egress](egress.md#proxy-only-egress).

The reusable module defaults `runtimeRouting=false`. Enabling it requires controlled sources, isolated TUN, local DNSSEC, strict encrypted DNS and a kernel authorization lease.
The older build-time `proxyOnly` option remains available for configurations without runtime routing; it controls core routing and DIRECT refusal, independently of host exceptions.
Keep it false when runtime routing is enabled. The application-proxy transport supports fixed-port TCP/UDP nodes.

### WireGuard exit with real ping

Set `my.proxy.ipTunnel` to use a controlled WireGuard exit in runtime `proxy-only`. System DNS then returns validated real addresses, so both `ping hostname` and `ping IP` can send native echo requests. The core and strict Firefox use a fixed SOCKS5 relay on the same WireGuard peer. The peer forwards ICMP at the IP layer and originates the proxied TCP/UDP connections.

Configure the two ends before selecting the mode.

1. On the peer, authorize the client's tunnel addresses as one IPv4 host route and one IPv6 host route. Enable forwarding for both families and allow related ICMP errors, including IPv4 fragmentation-needed and IPv6 Packet Too Big. Configure forwarded echo and the SOCKS5 relay to use the same public source address per family.
2. Bind the peer's SOCKS5 listener to a public literal address reachable inside the tunnel. This can be the same address as the WireGuard endpoint. Scope inbound access to the WireGuard interface and the client addresses. The SOCKS5 UDP association must advertise the configured relay address and fixed relay port. Register that SOCKS5 node in the source's remote catch-all with `udp: true`. Use a reachable rules-mode node to complete initial source acceptance before starting the tunnel.
3. Declare the endpoint, peer key, client addresses, relay and two controlled public echo targets beyond the peer. Keep the client private key in a root-only runtime file, such as a sops secret.

```nix
my.proxy.ipTunnel = {
  endpointAddress = "<peer public address>";
  endpointPort = 51820;
  listenPort = 51821;
  publicKey = "<peer WireGuard public key>";
  privateKeyFile = config.sops.secrets."wireguard-exit-key".path;
  clientIpv4 = "172.31.77.2";
  clientIpv6 = "fd42:77::2";
  relayAddress = "<relay public address>";
  relayPort = 1080;
  probeIpv4 = "<controlled IPv4 echo target>";
  probeIpv6 = "<controlled IPv6 echo target>";
  mtu = 1380;
};
```

After applying the configuration, use `clash-proxy-only`. `clash-egress` lists compatible leaves on the declared relay. `proxy-status --json` reports `capabilities.icmp="verified-dual-stack"` after the kernel route checks, both echo probes and the existing application and DNS checks pass. Loss of the tunnel leaves C in force. `clash-rules` and `clash-off` remove the WireGuard interface and its routes.

The status reports the current WireGuard health stage, including a route or echo failure by address family. If both echo probes fail, it also reports whether a recent peer handshake was observed. Each address family gets one retry for a missing echo reply within the same health budget. Both must pass to grant or renew the lease. To activate a changed peer or relay while the old policy is selected, use the `clash-rules` recovery transaction before selecting the new exit.

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
The managed parser pins the core to `rule`; the GUI cannot enable Global or Direct routing.
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
