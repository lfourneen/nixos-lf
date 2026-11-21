# Network patch and customization maintenance

English | [简体中文](CN/maintenance_CN.md) | [Documentation](README.md)

The network stack includes local patches to Mihomo, sing-tun, Gost, Clash Verge and dnsmasq, plus a managed Firefox wrapper.
This guide identifies their source files and the compatibility checks needed when updating them.

## Build integration

[flake.lock](../flake.lock) pins package sources.
Stable Nixpkgs supplies Gost, dnsmasq, Unbound and Firefox.
Unstable supplies Mihomo, the Clash Verge GUI and its separately packaged helper.
GUI, helper and core versions are independent.

[clash-verge.nix](../system/programs/clash/clash-verge.nix) builds the customized core/helper and places them in the GUI package.
[default.nix](../system/programs/gost/default.nix) builds the relay package.
Their `lib.fileset.toSource` inputs include only patchers, custom Go code and tests, so unrelated checkout changes do not rebuild those Go packages. **Add any new patch payload/test to the corresponding fileset.** A file present in the checkout but absent from the source set never reaches the builder.

Go patchers run in `postConfigure`, after `buildGoModule` has populated `vendor/`.
The helper's Rust patch runs in `postPatch`, appending to inherited Nixpkgs packaging.
Source anchors require exact matches.
A build failure after an upgrade is a request to compare upstream behavior, not a reason to weaken the anchor or skip a patch.

## Customization inventory

| Target | Repository entry | Purpose / upgrade focus |
|---|---|---|
| Mihomo parser | [patch_mihomo.py](../system/programs/clash/mihomo/patches/patch_mihomo.py), [managed_profile.go](../system/programs/clash/mihomo/config/managed_profile.go) | Final managed-policy enforcement, policy digest, known legacy normalization, no unconditional DIRECT catch-all |
| Mihomo controller | [managed_route.go](../system/programs/clash/mihomo/controller/managed_route.go), [policy_middleware.go](../system/programs/clash/mihomo/controller/policy_middleware.go) | Silent mode/TUN/reload discard and a narrow runtime write allowlist |
| Mihomo egress / DNS / logs | [target_guard.go](../system/programs/clash/mihomo/guard/target_guard.go), `patch_mihomo.py` | Checked/pinned targets, local/remote leaf authorization, optional direct-dial refusal, rule-DNS metadata, actual global-mode route chain and snapshot-only geodata |
| Vendored sing-tun | [patch_sing_tun.py](../system/programs/clash/mihomo/patches/patch_sing_tun.py) | TUN-bound OUTPUT TCP DNAT and guest TPROXY socket preservation |
| Vendored Gost HTTP handler | [patch_gost.py](../system/programs/gost/patches/patch_gost.py), [gost_http.go](../system/programs/gost/src/gost_http.go) | Bare numeric IPv6 CONNECT compatibility with upstream Go HTTP parsing |
| Clash Verge helper | [patch_clash_service.py](../system/programs/clash/service/patch_clash_service.py) | Kernel peer identity for lifecycle ownership, pinned root execution, fixed logs and constrained socket/startup file operations |
| Clash Verge package/launch | [clash-verge.nix](../system/programs/clash/clash-verge.nix), [core launcher](../system/programs/clash/service/clash-verge-core-launcher.sh), [stage tool](../system/programs/clash/service/clash-verge-stage.py), [GUI launcher](../system/programs/clash/gui/clash-verge-gui.py), [GUI defaults](../system/programs/clash/gui/clash-verge-defaults.sh), [control shim](../system/programs/clash/gui/control_shim.sh) | Authorized snapshots, privilege drop, GUI singleton handling and HTTPS delay-test default. OS proxy write interception and control-only snapshot reuse |
| dnsmasq | [default.nix](../system/config/network/network.nix), inline `src/forward.c` replacement | Return terminal strict-order UDP SERVFAIL/REFUSED, including DNSSEC EDE |
| Firefox package | [firefox.nix](../home/programs/firefox/firefox.nix), [guard.py](../home/programs/firefox/guard.py), [sandbox.py](../home/programs/firefox/sandbox.py), [scope.c](../home/programs/firefox/scope.c) | Immutable policy/AutoConfig/fonts, preflight, outer namespaces, filtered bus, portal identity and Landlock/seccomp. No Gecko source patch |
| Unbound / Chrony / networking | [default.nix](../system/config/network/network.nix), [time.nix](../system/config/network/time.nix), [network modules](../system/config/network) | Declarative policy and repository-owned coordinators. No local Unbound/Chrony source patch |
| Link identity | [default.nix](../system/config/network/identity/default.nix), [link_identity.py](../system/config/network/identity/link_identity.py), [partition.nix](../system/hardware/partition.nix) | Coordinated profile identities, private migration/restore, native wired lifecycle and DHCPv6 coexistence. No upstream source patch |
| Firewall lifecycle | [default.nix](../system/config/network/network.nix), [coordinator.nix](../system/config/network/proxy/coordinator.nix), [bootstrap.nix](../system/config/network/firewall/bootstrap.nix) | Exact table ownership, locked atomic replacement, failure retention and explicit table migration |
| Tailscale transport | [transport.nix](../system/config/network/tailscale/transport.nix), [tailscale_contract.py](../system/config/network/tailscale/tailscale_contract.py) | Upstream socket-mark contract enforced by normal system builds. Check mark/fallback/socket behavior and run the real CLI VM on upgrades. No Tailscale source patch |
| Direct egress | [options.nix](../system/config/network/options.nix), [default.nix](../system/config/network/network.nix), [egress_policy.py](../system/config/network/firewall/egress_policy.py) | Actor/socket identity, pre-NAT grants, canonical CIDR validation, scoped time/DoT and shared root/GUI private exclusions. Compare compatibility and restricted modes |
| Guest trust | [policy.nix](../system/config/network/guests/policy.nix), [guest_policy.py](../system/config/network/guests/guest_policy.py), [socket-guest.nix](../system/config/network/dns/socket-guest.nix), [default.nix](../system/programs/virtualisation/default.nix) | Interface/source roles, canonical grants, primary/delegated UID ownership, pre-NAT real-address DNS, bidirectional revocation and rootless launcher |
| Sunshine | [sunshine.nix](../system/programs/sunshine.nix) | Graphical-session Desktop service, local administration and no UPnP. Retain CUDA, verify the exact package in the configured CUDA cache with local builds disabled |

