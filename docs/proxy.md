# Proxy core and permissions

English | [简体中文](CN/proxy_CN.md) | [Documentation](README.md)

## Controlled subscription sources

This host enables `controlledSources=true`, `subscriptionTrust="fixed"`, `profileRefreshPolicy="auto"`, and blocking content audits. When controlled sources are off, the reusable module keeps the legacy GUI import path—the GUI-specific import and mutable-provider notes later in this guide apply to that compatibility mode.

`clash-source` registers sources through a root-only broker, keeping one source selected at a time with its own subscription, providers, revision, approvals, and accepted baseline. `add <id>` reviews and registers a catalog entry, then downloads, selects, and verifies it; `use <id>` runs the same immediate fetch and verification for an existing registration. The GUI can only request sources mapped to a root-approved `gui-profile` UID, and the broker downloads the mapped source and rechecks both mapping and registration before selecting it.

An unprivileged worker fetches the HTTPS subscription and registered HTTP providers, pins public endpoint addresses, and renders the config—Nix provides listeners, DNS, TUN settings, and geodata, while providers are expanded into inline nodes. GUI URLs, YAML, scripts, and caches can never authorize a new source.

The Sops `proxy_source` string holds all definitions as `{"sources":[...]}` (legacy single-source JSON still works). `review/register FILE --id ID` picks one entry; running `review` without `--id` lists the catalog, while `register` refuses an ambiguous multi-entry choice. IDs and GUI mappings must be unique. Only the selected entry enters the registration digest—adding another definition neither changes that digest nor approves the new source, and removing a catalog entry doesn't revoke its registered copy.

Downloads enforce standard TLS hostname verification, pinned public socket addresses, exact approved HTTPS redirects, an 8 MiB per-response limit, a 32 MiB combined budget, and a worker deadline. The default User-Agent is `clash.meta`, though an endpoint can register a single-line ASCII `user-agent` up to 256 characters (custom UA and credentials are permanently stripped after a cross-origin redirect). YAML aliases and duplicate keys are rejected.

Node domains are pinned while keeping their TLS identity; up to `max(1, node_count // 5)` unresolvable nodes may be dropped, though non-public answers and rule-referenced empty groups still reject the candidate. The renderer supports `select`, `url-test`, and `fallback` groups whose members are explicit or reference registered providers:

| Field | Accepted value |
|---|---|
| `url` | Latency-test URL: HTTPS, or HTTP on default port 80, without credentials or fragment |
| `interval` | Integer, 1–86400 seconds |
| `tolerance` | 0–60000 ms |
| `timeout` | 100–60000 ms |
| `max-failed-times` | 1–100 |
| `lazy` | Boolean |
| `expected-status` | HTTP status expression |

`load-balance`, dynamic filters, `rule-providers`, and provider filters/overrides are unsupported. Controlled sources also require protected node data channels: HTTP and SOCKS5 require authenticated TLS and are TCP-only (SOCKS5 TLS protects association setup over TCP, while native UDP datagrams stay plaintext); Shadowsocks requires an approved AEAD cipher and shared secret; VMess requires authenticated TLS or VMess AEAD (`alterId: 0`, approved cipher, valid UUID); VLESS requires authenticated TLS on a supported carrier; and Trojan, Hysteria2, TUIC, and AnyTLS use their supported authenticated transports. The source renderer drops unsupported variants and rejects the candidate if no usable node remains or a required route/group breaks, while the managed core rechecks the effective profile (including restored snapshots). AnyTLS participates in `Rule` routing only and is excluded from fixed-egress candidates (the single-leaf validator keeps its existing protocol list). Nodes that need a DNS lookup for their ECH config are also excluded from `lf-remote`, the managed DNS group `lf-dns`, and fixed-egress candidates to prevent circular resolution when a DNS transport leaf dials—they remain usable in subscription groups where another leaf can carry the encrypted lookup, and nodes with an inline `ech-opts.config` stay eligible.

A private manifest records the source ID, revision, response hashes, transformer, policy, rendered YAML, dropped-node count, and geodata, and is included in the snapshot digest. Registrations and approvals live under root-private `/var/lib/clash-source/sources/<id>`, protected by a shared `source.lock` around the atomic `selected` pointer (older single-source state migrates on the next broker run).

Approved snapshots survive switching and reboots. Fetch failures keep the prior selection, while parser, audit, or health failures preserve the authorized runtime snapshot—so `Active source` can temporarily differ from the requested selection until checks finish.

Before interrupting a ready core, the apply service inspects downloaded candidates. If an update only changes formatting or comments—meaning rendered rules in order, nodes, credentials, and assets match the authorized baseline and the source, policy, egress, running core, and **B** readiness all check out—existing connections stay open (a rejected candidate can also leave the authorized core running, and a transient HTTP observation failure won't restart the core for an equivalent update, though the supervisor still needs a fresh full proof to extend the kernel lease; during renewal, the identity relay can use the remaining time on the last committed lease for that generation). Applying an actually changed candidate enters **C** before restarting the core and runs startup checks again. Before a health round, the coordinator verifies the transport firewall against the staged digest (in `proxy-only`, loading the candidate's pinned endpoint tuples while the application lease is revoked). If no compatible fallback exists, **C** stays active. An interrupted routing transaction preserves proxy intent and resets unfinished routing to a verifiable state—retry with `clash-on`.