## Mihomo

The shared domain filter lives in [dns_filter.go](../system/programs/clash/mihomo/guard/dns_filter.go), [target_guard.go](../system/programs/clash/mihomo/guard/target_guard.go) and [blocklist.nix](../system/config/network/dns/blocklist.nix).
Its hooks in `dns.newHandler` and `Resolver.ExchangeContext` must precede hosts, fake-IP, cached replies and bootstrap upstream queries, including non-address RR types.
Application targets must pass the suffix filter before private-domain exemptions.

Preserve dnsmasq's longest-suffix and equal-specificity exception semantics and reject unreadable/invalid immutable list data.
Include filter payloads/tests in the package fileset and retain the `./dns` Go tests.

`patch_mihomo.py` inserts the policy at the final `ParseRawConfig`, controller router/PATCH handler, direct adapter, DNS metadata/dialer, TCP dial, actual adapter leaf, UDP mapping, geodata downloader and global route log.
Check **all** of these insertion points on a version bump.
The integration checks exercise policy enforcement at the actual dial and controller write handlers.

The root policy is generated by [profile.nix](../system/config/network/proxy/profile.nix).
[cvr-merge.nix](../home/config/clash/cvr-merge.nix), controller output and [health.py](../system/config/network/proxy/health.py) must agree with it.
New upstream `RawTun` fields require explicit classification in the schema-coverage tests.
Check listener/tunnel fields, defaults and aliases as well as fields exposed by `GET /configs`.
Keep duplicate/noncanonical PATCH rejection and validation/application on the same decoded schema.

Target checks run after routing/sniffing and before the actual TCP/UDP dial.
Checked IPs must remain pinned through a remote proxy.
Internal suffixes and literals must also pass leaf authorization.
Keep this at the actual selected child rather than checking `Unwrap` and dialing a group again.
Reused UDP associations retain the actual leaf and checked target provenance, validate new mappings, and recheck current inlet/port/grants on cached mappings without resolving every later packet again.

[validated_answers.go](../system/programs/clash/mihomo/guard/validated_answers.go) bounds and shares public dial-target lookups when local DNSSEC is enabled.
Keep answer reuse below validator TTLs and isolate cached answers and pending queries by policy.
Replacing a policy must retire its work, while one caller's cancellation must not cancel other consumers.
Preserve distinct-name limits and the shared A/AAAA deadline, with regression coverage for expiry, zero TTLs, failed families and policy replacement.

Preserve mapped-IP, metadata, all RFC 6052 prefix lengths, custom fake-IP and infrastructure-scope tests.
`LFRuleDNS` distinguishes rule-routed DNS from internal bootstrap, so `proxyOnly` cannot accidentally exempt application DNS.
Check metadata, adapter names and group leaves against the direct-dial tests.

Managed mode uses `LF_MANAGED_PROXY_POLICY`.
Its absence deliberately retains upstream behavior for development fixtures.
The production helper must supply it.
Keep policy hashing distinct from the root-owned snapshot digest.
The geodata download gate must also cover parser validation and replacement of a missing/corrupt database.

Managed fake-IP must forward HTTPS/SVCB records with their parameters intact while retaining address synthesis and the shared filter.
Keep sniffing disabled in both Rule and uniform modes.
Cached profiles must inherit the root policy so an ECH public name cannot replace the requested target.

The optional guest TPROXY listener has its own bind-address handling and IPv4 socket selection in `listener/listener.go` and `listener/tproxy`.
Preserve loopback mixed/DNS/controller behavior with `allow-lan=false`, passive TPROXY ownership checks and real TCP/UDP guest tests when updating these paths.

## sing-tun inside Mihomo

The target is `vendor/github.com/metacubex/sing-tun`, not an independently installed sing-tun package.
A Mihomo update can change it even when no explicit sing-tun setting changes.
Inspect Mihomo's `go.mod`, module replacement directives and generated vendor tree.

The patch has two independent parts:

1. `redirect_nftables_rules.go`: in managed non-mark auto-redirect mode, local OUTPUT TCP DNAT uses the TUN's own address instead of loopback. This preserves `SO_BINDTODEVICE(TUN)` sockets with `route_localnet=0`. Other redirect modes retain their upstream behavior.
2. `redirect_nftables_rules.go`: PREROUTING excludes packets marked `0x233` by the owned B-only guest TPROXY rules. Their transparent socket and original target must survive later auto-redirect lookup. The mark is a shared contract with `network.nix`. Ordinary guests cannot transmit a host packet mark over the wire.

Check both IP families, configured exclusions, hook conditions, the guest mark and redirect-port selection.
The guest mark lookup must precede TUN policy rules.
The declared priority is 100.
The firewall's C self-test reply exceptions and the health probe depend on this path.
The control/network VMs exercise kernel rerouting and belong to the sing-tun upgrade check set.

## Gost

The patch targets the vendored `github.com/go-gost/x/handler/http` inside `pkgs.gost`.
On upgrade, check both Gost's version and its `go-gost/x` dependency.
`lfReadRequest` rewrites only a numeric unbracketed IPv6 CONNECT target with a decimal nonzero port.
`net/http.ReadRequest` retains HTTP validation, and the returned reader preserves an already buffered tunnel payload.

Keep bracketed IPv6, ordinary requests, invalid ports/framing and immediate payload cases in [gost_http_test.go](../system/programs/gost/tests/gost_http_test.go).
Also retain the relay's single-owner checks, process reaping and resource limits.
A parser fix does not replace lifecycle protection.
The actual service must use `my.proxy.gostPackage`, not an unpatched package from PATH.

## Clash Verge helper and launchers

The GUI uses the Nixpkgs `.unwrapped` derivation and can reuse its exact store path from the local store or binary cache. A Nixpkgs update can change that path through its dependencies even when the GUI version stays the same.
`clash-verge-service-ipc` is the separately packaged Rust background service. The patched service and Mihomo build from source when their outputs are missing, and the wrapper installs the patched service directly.
Check Nixpkgs's helper derivation and inherited patches too: replacing `postPatch` or copying the wrong helper would discard integration behavior.

The helper patch covers `src/core/manager.rs`, `server.rs`, `logger.rs`, `reconcile.rs` and the vendored Kode Bridge HTTP server.
Keep kernel Unix peer credentials attached to each request. Non-root stop and running-core start requests are silent no-ops; controlled-source starts require root. Missing peer identity or unavailable lifecycle status must not authorize a GUI restart.
On upgrade, enumerate all executable launch, log-writer, socket chmod/unlink and saved-process reconciliation paths.
Managed execution must resolve to `LF_CLASH_CORE_PATH` with root-owned files/ancestors.
Root-owned sticky store ancestors are allowed.
Logs must stay in the private service directory, regardless of caller settings.
Client paths/PIDs must not become root file/process operations.

The common spawn path requires a living child and a connectable fixed controller socket within 15 seconds.
Candidate parsing and the additional audit share an 11-second preparation budget, reserving time to start the core or fall back.
The managed IPC handler has a 20-second budget.
Cancellation and readiness timeout stop the newly spawned process group, including validators, to prevent a late configuration commit.
Preserve this relationship when changing helper or IPC dependencies.

The GUI retains its upstream IPC client and short request timeout.
The helper serializes starts and reuses completed results for identical requests for 30 seconds.
The retry signature includes the fixed GUI file's identity and change timestamps.
A success also requires the same running PID and reachable fixed socket.
Authorized root compatibility imports launch again when input changes. Explicit authorization or a stopped core also requires a new launch.
A GUI caller may only perform the initial authorized compatibility start of a known stopped core.
Root source activation explicitly stops the core before starting its replacement.
An authorized root stop joins the watchdog and persists stopped desired-state, while the helper listener stays available.
A configuration refusal must not trigger a helper restart.

Keep [runtime.nix](../system/programs/clash/service/runtime.nix), [identity.nix](../system/programs/clash/service/identity.nix), the launcher and stage tool consistent.
The top runtime directory's group must match systemd's runtime ownership to avoid recursive reownership on restart.
Do not add the writable core cache to the root helper's `StateDirectory`.
It is created separately.
Preserve snapshot directories across helper restarts, keep authority/validation logs private and check the actual core's five capability sets after the real helper launch.

The startup defaults helper also normalizes `profiles.yaml`: it raises only existing integer `update_interval` values from 1 through 1439 to 1440, leaves `allow_auto_update`, local profiles, comments and unknown fields untouched, and runs under a per-user lock while checking that the GUI is stopped.
The outer launcher holds its lock through normalization until the GUI process is observable.
It never creates a missing `option` or interval.