When mixed HTTP fails in `rules` mode, the health check only tests or rotates DNS leaves—`lf-remote` stays on the user's chosen application exit and fails closed instead of silently switching nodes or changing your public egress IP. A temporary DNS pick is committed only after matching DNS and route checks pass (failure or timeout restores the prior pick, and manual selections win). TUN checks start with the first completed DNS candidate and cancel the remaining lookups once verified. The root controller can also sample leaf sessions over TLS to the configured literal DoH endpoints: when `rules` is healthy and probe time remains, it checks the DNS pick at most once a minute, switching to another DNS leaf that reaches DoH and returns a validated DNS answer if the current one fails (without touching the application exit). DNS answers, mixed HTTP, route proof, and the other checks still determine readiness. `proxy-status` shows a short ID for the observed DNS leaf, reporting explicit auth rejections as `node-auth`, unreachable DoH endpoints as `session-unavailable`, and unresolved validator probes as `validator-dns` when session evidence is inconclusive or a sampled session works (a DoH timeout still allows an HTTP trial for that leaf, and `rules` recovery samples different slices of the authorized list across rounds). **B** can also diagnose the system resolver via an unprivileged desktop UID. During a remote outage, a live `rules` **B** lease can keep carrying traffic already permitted by the rules (`proxy-status` notes remote degradation along with domestic DNS and `DIRECT` route results), with the existing deadline holding until a full proof renews the lease.

If daily Firefox reports a missing server, test system resolution from your desktop terminal with `getent ahosts www.bilibili.com`, then test plain HTTPS with `curl --noproxy '*' -IsS --max-time 6 https://www.bilibili.com/` and check the connection in Verge to see whether it hit `DIRECT` or a proxy chain. To test the identity browser's Unix relay, `CONNECT`, and certificate validation directly, run `curl --unix-socket /run/clash-strict/proxy.sock --noproxy '' -x http://localhost:80 -IsS --max-time 6 https://search.nixos.org/` (these only run on demand).