`clash-verge-stage.py` handles candidate checks, snapshot authority and health acceptance.
[source_refresh.py](../system/programs/clash/service/source_refresh.py) checks registered candidates before restarting a healthy authorized core.
It uses isolated copies and the same unprivileged parser and audit enforcement as import, without writing live caches or granting authority.
Keep candidate, source revision, settled baseline and root policy bindings intact through activation.
Eligible node changes create a root-private five-minute `approval.json`, which the normal approval reader checks again during import.
The record also binds audit and validator policies, and automatic approval never includes structural or unresolved findings.

Startup and rollback check the snapshot's source revision, renderer settings and managed policy, then apply the current audit. Keep older revisions under their original labels for comparison.
Startup and the root timer queue download requests. The coordinator marks a request ready after verified B has remained healthy for 90 seconds.
The stability interval binds the boot, snapshot, policy, routing selection and authorization epoch. Leaving healthy B resets the interval.
The stable blocklist path must still resolve to build-validated immutable content. Static checks repeat on restore, and live health still gates B.
The downloader consumes the ready request and rechecks protection before fetching. C keeps requests pending.
The shared proxy-intent guard only changes intent. Explicit `off` clears the download and application queues.
The apply service binds the downloaded generation to the source selection, registration revision, digest and routing selection.
It retries a busy import outside the operator and source locks. A changed binding discards the request.

Periodic downloads, mapped GUI requests and explicit `add` or `use` release the source publication lock during network waits.
Before publishing, the broker reacquires the lock and rechecks registration and selection. GUI requests also recheck the desktop index against the approved mapping.
Explicit operations in A or B bypass the automatic stability interval and wait for the verifier's result for the downloaded digest. The wait holds neither the operator nor the source lock.

Within the current boot, health-failure pins survive explicit on/off requests for their remaining TTL. Startup imports keep the full 60-second window. Later imports with repeated fresh full failures can roll back after the configured grace. Successful acceptance always keeps the full window.

Existing exact `approval.json` records retain their binding checks.
Source comparison has no approval or activation action.

The authorization lock is released during checks and reacquired to recheck runtime health and bindings.
Pending acceptance or rollback defers the refresh.
The fixed `clash-source-guard.service` transaction enters C before restart, with `CAP_NET_ADMIN` kept outside the download/parser service.
Guard failure must prevent restart. Actual import still validates and audits again, then uses health acceptance or rollback.
Snapshot authority is published under the policy lock, after the authorization lock. The coordinator verifies the transport firewall before releasing the policy lock and binds its health observation to that snapshot digest. Generation or snapshot changes invalidate the observation.

The mode coordinator signals systemd readiness after verifying the initial A or C firewall. Full B health is checked by the running loop, after its dependent units can start. When a switch changes the routing generation in A, the inactive selection resets to the new generation's `rules` policy and proxy intent stays off.

`profile-refresh-verify.timer` runs every ten seconds.
The mode supervisor publishes `/run/proxy-mode/boot-id`, and `refresh.nix` defines the five-minute user notification timer.
The audit worker and status tools use PyYAML supplied explicitly by Nix.
Keep the worker unprivileged, preserve the fixed JSON schema and reason vocabulary, and pass `subscriptionAuditAllowedHosts` from root configuration rather than deriving permissions from a subscription.

Nixpkgs currently exposes the underlying core as `.verge-mihomo-wrapped`.
The package replaces public `verge-mihomo` with the authorized launcher.
Inspect wrapper layout on GUI/package updates.
GUI process identity must remain compatible with `clash-on/off`, and obsolete same-user singletons must not retain an old package's core path.
HTTPS delay defaults are set through writable preferences.
The launch wrapper intercepts System Proxy writes through gsettings, dconf and KDE helpers, including absolute paths injected by Nixpkgs.
Without mount namespaces, memory GSettings and KDE 5 helper names provide the fallback.
The controller silently discards mode/TUN patches and config reloads. The parser derives TUN from defaults plus root policy.
On GUI upgrades, run `proxy-gui` and `proxy-gui-vm` to check real IPC, page controls, PAC/guard writes, startup/exit cleanup and core continuity against both routing submodes.

## DNS and browser integration

Runtime routing uses two immutable policy/health/audit bundles and one root-selected symlink.
Keep the operator → source → authorization → policy lock order; probes run without the policy lock and recheck the routing epoch and policy digest before committing.
Only a complete mixed TCP, fake-IP TCP, DNS, listener and configuration proof may apply a submode or renew its kernel lease; proxy-only additionally requires real-address DNS through the core validator.
Normal source downloads recheck selection, registration and policy/epoch after releasing the source lock for network waits. Explicit recovery holds the registration lock during its bounded download and keeps C active.
Preserve original Rule data and pinned nodes in the digested `source-data.json`, with separate accepted snapshots per source and submode.
Policy transitions close old core/relay flows, flush resolver caches and retain fake-IP domain mappings across restarts.
A source start waits for the newly started helper's IPC listener; startup delay alone must not restart that helper. Existing-core recovery retains the unit restart for a failed IPC transport.
The separate `lf_routing` table must enforce and verify the declared node tuples even after a base firewall reload; established direct replies grant no exception.
Recovery checks the same expiring tuple on output and on replies. The routing table permits only packets marked by those checks. The VM freezes the recovery command and coordinator to verify kernel expiry on an established connection.
Run `proxy-routing` and `proxy-routing-vm` when changing these paths.

The dnsmasq patch is an inline `--replace-fail` in `network.nix`.
In strict-order retry handling, the final server's UDP SERVFAIL/REFUSED must reach the client, including its EDE.
The condition uses the sent-to array index because server endpoints can repeat.
Check terminal-error, retry and duplicate-server behavior against the new `forward.c`.
Keep the real DNS response tests in the network VM.

Unbound uses unmodified `pkgs.unbound-with-systemd`.
Preserve `--enable-systemd` if introducing a future override: the host unit is `Type=notify`.
Keep DNSSEC/CD behavior, persistent anchor initialization, TLS authentication, reply filtering, stale-cache limits and probe verdicts aligned with [DNS](dns.md).
Chrony changes must preserve NTS requirement and certificate-time checks.

With `localDnssec`, [core-validator.nix](../system/config/network/dns/core-validator.nix) runs separate application and infrastructure validators in the core namespace.
Preserve their independent query/resource budgets, persistent anchors and refusal of client CD bypasses.
Validation and upstream selection are separate: rules application DNS keeps a domestic split, with `geosite:cn` using the domestic validating DoH endpoints directly and other names using Cloudflare/Google DoH routed through `lf-remote` with controlled sources, while node bootstrap retains its independent encrypted resolver.
The root-owned `lf-dns-transport` configuration keeps its own upstream list (`mihomoDomesticValidatingDnsUpstreams`) and must not inherit Mode A's DoT list.
Keep the config-parser and executor transport hooks in `patch_mihomo.py`, complete DNSSEC material, TLS verification and raw forwarding without ordinary cache reuse.
Rules application queries retain this split and complete DNSSEC material. Proxy-only instead binds both validator transports to authenticated literal-address DoH through the pruned remote catch-all, without direct bootstrap. Keep real-address source/backend DNS on its fixed Unix bridge and independent request budget, with no host resolver fallback.
The private TUN UID exclusion must remain paired with the validator's loopback-only query firewall and denial of application access to all validator/transport ports.
Mihomo must validate before public fake-IP synthesis and before pinning a public dial target, clear AD on synthetic addresses, and retain HTTPS/SVCB parameters.
Keep known-profile migration separate from rejection of foreign values and controller weakening.
Run `proxy-assurance-vm` when changing these paths.

Firefox customization wraps the packaged browser with immutable policy, AutoConfig, fonts, preflight and outer confinement.
An update can alter Home Manager's final package/wrapper or preference semantics without any source patch failing.
Check the final launcher and desktop/MIME entries, policy locks, DoH/proxy values, sandbox environment and existing-profile/process behavior with `browser-privacy`.

Browser checks cover three areas:

- `browser-privacy` checks the packaged policies, launcher and preference locks.
- `browser-sandbox-vm` checks a real Wayland browser, system CA trust, file and device access, audio and document portals. It also exercises both installed launchers under fixture routing states, window reuse and cleanup after revoking portal access.
- `browser-ech-vm` and `browser-ech-uniform-vm` check HTTP proxy and transparent paths, HTTPS records, ECH retries and certificate rejection.

For sandbox dependency or kernel updates, review bubblewrap, xdg-dbus-proxy, Landlock, seccomp and portal identity handling.
Keep the per-app document view and host-only instance records.
Device selection must reject regular files and unrelated GPU nodes.
System trust must work through the read-only CA bundle without exposing `/etc/static`.