`clash-off` revokes runtime authority and retires the current routing epoch, leaving **A** with routing shown as inactive while keeping the source's accepted baseline for the next startup. With automatic refresh on, the next `clash-on` prefers an accepted snapshot matching the current source revision, managed policy, and renderer settings (recording `restored`), still repeating parser, content-audit, and digest checks while gating **B** on live health. Because the shared blocklist uses a stable policy path, validated blocklist updates don't invalidate accepted baselines. The coordinator coalesces startup and timer requests (keeping them pending in **C**), and in **B** triggers the downloader after 90 continuous seconds of verified health under the same boot, snapshot, policy, and routing selection. Mapped GUI requests follow the same gate; in **A**, downloads run directly to prepare for the next proxy startup. In **B**, a successful download queues its exact candidate for apply (if another import or rollback is running, the apply service waits and rechecks selection, registration, and digest before retrying), while foreground core startup can finish in parallel; `clash-off` cancels queued downloads and applies. With runtime routing, explicit commands can apply a downloaded candidate in **C** or fetch a fresh one under a short kernel lease (see [recover a registered source](operations.md#recover-a-registered-source)). A first import without an accepted baseline uses the available candidate and the normal health observation window; stopping and restarting never waives structural checks.

Node identity, authentication, and transport changes are compared against each source's own accepted baseline—even across source switches or reboots. This host automatically approves `node_identity`, `node_authentication`, and `node_transport` changes (including logical-server and TLS-name rotation) on both the first `clash-on` after reboot and the root refresh timer, so routine updates need no manual review.

In the reusable module, `my.proxy.subscriptionAuditAutoApprove` defaults to `[]` (disabled). Enabling it requires controlled sources, `profileRefreshPolicy="auto"`, `subscriptionAudit="enforce"`, and an accepted baseline: every deny category must be in the configured list, with no unresolved findings. The audit compares permissions across node names—an alias of an unchanged leaf keeps its classification, while a new leaf without equivalent permissions must satisfy all three approval categories. Private or metadata addresses, cert-verification bypasses, plaintext credentials, unauthorized `hosts` overrides, protected-route changes, sensitive-route changes in either direction, and malformed input still block import. Each automatic approval record lasts five minutes and binds candidate, baseline, source revision, managed policy, validator policy, and audit policy; import repeats parser, approval, and blocking-audit checks before health acceptance or rollback. Setting the option back to `[]` disables auto-approval without relaxing checks on existing records (see [source registration](operations.md#source-registration)).

Source downloads and node-address pinning use the filtered real-address gateway on `127.0.0.1:1056` independently of **B**'s fake-IP answers (only `clash-fetch` and root can query it directly). The worker rejects non-public answers and verifies HTTPS certificates; subscription downloads, pinned node endpoints, and **C** recovery all require native addresses and refuse synthesized addresses under `nat64Prefixes`, though one usable family can stand in when the other is down. Downloads validate the full address set before connecting and retry public addresses within one deadline while preserving HTTPS hostname and cert checks. A controlled source can pin up to two alternate public addresses for a TCP-backed node: a failed dial or a 250 ms delay starts the next attempt (at most two concurrent dials per race), TLS keeps the original node identity and cert policy, and fixed exits can use those alternates when the operator picks an identity that includes them (single-address grants, the WireGuard relay, and native UDP/QUIC keep their fixed endpoints). Website TCP dials apply the same timing to locally validated destination addresses, forwarding data only on the winning connection. If no usable answer remains, refresh fails without falling back to system DNS. Fresh lookups in `rules` use infrastructure Unbound's DoT path (`unboundDotInProxy=false` limits that path to eligible cached or local answers), while runtime `proxy-only` uses the core application validator ([resolver paths](dns.md#restricted-socket-guest-dns)).

## Visibility and trust

| Participant | Information it can observe |
|---|---|
| Browser and destination | Application content; the destination also knows accounts/cookies and exit address |
| Local HTTP proxy/core | CONNECT names, DNS and routing metadata, and plaintext HTTP content |
| Access network / DNS service | Transport addresses and timing; the resolver knows queries it answers |
| Entry/exit operators | Adjacent addresses and flow timing; an exit sees destination metadata and plaintext HTTP |
| Colluding operators | Additional correlations; local health probes cannot establish their honesty |

Status reports `controlled-fetch`, `legacy-unverified`, or unknown provenance separately from mode, routing, and availability, and shows the latest refresh result, audit categories/counts, and the last automatic approval (unique node count and timestamp). HTTPS certificate checks authenticate the download connection, though publisher content signatures aren't checked because no publisher verifier is configured: automatic checks catch identifiable risks, not who initiated a rotation or whether a publisher is authentic (domain diffs can't tell an operator rotation from a malicious swap). TLS protects authenticated content, while ECH and separate profiles do not hide accounts, destination IPs, or timing—an operator can still log or collude.

The desktop controller exposes live core logs and connection metadata (including destination names and proxy chains). The root helper writes core log files under root-only `/var/lib/clash-verge-service/logs`, rotating at 10 MiB across eight files (the system journal has its own retention policy).

## Components and identities

| Component | Default endpoint / state | Identity |
|---|---|---|
| Mihomo | Mixed `127.0.0.1:7897`, DNS `:1053`, TUN `Mihomo` | `clash-core`, UID 982; `NET_ADMIN` and `NET_RAW` inside the core network namespace |
| Clash Verge helper | On-demand `clash-verge.service` | Root with bounded launch/file capabilities; network capabilities confined to the core namespace |
| GUI controller forwarder | `/run/user/<desktop-uid>/clash-verge-rev/verge-mihomo.sock` | Desktop UID, `core` group, no capabilities; socket 0600 |
| Gost | HTTP `127.0.0.1:33332`, transparent `127.0.0.1:33333` and `[::1]:33333` | UID 987, no capabilities |
| dnsmasq / Unbound / dhcpcd | Local DNS / wired DHCP | UIDs 985 / 983 / 986 |

[identity.nix](../system/programs/clash/service/identity.nix) and [runtime.nix](../system/programs/clash/service/runtime.nix) define the core identity and service contract. The root launcher drops privileges via `setpriv`, keeping only those two network capabilities across permitted, effective, inheritable, ambient, and bounding sets. Supervisors talk directly to `/run/clash-verge-rev/core/mihomo.sock`, while a capless desktop-UID `socat` service in the host namespace exposes the GUI path over Unix byte streams without passing file descriptors.

The helper keeps only the privileges needed for identity transitions, directory ownership, and child management, accepting only the packaged root-owned launcher as an executable. Managed logs stay in `/var/lib/clash-verge-service/logs` with fixed rotation, caller-supplied IPC paths never trigger root `chmod`/`unlink` or startup reconciliation, systemd handles child cleanup, and helper private state stays separate from writable core cache in `/var/lib/clash-core`.

At its privileged boundary, the helper enforces fixed execution paths and file-operation rules, and systemd places both helper and core in `lf-clash-core` before execution. The core cannot touch host interfaces, routes, or firewall tables (those belong to fixed root services), while desktop programs sharing the GUI UID can use permitted GUI/controller operations. The upstream GUI is used unmodified—Nix keeps its `.unwrapped` output and swaps only the launch wrapper, helper, and core—so System Proxy, TUN, and core-mode buttons stay clickable and save local preferences, but are silently discarded before touching the OS or managed core:

- **Launch wrapper shims.** A private mount namespace masks the absolute `gsettings` and KDE helper paths that Nixpkgs substitutes into the binary, while `dconf` and KDE 5 helpers are replaced by PATH shims (falling back to a memory GSettings backend and KDE 5 helper names if a mount namespace isn't available). System-proxy, PAC, and proxy-guard writes return success without altering OS settings, while reads pass through to real helpers where possible.
- **Controller write policy.** `PATCH /configs` for `mode` or `tun` and `PUT /configs` profile reloads return `204` without applying, while other fields and writes return `403` (so discarded reloads never trigger the GUI's restart fallback).
- **Managed parser.** Before final validation, the parser pins the generated profile's core mode to `rule` and overwrites the entire `tun` block with the root policy, so GUI preferences can't break validation or alter the live TUN.

Whatever local preference the GUI displays isn't authoritative—check the controller, `proxy-status`, or the managed core for real state (missing or corrupt runtime policy never unlocks control writes). Status reads, managed proxy-group selection, closing connections, delay tests, and logs all work normally; note that Verge's "close connections on mode change" option still closes connections if you click a mode control, so turn it off if you want sessions left alone (the core-mode request itself is discarded either way). The helper checks kernel Unix peer credentials for lifecycle calls: GUI stop requests and starts of an already-running core are silent no-ops, controlled-source starts require root, closing the GUI leaves the core running, and `clash-off` is how you exit **B**.

An explicitly enabled IPv4 guest TPROXY inlet binds `0.0.0.0:7896`, while ordinary mixed/DNS/controller endpoints stay local under `allow-lan=false`. The packaged listener handles this bind separately with IPv4 socket selection, checks its TCP/UDP UID and service cgroup before **B** (guest interface/source authorization stays in the firewall), and core `auto-redirect` preserves the owned guest mark rather than replacing its transparent socket (see [guest networking](network.md#guests-and-containers)).

## Compatibility GUI imports

`proxy-control preflight` validates the desktop profile, required assets, and managed parser without changing network intent. `clash-on` requires preflight to pass, records proxy intent, enters **C** before starting the helper, clears refresh pins and pending health checks, preserves intact snapshot authority, and opens a single-use 300-second import window (which a failed import consumes).

[clash-verge-stage.py](../system/programs/clash/service/clash-verge-stage.py) accepts bounded regular files owned by the desktop user, rejects symlinks, detects concurrent edits, copies YAML and geodata into a root-owned snapshot (`0750` dirs, `0640` files), normalizes the controller path, and runs `-t` as capability-free `clash-validate` (authority records and parser diagnostics stay root-only). Authorized `127.0.0.1` and `::1` TCP/UDP targets reach host loopback through the capless `clash-loopback` broker, which checks peer UID, blocks reserved proxy/DNS ports, preserves original destination ports on UDP interception, and maps port 53 to the host gateway only under opportunistic DNS (strict DNS disables it)—carrying bytes and datagrams, never host socket descriptors, and ignoring other loopback aliases. The snapshot digest covers YAML, geodata bytes (`Country.mmdb`, `GeoIP.dat`, `GeoSite.dat`, `ASN.mmdb`, `BundleMRS.7z`, with legacy country-db names normalized), and absent optional assets, while `cache.db` and the `proxies/` provider cache live in separate writable state outside the digest.

### Refresh policies

`my.proxy.profileRefreshPolicy` defaults to `strict` (requiring a new `clash-on` window for changed GUI YAML); this host sets `auto`, allowing changed YAML to be imported while proxy intent is active once input, parser, content-audit, and digest checks pass. Unchanged YAML follows the normal restart path and reuses authorized geodata; GUI database updates alone never trigger an import, managed geodata downloads stay off (even to replace a missing or corrupt database), and `clash-off` revokes authority and blocks GUI updates from restarting the core.

A validated candidate enters `verifying`, where `profile-refresh-verify.timer` checks every 10 seconds and requires at least 60 seconds of observation before accepting a fresh health result for that digest (`accepted` sets the settled baseline; consecutive imports during observation keep that baseline). Failure restores the baseline and its authority, swaps the core over helper IPC, and records `rolled_back`—leaving the helper listener up unless helper transport itself failed. Startup imports always keep the full 60-second window with no early rollback; subsequent imports with a settled fallback can roll back after `my.proxy.profileRefreshFailureGraceSeconds` (default 20 s, range 20–60 s) once two consecutive fresh full-health failures hit the same candidate and policy. On first import, DNS or controller observation failures keep the candidate in verification while a definitive refusal clears the snapshot and its authority. Confirm recovery with `proxy-status` (an active helper unit alone doesn't prove readiness, GUI delay history can be empty after a restart, and **C** blocks GUI latency checks—readiness uses fresh mixed-port and TUN probes).

At startup under `auto`, candidate read or validation errors keep an intact authorized snapshot (`fallback`), failing with exit 78 if no valid fallback exists; the launcher always runs the effective snapshot. Rejected digests are pinned for one hour (reused across ordinary restarts without extending TTL, though controlled sources with auto-approval recheck audit rejections against current policy). Within the same boot, controlled sources keep health-failure pins across `clash-on`/`clash-off` for their remaining TTL so the same bad candidate isn't approved again; both commands cancel pending health checks, and compatibility GUI imports clear their pins. `/run/proxy-mode/profile-refresh.json` (`0644`) atomically publishes policy, latest result, truncated 12-char digests, fixed failure reason, timestamps, audit categories, last auto-approval, and up to 20 history entries—never URLs, credentials, node names, private paths, or raw parser output.

### Content audit

`my.proxy.subscriptionAudit` supports `off`, `warn` (module default), and `enforce` (used here), running offline as capability-free `clash-validate` between `-t` and snapshot commit (`off` only disables this extra check; managed parser and digest checks always run).

The audit checks node server addresses, `skip-cert-verify`, empty or invalid groups, YAML key structure, `hosts` mappings, and provider config changes against the last settled YAML (`subscriptionAuditProtectedRoutes` enforces required route classes for exact domains even on first import; `sensitiveDomains` compares suffix routes against the accepted profile; mutable providers keep the trust model below). The Sops-encrypted `insecure_sources` allowlist ([Sops allowlists](../system/secrets/whitelist.yaml)) controls early stripping of `skip-cert-verify` leaves before address resolution—combined losses from unresolvable nodes and literal loopback placeholders are capped at `floor(remaining_nodes / 5)` (at least 1). Put one registered source ID per line (blank lines and duplicates are fine; a missing/unreadable file, invalid UTF-8, or malformed ID disables sanitizing for all sources, leaving those leaves for later checks). Intentional non-public node endpoints must be listed in `my.proxy.subscriptionAuditServerCidrs` (which grants no app access; multicast is always refused).

`my.proxy.remoteOnlyCatchAll` (enabled here) tells the Merge script to walk the final catch-all policy and its group references, stripping `DIRECT` and other non-proxy members across any subscription group names while rejecting empty or cyclic groups (shared groups lose those manual choices; explicit `DIRECT` rules and unrelated groups stay intact, and dynamic provider routing still needs a conclusive audit under `enforce`). Because this runs when the GUI generates YAML, reapply the current profile after changing Merge policy before requesting a new import.

Every import—including the first—checks `hosts` mappings against `my.proxy.subscriptionAuditAllowedHosts` (default `{}`, blocking all subscription `hosts` overrides):

```nix
my.proxy.subscriptionAuditAllowedHosts = {
  "service.internal" = "192.168.1.10";
};
```

`my.proxy.sensitiveDomains` (default `[]`) checks user-chosen suffixes across ordered `DOMAIN`, `DOMAIN-SUFFIX`, `DOMAIN-KEYWORD`, and `MATCH` rules with statically resolvable groups to catch proven `DIRECT`-to-proxy or `DIRECT`-to-reject shifts (mixed routes, `GEOSITE`, `GEOIP`, logical rules, and dynamic groups conservatively return `unknown`; this is not a full Mihomo evaluator, and infra targets aren't added automatically). `my.proxy.subscriptionAuditProtectedRoutes` declares expected `direct`, `proxy`, or `reject` routes for exact domains (health and delay-test domains default to `proxy`; add your subscription and update domains here)—each candidate must prove every protected route, and indeterminate rules or groups fail compliance.

Under `enforce`, any deny verdict, worker failure, or indeterminate sensitive/protected route blocks import (`blocked by audit` or `audit indeterminate`). `warn` logs findings and proceeds. Structural warnings flag large rule/node count swings, host concentration, new plaintext credential nodes, and broad geographic routing shifts, publishing only fixed categories and counts.

## Managed profile and controller

[profile.nix](../system/config/network/proxy/profile.nix) defines the root policy and [cvr-merge.nix](../home/config/clash/cvr-merge.nix) generates the matching GUI Merge config. After GUI/subscription processing, the final core parser enforces listeners, mark, DNS, TUN routes/exclusions, and private-target settings (Mihomo races eligible addresses for outbound TCP and binds outbound transport inside the core namespace to `lf-host`), applying them to cached profiles too so the GUI can't alter them through the controller. Rule providers are pinned empty, automatic geodata updates are disabled, and unconditional `MATCH,DIRECT`/`FINAL,DIRECT` rules are refused while other rules and group choices keep their meaning. The core reports a `managed-policy` digest for system invariants alongside the separate snapshot digest binding YAML and assets.

On restart, a generated profile differing only in system-owned `mode` and `tun` fields reuses the accepted snapshot (any other change still needs `clash-on`). The root policy also provides the shared domain-filter store path, additions, and exceptions: the core checks DNS before hosts/cache/fake-IP and application targets before TCP/UDP dialing so delegated HTTP/SOCKS lookups follow the same list as system DNS (internal bootstrap queries use the filter too, with separate transport and target-validation scopes; see [DNS](dns.md) for split DNS, native MagicDNS, and literal-IP boundaries).

| Controller operation | Managed behavior |
|---|---|
| `GET` / `HEAD` / `OPTIONS` | Read access; desktop DNS and delay requests that initiate network traffic require **B** authorization |
| `PUT` / `DELETE /proxies/<name>` | Select or release a proxy choice in `rules`; runtime `proxy-only` returns `403` (root controls exit changes) |
| `DELETE /connections` or `/connections/<id>` | Close connections |
| `PATCH /configs` | Empty patch, `mode`, or `tun` returns `204` without applying; any other field returns `403` |
| `PUT /configs` | Returns `204` without applying (GUI reload cannot replace the system-rendered profile) |
| Other writes | Forbidden by default |

Disabling rules, changing providers/geodata/storage, restarts, and upgrades are all forbidden over the controller. Core mode is pinned to `rule`, both controller sockets enforce the same write policy (the direct Unix socket identifies root recovery probes by peer credentials), and new write endpoints on a core upgrade require an explicit policy decision.

Desktop node, group, and provider latency tests hit the exact HTTPS endpoint in `my.proxy.delayTestUrl` (the controller maps Verge's built-in fallback URL there, caps each dial at 10 s with up to 30 s batch scheduling, counts only successful HTTP responses, and skips built-in direct adapters in group/provider batches). Desktop DNS queries and delay tests require active **B** authorization and are cancelled if it expires or is revoked (in **C**, the desktop can still read controller state and existing results; root recovery DNS queries over the direct Unix socket use configured probe names). The core uses Verge's unified delay (measuring a second request on a reused connection when possible, while recovery probes finish on the first response; cold DNS, socket setup, and first TLS handshakes still affect new app connections). Here, `my.proxy.delayTestUrl` is the `gstatic` endpoint; if a health round proves the primary failed while `my.proxy.delayTestFallbackUrl` succeeds through the expected proxy, desktop tests can use the fallback for that 60-second proof window (Verge displays denied tests as `0`—check `proxy-status` and retest once the lease recovers). Desktop tests share 64 dial slots (64 starts/s, burst 128; up to four concurrent group/provider batches of at most 8 dials each), automatic checks have their own 10 slots (10 starts/s), root probes over the Unix controller have separate slots, and fixed exits stay pinned during delay tests.

## Subscription trust in compatibility mode

`my.proxy.subscriptionTrust="source"` treats proxy providers as trusted mutable sources, whereas `"fixed"` refuses providers so inline nodes are bound to the authorized YAML snapshot. Preflight summarizes counts, source hosts, and cert-verification requests without leaking URLs or credentials (for controlled sources, run `sudo proxy-control preflight` to read root-owned source state).

Outside controlled sources, the global merge script strips inline nodes with `skip-cert-verify` and drops their group references (halting generation if a group with no provider members becomes empty), and the core refuses cert-verification bypasses after per-profile extensions and provider overrides—keep `my.proxy.insecureProxyExceptions` empty and configure valid certs or CAs on nodes (`remoteTargetGrants` with mutable providers also needs `allowDynamicRemoteGrants=true`). Preflight checks profile, assets, and managed parser; providers are checked by the core on load/refresh, with the snapshot's `proxies/` symlink pointing to writable `/var/lib/clash-core/proxies/` (HTTP providers try cache first, fetch remotely on cache rejection, retain their last accepted nodes/cache if a refresh fails, keep fallback payloads during init failures, and retry automatically when their update interval is positive). Fast snapshot checks compare file identities, permissions, sizes, and `ctime`/`mtime` against the last full verification, forcing a full SHA-256 pass on auth/metadata changes, every 60 seconds, on startup/import, or inside sandboxes without a visible boot ID. Snapshot files and verification records are writable only by root.

## Routing policy

This host enables `my.proxy.runtimeRouting=true`, letting you switch between the two **B** routing submodes without rebuilding:

```sh
clash-rules
clash-proxy-only
```

Both request proxy access (even from **A** or **C**) and report either the active mode or the failure reason in the terminal—without opening the GUI, sending notifications, or printing the overview. In **B** and **C**, `proxy-status` shows the routing selection, transaction epoch, observed core mode, and protocol coverage (the root commands are `sudo proxy-control routing rules` and `sudo proxy-control routing proxy-only`). Both submodes render core `mode: rule` so Mihomo evaluates the rendered rule list; Verge's Rule/Global/Direct buttons are core-mode requests (which are discarded), not routing-submode selectors.

`rules` keeps subscription conditions (including domestic `DIRECT`, profile `REJECT`, and managed probe overrides), whereas runtime `proxy-only` pins all supported application traffic—native TCP/UDP, explicit proxies, strict Firefox, and both DNS validator transports—to a single fixed leaf from the accepted source's remote catch-all (auto-testing, fallback, and GUI selection cannot swap it, `DIRECT`/`COMPATIBLE` remain refused, and original rules/groups/nodes are saved separately so switching back to `rules` restores them).

Run `clash-egress` to list candidates and `clash-egress APPROVED_NODE_ID` to pick one (root equivalents: `sudo proxy-control egress list` and `sudo proxy-control egress APPROVED_NODE_ID`). If you haven't picked an exit yet in this boot, the first strict switch uses the first reachable remote leaf in the catch-all graph. Changing exits runs through **C**, closes old TCP streams and UDP associations, and requires fresh health and authorization (re-selecting the same ready ID is a no-op; node failure holds **C** rather than failing over). Exit IDs bind the full pinned node definition (`egress_requested` and `egress_epoch` alongside routing epoch and snapshot), survive `clash-off`/`clash-on` and switching to `rules` within the current boot, and reset on reboot. If a source changes the chosen node's address, credentials, or transport, accept the update in `rules` first and then pick the new ID; similarly, if a system rebuild replaces the active policy or health artifacts and leaves an older-generation selection active, run `clash-rules` to start a new rules transaction and then `clash-egress` to pick an exit for the new policy.

`proxy-status --json` reports `capabilities.egress="fixed-leaf"` in strict mode, with `capabilities.udp_declared` showing the adapter's UDP flag (`unsupported` when missing, `not-probed` otherwise; IPv6 depends on the node, and unsupported requests never fail over). If you need active UDP checks, set `my.proxy.udpHealth.address` and `my.proxy.udpHealth.port` to an approved public literal UDP echo server: in healthy transparent `proxy-only`, a timer sends one nonce every two minutes as an ordinary desktop socket and reports `verified`, `no-reply`, or `error` in `capabilities.udp` (150-second TTL, advisory only—it doesn't affect leases, refreshes, or exit choice). `capabilities.remote_nat="not-measured"` reminds you that a single node may still use different NAT addresses across TCP/UDP or destinations.

Every switch installs **C**, revokes **B** authorization, stops the old core and relay flows, applies the immutable policy, and recreates the core namespace, waiting for parser, content-audit, listener, routing, DNS/TUN, and full health checks before returning **B** (failures hold the requested mode and **C** without falling back to **A** or `rules`; repeating an already-ready selection keeps the running core if the snapshot still matches). `clash-off` switches to **A** while remembering the routing choice for the next `clash-on`, and rebooting resets to `rules` (`bootMode` independently decides whether boot starts in **A** or requests proxy).

`proxy-only` requires an accepted local source for the current revision with pinned literal node addresses—complete health acceptance in `rules` before your first offline strict startup on a new revision. Switching and strict startup need no direct DNS or subscription download (app and worker DNS use locally validated answers over remote encrypted transports; see [DNS](dns.md#mode-b-managed-mihomo-dns)), while LAN, multicast, time sync, Tailscale, and inbound service replies are disabled (leaving only scoped DHCP, NDP, and necessary transport errors; see [strict egress](egress.md#proxy-only-egress)). In the reusable module `runtimeRouting` defaults to `false` (enabling it requires controlled sources, isolated TUN, local DNSSEC, strict encrypted DNS, and a kernel lease; keep legacy build-time `proxyOnly=false` when using runtime routing).

### WireGuard exit with real ping

Setting `my.proxy.ipTunnel` switches runtime `proxy-only` to a controlled WireGuard exit: system DNS returns locally validated real addresses so both `ping hostname` and `ping IP` send native ICMP echo requests, while the core and strict Firefox use a fixed SOCKS5 relay on the same WireGuard peer (which forwards ICMP at the IP layer and originates proxied TCP/UDP). Configure both ends before switching:

1. On the peer, route the client's IPv4 and IPv6 tunnel addresses as host routes, enable dual-stack forwarding, allow related ICMP errors (IPv4 fragmentation-needed and IPv6 Packet Too Big), and use the same public source address per family for forwarded echo and the SOCKS5 relay.
2. Bind the peer's SOCKS5 listener to a public literal address reachable inside the tunnel (which can match the WireGuard endpoint), restrict inbound access to the WireGuard interface and client addresses, advertise the configured relay address and fixed port in SOCKS5 UDP associations, and register the node in the source's remote catch-all with `udp: true` (keeping another reachable node in the catch-all for initial source acceptance and `rules` mode).
3. On the client, declare the endpoint, peer key, client addresses, relay, and two controlled public echo targets beyond the peer, with the client private key in a root-only runtime file (e.g. a sops secret):

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

After applying, run `clash-proxy-only` (`clash-egress` lists compatible leaves on that relay). Once kernel routes, both echo probes, and the normal app/DNS checks pass, `proxy-status --json` reports `capabilities.icmp="verified-dual-stack"` (tunnel failure holds **C**; `clash-rules` and `clash-off` tear down the WireGuard interface and its routes). Status shows the WireGuard health stage by family (and whether a recent peer handshake was seen if both probes fail); each family gets one retry within the health budget, and both must pass to grant or renew the lease. To pick up a changed peer or relay while an older policy is selected, run `clash-rules` first before selecting the new exit.

## Domain targets

Managed sniffing is disabled in both `Rule` and uniform proxy modes: explicit target names and fake-IP mappings supply domain routing identity (an ECH cover SNI never replaces the dial target), literal IPs use IP rules, and the parser normalizes cached GUI profiles to match (see [browser ECH discovery](browser.md#ech-discovery-and-proxy-compatibility)).

[target_guard.go](../system/programs/clash/mihomo/guard/target_guard.go) validates application targets before TCP/UDP egress. Public names must resolve locally to non-empty, all-public answers (one retry on resolver error). TCP interleaves validated IPv4/IPv6 candidates and tries up to eight addresses at the selected leaf (2-second per-attempt cap, 6-second total deadline), with each candidate checked against that leaf's egress policy—the node receives a pinned IP and never resolves the target name itself, while UDP pins one target per association. `privateTargetDomains` and the authenticated MagicDNS suffix allow private answers, but still require local resolution, pinning, and egress authorization (they don't create a resolver route or bypass the shared domain filter). The core tries the next candidate on dial errors and hands off the connection on the first success (for optimistic or one-way proxy protocols, tunnel establishment counts as dial success and ends fallback).

### Local and remote authorization

Authorization runs at the actual selected leaf (including nested, fallback, and load-balancing groups). Naming a remote adapter `DIRECT` confers no direct authority, the managed parser pins `rule` mode (so the GUI can't enable Global or Direct), and `proxyOnly` independently blocks application `DIRECT`.

| Target reaching the core | Local DIRECT | Remote proxy |
|---|---|---|
| Public literal or checked public name | Allowed unless `proxyOnly` refuses `DIRECT` | Allowed |
| Private/CGNAT/loopback/ULA/link-local literal, or private answer for an approved internal suffix | Must match `localTargetCidrs` and a host inlet | Requires a matching `remoteTargetGrants` entry |
| Listed metadata endpoint, unspecified, multicast, selected reserved/transition range, or raw fake-IP | Refused | Refused, even with a grant |
| Known NAT64 translation | Embedded IPv4 must pass public-address checks | Same; a private embedded address cannot be granted |

`localTargetCidrs` defaults to shared local/private ranges for host inlets (`HTTP`, `HTTPS`, `SOCKS4`, `SOCKS5`, `REDIR`, `TUN`; guest `TPROXY` has no default local grant). Setting `localTargetCidrs=[]` removes that core permission without changing native host firewall grants, guest forwarding, or **A**'s Gost passthrough.

Remote grants default to empty. To expose an internal service behind a proxy node, specify the exact leaf name, canonical CIDR, core inlet, transport, and ports:

```nix
my.proxy.remoteTargetGrants = [{
  proxy = "Office node";
  cidr = "10.20.30.40/32";
  inlets = [ "SOCKS5" ];
  network = "tcp";
  ports = [ 443 ];
}];
```

This authorizes the IP as interpreted by that node—nothing else (no remote DNS, other nodes, inlets, transports, or ports; for a hostname, declare its internal suffix and provide an answer via authorized `hosts` or the core resolver). Because grants match leaf names rather than authenticating remote servers, only use trusted sources and avoid reusing a granted name on another node. Remember that inlets reflect what the core actually receives (Gost's forwarded `CONNECT`—including from its transparent inlet—arrives as `HTTPS`, without authenticating the app or preserving its UID). Each new destination in a reused UDP association is validated and pinned against the association's leaf, and cached mappings recheck inlet, destination port, and egress grants on every packet so switching ports can't reuse a narrower grant. Established TCP connections aren't retroactively checked—rebuild and re-import/restart the core after policy changes.

### Address classes and modes

IPv4-mapped addresses are normalized before checks, zone-scoped IPv6 literals are refused, and the non-grantable metadata list covers `169.254.169.254`, `169.254.170.2`, `100.100.100.200`, and `fd00:ec2::254`. Well-known `64:ff9b::/96` is always recognized, and `nat64Prefixes` can declare extra [RFC 6052](https://www.rfc-editor.org/rfc/rfc6052.html) translation prefixes (lengths 32, 40, 48, 56, 64, 96; most specific wins)—embedded public IPv4 is usable, while private, metadata, and synthetic embedded targets fail closed, and `64:ff9b:1::/48` stays refused unless explicitly declared. The fake-IP pool is strictly a routing identity: a live mapping must recover its domain first, then pass filtering, resolution, and authorization (missing mappings and real DNS answers with synthetic targets are refused, even for custom pools).

These rules govern application traffic reaching the core (typically in **B**). In **A**, Gost and native sockets follow **A**'s firewall policy and keep literal HTTP portal access; **C** blocks ordinary apps ahead of the core, so neither local nor remote grants can reopen access. Internal bootstrap, proxy-endpoint transport, and authenticated MagicDNS have separate infrastructure permissions that rule-routed DNS doesn't inherit, native LAN and guest forwarding stay separate, and the core only administers its private network namespace while root services run the host.

## Gost lifecycle

[relay.nix](../system/programs/gost/relay.nix) follows the mode's atomic state—reaping the previous process before binding, avoiding `SO_REUSEPORT`, requiring one unique listener per endpoint, and bounding descriptors, memory, and tasks. In **B**, it trusts the supervisor's health verdict and rechecks the mixed listener identity using passive socket checks (with a self-loop detector as backstop) rather than connecting to the transparent inlet. It also handles legacy bare numeric IPv6 `CONNECT` targets with valid ports while preserving Go's HTTP framing and buffered tunnel bytes (prefer `[IPv6]:port` where possible). Gost is invoked by store path—never placed on `PATH` or in the `initrd`—and all source/packaging tweaks are listed in [Patch maintenance](maintenance.md).