For Firefox or NSS updates, check the CONNECT gate, native type-65 resolver API and retry behavior against the [browser guide](browser.md#ech-discovery-and-proxy-compatibility).

`dnsEncryption="strict"` must agree across the coordinator, broker and static firewall: no automatic or forced fallback, no plaintext leases, and no fallback-set accept rule.
Retain approved split DNS, MagicDNS and literal portal access.
Test both transport policies.
The default recovery budget remains a separate behavior.

On NVIDIA systems, also check the real Wayland compositor and PipeWire/Pulse backend using a fresh profile.
Compare the same package and privacy policy with and without outer confinement: `about:support` should retain the expected hardware WebRender and DMA-BUF capabilities when NVIDIA access is selected.
Confirm that the EGL platform configuration resolves inside the sandbox.
Hardware video decoding has separate driver and Gecko requirements.
Check decoding separately with the same package and an unsandboxed baseline.

## Firewall ownership and loading

The [firewall lifecycle](network.md#firewall-lifecycle) reserves exact table family/name pairs.
Activation, reload, mode changes and drift repair operate within that ownership boundary.
The deletion list belongs exclusively to `proxy-control migrate-firewall`.
The [migration procedure](operations.md#firewall-table-migration) requires the operator to confirm each target's ownership before deletion.

Preserve the shared policy lock and the single transaction containing static rules plus the safety fragment.
The explicit migration adds its cleanup to that same transaction without leaving persistent cleanup state.
A rejected nft transaction must retain the previous policy, and first-load failure must retain the independent bootstrap baseline.
Stopping the nftables unit must leave protection installed.
Proxy intent must pass through C before a health proof permits B, including when the core remains alive during reload or migration.
Keep `inet lf_policy_guard` independent of `inet lf_filter`. The required chains record traversal in temporary packet priority bits, which are cleared at both ends of each monitored hook. Apply the guard's requirements and the corresponding B or C chains in one transaction.

Validating and HTTP-only configurations try `tunProbeNames` in order.
Synthetic-only TUN configurations use a fresh name instead.
The client DNS probe allows up to three rounds within a 20-second probe budget.
Timeouts and unusable answers trigger another attempt, while exhaustion remains a hard failure.
HTTP-only probes must parse an IPv4 answer rather than accept nonempty diagnostics or a CNAME alone.
Reserve `degraded` for advisories after every hard check passes, and retain generation, boot, freshness and active-alarm checks in readiness.

When authorization leases are enabled, preserve the kernel timeouts and C request/reply guards in all four expiry chains.
Only a fresh complete proof bound to boot, configuration, policy generation and sleep epoch may renew the lease, and it must be consumed once even after an ambiguous apply failure.
Sleep revocation, fresh-proof recovery and cleanup when leases are disabled must remain consistent with readiness and nft event filtering.
`proxy-assurance-vm` covers frozen/stopped supervisors, existing TCP/UDP in both IP families, bidirectional expiry and retained recovery access.

For changes to nftables, systemd loading or coordination, run `proxy-firewall-vm`, the relevant complete-rule variants, `proxy-control-vm` and `proxy-network-vm`.
The dedicated VM checks A/C table ownership, external sets and ordered rules, failed transactions, lock contention and reuse of migration target names.
The control VM checks the real helper/core, preservation of its TUN table, C enforcement and B recovery.
The broad VM retains first-load and drift-recovery coverage.

The [drop summary](operations.md#drop-counters) maps existing chains and nft JSON expressions in [inspect.py](../system/config/network/diagnostics/inspect.py) to reason/role groups.
Preserve that mapping when changing chain names, guest interface order or source/port expressions.
Run `proxy-regressions`, `proxy-control-vm` and `proxy-guest-vm` after changing the summary.
Keep permission/parse errors distinct from zero counters and keep diagnosis independent of enforcement.

## Link identity

[default.nix](../system/config/network/identity/default.nix) and [link_identity.py](../system/config/network/identity/link_identity.py) use upstream NetworkManager/systemd/dhcpcd APIs without source patches.
Check actual nmcli property names and enum formatting, both hostname switches, stable-id substitutions, DHCP identifiers and IPv6 address generation.
Preserve the identity-only backup whitelist, private state, inactive/autoconnect guards and refusal to overwrite manual edits.
Rebuilds do not apply profile targets automatically.

Retain the upstream generated dhcpcd configuration and service hardening when extending its single-interface command, PID file and reload.
Its DHCPv6 socket must coexist with NetworkManager's Wi-Fi client.
Run `network-identity-config`, `network-identity-vm`, `proxy-control-vm` and `proxy-network-vm` together.
The identity VM requires fresh exchanges for each activation, including unchanged MACs.
The [identity guide](identity.md) covers persistence preparation and reauthorization.

## Upgrade workflow

### Source compatibility checks

[sources.json](../system/config/network/maintenance/sources.json) groups the maintained entrypoints, invariants and regression checks by component.
[default.nix](../system/config/network/maintenance/default.nix) fingerprints the selected package sources, available vendored dependency hashes and primary Nixpkgs package recipes.
Normal system builds require these inputs to match the configured source baseline.
A same-version source or recipe change still requires compatibility checks.
A Mihomo change also selects its vendored sing-tun and SOCKS components.

Preview changes to the lock inventory and network baseline from the checkout. Use `nix flake update <input>` to limit an application update to its input. Use `nix build --dry-run .#nixosConfigurations.lfour.config.system.build.toplevel` to see which store paths the candidate system would download and build.

```sh
python3 scripts/check-maintenance.py --plan-pins
```

Generate a detailed network plan for source entrypoints and invariants:

```sh
nix eval --json .#nixosConfigurations.lfour.config.my.proxy.upgradeSources \
  > /tmp/network-upgrade-selected.json
python3 system/config/network/maintenance/upgrades.py plan \
  system/config/network/maintenance/sources.json /tmp/network-upgrade-selected.json \
  > /tmp/network-upgrade-plan.json
```

The plan lists changed components, source entrypoints, invariants and checks.
Add component names such as `mihomo sing-tun socks`, `gost`, `clash`, `dns`, `firefox`, `tailscale`, `firewall` or `link-identity` to request a plan for local customization changes or unchanged inputs.

Execute the selected checks with:

```sh
jq -r '.checks[] | ".#checks.x86_64-linux." + .' /tmp/network-upgrade-plan.json \
  | xargs -r nix build --no-link
```

Review upstream sources and packaging, then run `python3 scripts/check-maintenance.py --sync-pins`. Inspect the version and hash diffs and enter `y` to write the inventory and network baseline. Run the affected component checks, `proxy-upgrade-review` and the candidate system build.
The sync updates only the root inventory when network fingerprints match.
Its schema requires each component's minimum source and regression coverage, and the flake checks that the named checks exist.

The gate checks source versions and fingerprints.
Exact patch anchors, package tests and kernel/browser integration tests check the corresponding behavior.
The recipe fingerprint covers the primary package expression, not the full transitive build closure.
Check the lock diff for other relevant changes, including the deployed kernel and graphics stack.
Keep the component inventory current when adding a patch, dependency or entrypoint.

### Upgrade steps

1. Preserve a working system generation, then update the intended flake input.
2. Compare the lock diff, Nixpkgs package expression, upstream sources and vendored dependencies.
3. Adjust affected patch insertion points. Update filesets and build phases when files move. Retire upstreamed fixes while retaining their regression checks.
4. If a configuration contract changes, update root policy, GUI Merge, health checks, controller behavior and listener identities together.
5. Run the affected component checks from the table below, then build the candidate system. Refresh, content-audit and startup-default changes also need the corresponding control and refresh VMs.
6. After deployment, restart the old browser, run `clash-off` and `clash-on`, and check readiness and application traffic. With `auto`, also check acceptance and rejected-candidate fallback.
7. Update source baselines, the customization inventory and affected guides to match the final configuration.

## Validation

The configuration-copy script has a separate check:

```sh
python3 tests/deployment/test_deployment.py scripts/push-to-dir.sh
```

It uses temporary directories and requires Python, Bash and rsync.

The following checks are exposed as `checks.x86_64-linux.*` in [tests/checks.nix](../tests/checks.nix).
Select checks for the component being changed.
VM checks run in an isolated guest and need KVM access for hardware acceleration.

| `checks.x86_64-linux.*` | Scope |
|---|---|
| `proxy-control` | Temporary-file snapshot authorization, expiry, restart, hostile inputs and permissions, plus single-use B lease proofs and sleep/generation rejection |
| `proxy-refresh` | Offline audit, bounded HTTP checks, local update state and notification deduplication |
| `proxy-source` | Registered fetch/render, read-only comparison, automatic approval bindings, reboot import, candidate prechecks and runtime recovery |
| `proxy-source-config` | Source/browser configuration, private DNS paths and separation of download and policy capabilities |
| `proxy-refresh-vm` | Real auto/enforce helper/core: healthy acceptance, rejected-candidate fallback, pinned restarts, rollback and desired-state recovery |
| `proxy-upgrade-review` | Selected source/dependency/recipe baselines, component coverage, check-name validation and source compatibility checks. The input gate also runs in normal system builds |
| `network-identity-config` | Identity migration boundaries, actual nmcli parsing/hostname restoration, wired variants, service scope and persistence. Target validation also runs in normal system builds |
| `network-identity-vm` | Real wired/Wi-Fi DHCPv6 coexistence, fresh dual-stack identities, UUID/epoch/random rotation, stable/fixed/permanent behavior, profile precedence, restore, retained host keys and per-profile ARP cleanup |
| `proxy-arp-vm` | ARP reply scope and counters, gateway conflicts, B lease revocation, link recovery and IPv6 regression checks |
| `proxy-regressions` | Health/state/rules/DNS leases, bounded client DNS retries, budget and supervisor regressions |
| `proxy-profile`, `proxy-gui` | Generated profile variants, policy/template agreement, GUI defaults, process wrapper and desktop command interception |
| `proxy-gui-vm` | Unchanged production GUI under WebKit automation with a renamed account and custom home: mode/TUN/System Proxy/PAC/guard no-ops, preserved OS state and core PID, no control errors, GUI exit and root routing transactions |
| `proxy-routing` | Boot-scoped immutable selection, full proof and epoch binding, failed preparation, sanitized status and real-DNS deadline/fallback refusal |
| `proxy-routing-vm` | Registered sources and production namespace: bidirectional rules/proxy-only switching, original rules restored, physical-peer node-only evidence, real DNS/source refresh, old replies, guest refusals, idempotence, concurrent selection, off/on, reboot and kernel expiry |
| `proxy-single-egress-vm` | Two controlled exits: rules selector separation, fixed-leaf dual-stack TCP/UDP and DNS observations, actual Firefox and strict inlet, unsupported/lost UDP, old flows, concurrent refresh, uplink/node failure, plus routing and lease regressions |
| `proxy-ip-tunnel-vm` | Dual-stack WireGuard echo, fixed TCP/UDP exit, relay restricted to the declared peer, source acceptance, lease revocation and peer rotation |
| `proxy-rules`, `proxy-egress-rules` | Complete compatibility/restricted mode rules parsed by the build-time LKL nft environment |
| `proxy-firewall-vm` | A/C ownership, C isolation during main table or chain loss and failed repair, external-table preservation, rejected-load rollback and migration |
| `proxy-dns-strict-rules` | Complete strict DNS firewall rules parsed in LKL |
| `proxy-dns-config` | Generated DNS and verifier script syntax across default, strict, dual-stack, IPv6-only and HTTP-only configurations |
| `proxy-dns-strict-vm` | Real helper/core, shared DNS/proxy filtering, strict outage/override/lease and leftover-set refusal, private split permission and literal portal recovery |
| `proxy-assurance-vm` | A/B local DNSSEC, DNS cutover and probe failure, independent DNS budgets, and dual-stack B isolation during rule loss, lease expiry and suspend |
| `proxy-guest-config`, `proxy-guest-rules` | Production/dedicated-account configuration and rootless launcher syntax. Complete mixed-role rules in LKL. Guest validation is also required by normal system builds |
| `proxy-guest-vm` | Three real routed networks, DHCP/DNS, IPv4/ULA, spoofed sources, actual pasta/RootlessKit/subuid sockets, fake-IP refusal, stale DNS conntrack, bidirectional grant revocation and C with a live core |
| `proxy-hermes-containers-vm` | Real legacy/new Docker, socket ACL, offline image, account grants, TCP/UDP, unsupported IPv6 refusal, Hermes host proxy, A/B/C and recovery |
| `proxy-core-netns-vm` | Independent core namespace prototype: TUN/veth, dual-stack nodes, native/SOCKS UDP, DNS/fake-IP, marks/raw packets, host policy ownership, revocation and restart |
| `proxy-egress-config` | Canonical CIDRs and bounded grant/time scopes. Also required by normal system builds |
| `proxy-egress-vm` | Restricted counterpart of the Tailscale VM. Scoped dual-stack private/multicast/DNS/DoT/NTS traffic, UID/interface/port denials, established grant revocation and C |
| `proxy-egress-control-vm` | Restricted policy before real production TUN/NAT, including allowed/denied private destinations |
| `tailscale-transport` | Selected upstream Linux bypass constant and expected magicsock/STUN socket marking paths. Also required by normal system builds |
| `proxy-tailscale-vm` | Real Tailscale listener and CLI, dual-stack UDP/STUN on both uplinks, listener restart and replacement, port reservation, authorization expiry, strict-mode blocking and remote desktop port policy |
| `proxy-control-config` | Production/test identities, helper capabilities and generated listeners agree |
| `proxy-core` | Patched core build and Go tests for the parser, router, target/cache, actual adapter leaf, DIRECT, UDP, DNS, SOCKS framing and sing-tun interval endpoints |
| `proxy-target-vm` | Real helper/core, distinct local/node loopback traps, HTTP/CONNECT/SOCKS, internal-name pinning, scoped remote grant, Rule/Global and A/C behavior |
| `proxy-gost` | Patched relay build and vendored HTTP parser tests |
| `browser-privacy` | Final native browser policy/locks and offline launch behavior |
| `browser-sandbox-vm` | Real Wayland Firefox, system-root TLS and invalid-certificate rejection, parent isolation, audio, portal grants, link reuse, generation/restart and fail-closed proxy lifecycle |
| `browser-ech-vm` | Confined real Firefox/helper/core, native HTTPS discovery, ECH versus GREASE, key/no-ECH retry, untrusted rejection, certificate errors, cover-name target preservation and A/C behavior |
| `browser-ech-uniform-vm` | The same real browser paths and policy consumption with `proxyOnly=true` |
| `proxy-control-vm` | Real production helper/launcher/netns, capabilities, localhost broker, overlap/link/resume recovery, authorization, DNS and bidirectional C revocation |
| `proxy-managed-netns-vm` | Registered TLS sources, preserved core, authority and established TLS connections after rejection, automatic rotation, structural rejection, full readiness, strict inlet, C revocation and source switching in the production core namespace |
| `proxy-uniform-vm` | Production helper/core under uniform proxy policy: fresh TCP/UDP DIRECT refusal, dual-stack UDP, native IPv6 TUN and recovery |
| `proxy-network-vm` | Broad kernel fault injection: modes, DNS, leases, sleep, guests, TPROXY and recovery |

For example, build related packages and quick checks in one invocation:

```sh
nix build --no-link .#checks.x86_64-linux.proxy-core \
  .#checks.x86_64-linux.proxy-gost .#checks.x86_64-linux.proxy-profile \
  .#checks.x86_64-linux.proxy-control .#checks.x86_64-linux.proxy-control-config \
  .#checks.x86_64-linux.proxy-regressions .#checks.x86_64-linux.proxy-gui
nix build --no-link .#checks.x86_64-linux.proxy-control-vm \
  .#checks.x86_64-linux.proxy-network-vm .#checks.x86_64-linux.proxy-target-vm
nix build --no-link .#checks.x86_64-linux.proxy-egress-config \
  .#checks.x86_64-linux.proxy-egress-rules .#checks.x86_64-linux.proxy-egress-vm \
  .#checks.x86_64-linux.proxy-egress-control-vm
nix build --no-link .#nixosConfigurations.lfour.config.system.build.toplevel
```

`proxy-control-vm` and `proxy-managed-netns-vm` exercise the production helper and core namespace.
`proxy-network-vm` covers broader fault injection, while `proxy-core-netns-vm` has a separate prototype topology.

On machines with limited memory, build VM targets one at a time with `--max-jobs 1`.
After activation, check readiness and application traffic on the actual host.
