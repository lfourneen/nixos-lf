# Network patch and customization maintenance

English | [简体中文](CN/maintenance_CN.md) | [Documentation](README.md)

The network stack carries local patches for Mihomo, `sing-tun`, Gost, Clash Verge, and `dnsmasq`, along with a managed Firefox wrapper. This guide maps out where those customizations live and what to check before bumping dependencies.

## Build integration

[flake.lock](../flake.lock) pins the upstream sources: Stable Nixpkgs supplies Gost, `dnsmasq`, Unbound, and Firefox, while Unstable supplies Mihomo, the Clash Verge GUI, and its separately packaged helper (GUI, helper, and core versions move independently).

[clash-verge.nix](../system/programs/clash/clash-verge.nix) builds the patched core/helper and wraps them into the GUI package, and [package.nix](../system/programs/gost/package.nix) builds the relay. Both use `lib.fileset.toSource` to pull in only patchers, custom Go files, and tests so unrelated repo edits don't trigger Go rebuilds—**remember to add any new patch file or test to the matching fileset**, otherwise the builder won't see it. Go patchers run in `postConfigure` after `buildGoModule` populates `vendor/`, and the helper's Rust patch runs in `postPatch` on top of Nixpkgs's own patches. Anchors require exact matches; if an upgrade breaks an anchor, compare upstream behavior instead of loosening the match or skipping the patch.

## Customization inventory

| Target | Repository entry | Purpose / upgrade focus |
|---|---|---|
| Mihomo parser | [patch_mihomo.py](../system/programs/clash/mihomo/patches/patch_mihomo.py), [managed_profile.go](../system/programs/clash/mihomo/config/managed_profile.go) | Final managed-policy enforcement, policy digest, known legacy normalization, no unconditional `DIRECT` catch-all |
| Mihomo controller | [managed_route.go](../system/programs/clash/mihomo/controller/managed_route.go), [policy_middleware.go](../system/programs/clash/mihomo/controller/policy_middleware.go) | Silent mode/TUN/reload discard and a narrow runtime write allowlist |
| Mihomo egress / DNS / logs | [target_guard.go](../system/programs/clash/mihomo/guard/target_guard.go), `patch_mihomo.py` | Checked/pinned targets, local/remote leaf authorization, optional direct-dial refusal, rule-DNS metadata, actual global-mode route chain, and snapshot-only geodata |
| Vendored `sing-tun` | [patch_sing_tun.py](../system/programs/clash/mihomo/patches/patch_sing_tun.py) | TUN-bound OUTPUT TCP DNAT and guest TPROXY socket preservation |
| Vendored Gost HTTP handler | [patch_gost.py](../system/programs/gost/patches/patch_gost.py), [gost_http.go](../system/programs/gost/src/gost_http.go) | Bare numeric IPv6 `CONNECT` compatibility with upstream Go HTTP parsing |
| Clash Verge helper | [patch_clash_service.py](../system/programs/clash/service/patch_clash_service.py) | Kernel peer identity for lifecycle ownership, pinned root execution, fixed logs, and constrained socket/startup file operations |
| Clash Verge package/launch | [clash-verge.nix](../system/programs/clash/clash-verge.nix), [core launcher](../system/programs/clash/service/clash-verge-core-launcher.sh), [stage tool](../system/programs/clash/service/clash-verge-stage.py), [GUI launcher](../system/programs/clash/gui/clash-verge-gui.py), [GUI defaults](../system/programs/clash/gui/clash-verge-defaults.sh), [control shim](../system/programs/clash/gui/control_shim.sh) | Authorized snapshots, privilege drop, GUI singleton handling, HTTPS delay-test default, OS proxy write interception, and control-only snapshot reuse |
| `dnsmasq` | [default.nix](../system/config/network/network.nix), inline `src/forward.c` replacement | Return terminal strict-order UDP `SERVFAIL`/`REFUSED`, including DNSSEC EDE |
| Firefox package | [firefox.nix](../home/programs/firefox/firefox.nix), [guard.py](../home/programs/firefox/guard.py), [sandbox.py](../home/programs/firefox/sandbox.py), [scope.c](../home/programs/firefox/scope.c) | Immutable policy/AutoConfig/fonts, preflight, outer namespaces, filtered bus, portal identity, and Landlock/seccomp (no Gecko source patch) |
| Unbound / Chrony / networking | [default.nix](../system/config/network/network.nix), [time.nix](../system/config/network/time.nix), [network modules](../system/config/network) | Declarative policy and repository-owned coordinators (no local Unbound/Chrony source patch) |
| Link identity | [link-identity.nix](../system/config/network/identity/link-identity.nix), [wired.nix](../system/config/network/identity/wired.nix), [link_identity.py](../system/config/network/identity/link_identity.py), [partition.nix](../system/hardware/partition.nix) | Coordinated profile identities, private migration/restore, native wired lifecycle, and DHCPv6 coexistence (no upstream source patch) |
| Firewall lifecycle | [default.nix](../system/config/network/network.nix), [coordinator.nix](../system/config/network/proxy/coordinator.nix), [bootstrap.nix](../system/config/network/firewall/bootstrap.nix) | Exact table ownership, locked atomic replacement, failure retention, and explicit table migration |
| Tailscale transport | [transport.nix](../system/config/network/tailscale/transport.nix), [tailscale_contract.py](../system/config/network/tailscale/tailscale_contract.py) | Upstream socket-mark contract enforced by normal system builds; check mark/fallback/socket behavior and run the real CLI VM on upgrades (no Tailscale source patch) |
| Direct egress | [options.nix](../system/config/network/options.nix), [default.nix](../system/config/network/network.nix), [egress_policy.py](../system/config/network/firewall/egress_policy.py) | Actor/socket identity, pre-NAT grants, canonical CIDR validation, scoped time/DoT, and shared root/GUI private exclusions (compare compatibility and restricted modes) |
| Guest trust | [policy.nix](../system/config/network/guests/policy.nix), [guest_policy.py](../system/config/network/guests/guest_policy.py), [socket-guest.nix](../system/config/network/dns/socket-guest.nix), [docker.nix](../system/programs/virtualisation/docker.nix) | Interface/source roles, canonical grants, primary/delegated UID ownership, pre-NAT real-address DNS, bidirectional revocation, and rootless launcher |
| Sunshine | [sunshine.nix](../system/programs/sunshine.nix) | Graphical-session Desktop service, local administration, no UPnP; retain CUDA and verify the exact package in the configured CUDA cache with local builds disabled |

## Mihomo

The shared domain filter lives in [dns_filter.go](../system/programs/clash/mihomo/guard/dns_filter.go), [target_guard.go](../system/programs/clash/mihomo/guard/target_guard.go), and [blocklist.nix](../system/config/network/dns/blocklist.nix). Its hooks in `dns.newHandler` and `Resolver.ExchangeContext` must sit ahead of hosts, fake-IP, cached replies, and bootstrap upstream queries (including non-address RR types), and application targets must pass the suffix filter before private-domain exemptions. Keep `dnsmasq`'s longest-suffix and tie-breaking exception semantics intact, reject unreadable/invalid immutable list data, include filter payloads/tests in the package fileset, and keep the `./dns` Go tests passing.

`patch_mihomo.py` wires policy into the final `ParseRawConfig`, controller router/`PATCH` handler, direct adapter, DNS metadata/dialer, TCP dial, actual adapter leaf, UDP mapping, geodata downloader, and global route log—check **all** of these hook points on any version bump, as the integration checks test policy enforcement at real dial and controller write paths.

The root policy comes from [profile.nix](../system/config/network/proxy/profile.nix), and [cvr-merge.nix](../home/config/clash/cvr-merge.nix), controller output, and [health.py](../system/config/network/proxy/health.py) must stay aligned with it. Any new upstream `RawTun` fields must be explicitly classified in the schema-coverage tests (check listener/tunnel fields, defaults, aliases, and `GET /configs` output, while keeping duplicate/noncanonical `PATCH` rejection and validating/applying on the same decoded schema).

Target checks run after routing/sniffing and right before the real TCP/UDP dial: checked IPs stay pinned through remote proxies, internal suffixes and literals still need leaf authorization, and checks must run on the actual selected child rather than checking `Unwrap` and re-dialing a group. Reused UDP associations preserve the real leaf and checked target provenance, validate new mappings, and recheck inlet/port/grants on cached mappings per packet without re-resolving.

With `localDnssec` on, [validated_answers.go](../system/programs/clash/mihomo/guard/validated_answers.go) bounds and shares public dial-target lookups: reuse must stay within validator TTLs, caches and in-flight work stay isolated per policy (a replacement policy retires old work while single-caller cancellation doesn't abort shared lookups), and distinct-name limits plus the shared A/AAAA deadline must keep regression coverage for expiry, zero TTLs, failed families, and policy replacement. Keep mapped-IP, metadata, all RFC 6052 prefix lengths, custom fake-IP, and infrastructure-scope tests intact (`LFRuleDNS` separates rule-routed DNS from internal bootstrap so `proxyOnly` can't exempt app DNS; check metadata, adapter names, and group leaves against direct-dial tests). Managed mode relies on `LF_MANAGED_PROXY_POLICY` (leaving it unset keeps upstream behavior for dev fixtures, while the production helper must supply it), keeps policy hashing separate from the root snapshot digest, and gates geodata downloads along with parser validation and missing/corrupt DB replacement.

Managed fake-IP must forward HTTPS/SVCB records with parameters intact while preserving address synthesis and the shared filter. Keep sniffing disabled in both `Rule` and uniform modes, and ensure cached profiles inherit root policy so an ECH public name never replaces the requested target. For the optional guest TPROXY listener (`listener/listener.go` and `listener/tproxy`), keep loopback mixed/DNS/controller behavior under `allow-lan=false`, passive TPROXY ownership checks, and real TCP/UDP guest tests intact.

## sing-tun inside Mihomo

This patch targets Mihomo's vendored `vendor/github.com/metacubex/sing-tun` rather than a standalone `sing-tun` package, so a Mihomo bump can change it even if no `sing-tun` setting changed—inspect Mihomo's `go.mod`, replace directives, and vendor tree. The patch has two parts in `redirect_nftables_rules.go`:

1. In managed non-mark `auto-redirect` mode, local `OUTPUT` TCP DNAT uses the TUN's own address instead of loopback so `SO_BINDTODEVICE(TUN)` sockets work with `route_localnet=0` (other redirect modes stay upstream).
2. `PREROUTING` excludes packets marked `0x233` by the B-only guest TPROXY rules so their transparent socket and original target survive `auto-redirect` lookup (a shared contract with `network.nix`, since ordinary guests can't send a host packet mark over the wire).

Verify both IP families, exclusions, hook conditions, the guest mark, redirect-port selection, and the priority-100 guest-mark lookup ahead of TUN rules—**C**'s self-test reply exceptions and health probes depend on this, and the control/network VMs cover kernel rerouting.

## Gost

The patch targets vendored `github.com/go-gost/x/handler/http` inside `pkgs.gost`—check both Gost and `go-gost/x` versions on upgrades. `lfReadRequest` rewrites only bare numeric IPv6 `CONNECT` targets with nonzero decimal ports while `net/http.ReadRequest` keeps HTTP validation and the returned reader preserves buffered tunnel bytes. Keep bracketed IPv6, ordinary requests, invalid port/framing, and immediate-payload cases in [gost_http_test.go](../system/programs/gost/tests/gost_http_test.go), keep relay single-owner checks, reaping, and resource limits intact, and make sure the service uses `my.proxy.gostPackage` rather than an unpatched `gost` from `PATH`.

## Clash Verge helper and launchers

The GUI uses Nixpkgs's `.unwrapped` derivation (reusing its store path from local store or binary cache when available, though dependency bumps in Nixpkgs can change the path even when the GUI version stays the same), while `clash-verge-service-ipc` is packaged separately—both the patched service and Mihomo build from source when missing, and the wrapper installs the patched service directly. Check Nixpkgs's helper derivation and inherited patches too so `postPatch` isn't clobbered.

The helper patch touches `src/core/manager.rs`, `server.rs`, `logger.rs`, `reconcile.rs`, and the vendored Kode Bridge HTTP server. Keep kernel Unix peer credentials attached to every request (non-root stop and already-running start requests are silent no-ops; controlled-source starts require root; missing peer identity or lifecycle status never authorizes a GUI restart). Audit all exec, log-writer, socket `chmod`/`unlink`, and saved-process reconciliation paths on upgrades: managed exec must resolve to `LF_CLASH_CORE_PATH` with root-owned files/ancestors (root-owned sticky store ancestors allowed), logs stay in the private service dir regardless of caller settings, and client paths/PIDs never become root file/process operations.

The common spawn path requires a live child and connectable controller socket within 15 seconds, candidate parsing plus content audit share an 11-second prep budget, and the managed IPC handler has a 20-second budget—cancellation or timeout kills the newly spawned process group (including validators) to prevent late config commits. The helper serializes starts and caches identical results for 30 seconds (keyed by fixed GUI file identity/timestamps, requiring the same live PID and reachable socket); compatibility root imports relaunch on input changes, GUI callers may only perform the initial authorized start of a stopped core, root source activation stops the core before starting its replacement, root stop joins the watchdog and persists stopped desired-state while keeping the helper listener up, and config refusals never restart the helper.

Keep [runtime.nix](../system/programs/clash/service/runtime.nix), [identity.nix](../system/programs/clash/service/identity.nix), the launcher, and the stage tool in sync: the top runtime dir's group must match systemd's runtime ownership to avoid recursive reownership on restart, writable core cache lives outside the helper's `StateDirectory`, snapshot dirs survive helper restarts, authority/validation logs stay private, and all five capability sets are checked on the real launched core. Meanwhile, the startup defaults helper normalizes `profiles.yaml` (bumping existing integer `update_interval` values in `1..1439` to `1440` without touching `allow_auto_update`, local profiles, comments, or unknown fields, and never creating missing keys) under a per-user lock while confirming the GUI is stopped—the outer launcher holds its lock until the GUI process is observable.

`clash-verge-stage.py` handles candidate checks, snapshot authority, and health acceptance, while [source_refresh.py](../system/programs/clash/service/source_refresh.py) checks registered candidates in isolated copies before restarting a healthy core (without writing live caches or granting authority). Keep candidate, source revision, settled baseline, and root policy bindings intact through activation; eligible node changes write a root-private 5-minute `approval.json` (also binding audit and validator policies, never including structural or unresolved findings) that the normal approval reader rechecks at import. Startup and rollback recheck source revision, renderer settings, and managed policy before running the current audit (keep older revisions under their original labels for comparison).

Startup and the root timer queue download requests, marked ready by the coordinator once **B** has stayed verified and healthy for 90 continuous seconds (bound to boot, snapshot, policy, routing selection, and authorization epoch; leaving healthy **B** resets the timer; **C** keeps requests pending; explicit `off` clears download and apply queues). The downloader rechecks protection before fetching, and the apply service binds the downloaded generation to source selection, revision, digest, and routing selection (retrying busy imports outside operator/source locks and dropping the request if bindings change). Periodic fetches, mapped GUI requests, and explicit `add`/`use` release the source publication lock during network waits and reacquire it to recheck registration, selection, and (for GUI requests) the desktop index before publishing. Explicit **A**/**B** operations skip the 90-second stability gate and wait for the verifier without holding operator or source locks. Within a boot, health-failure pins survive `clash-on`/`clash-off` for their remaining TTL; startup imports keep the full 60-second window, while later imports with repeated fresh full failures can roll back after the configured grace (success always takes the full 60 seconds).

Checks release the authorization lock during probes and reacquire it to verify runtime health and bindings (pending acceptance or rollback defers refreshes). `clash-source-guard.service` enters **C** before restart—keeping `CAP_NET_ADMIN` out of download/parser services—and guard failures block restart; actual import still revalidates and audits before acceptance or rollback. Snapshot authority is published under the policy lock after the authorization lock, with the coordinator checking the transport firewall before releasing the policy lock and binding health observations to that snapshot digest. The mode coordinator signals systemd readiness right after verifying the initial **A** or **C** firewall, checking full **B** health in its run loop; when a switch changes the routing generation in **A**, the inactive selection resets to the new generation's `rules` policy with proxy intent off.

`profile-refresh-verify.timer` runs every 10 seconds, the mode supervisor writes `/run/proxy-mode/boot-id`, `refresh.nix` sets up the 5-minute notification timer, and the audit worker/status tools use Nix-provided PyYAML (keep the worker unprivileged with its fixed JSON schema and pass `subscriptionAuditAllowedHosts` strictly from root config). Nixpkgs currently exposes the underlying core as `.verge-mihomo-wrapped`, which the package swaps with the authorized launcher (`verge-mihomo`)—inspect wrapper layout on GUI updates, keep GUI process identity compatible with `clash-on`/`off` and stale singleton cleanup, set HTTPS delay defaults in writable prefs, keep System Proxy interception working (with memory GSettings and KDE 5 fallback), keep controller no-ops/TUN derivation intact, and run `proxy-gui` plus `proxy-gui-vm` across both routing submodes.

## DNS and browser integration

Runtime routing uses two immutable policy/health/audit bundles and one root-selected symlink, following the `operator → source → authorization → policy` lock order (probes run outside the policy lock and recheck routing epoch and policy digest before committing). Applying a submode or renewing its kernel lease requires a complete mixed TCP, fake-IP TCP, DNS, listener, and config proof (plus real-address DNS through the core validator for `proxy-only`). Normal fetches recheck selection, registration, and policy/epoch after network waits; explicit recovery holds the registration lock during its bounded fetch while keeping **C** active. Keep raw `Rule` data and pinned nodes in digested `source-data.json` with separate accepted snapshots per source and submode. Policy switches tear down old core/relay flows, flush resolver caches, and preserve fake-IP domain mappings across restarts; source starts wait for a fresh helper's IPC listener without restarting it just for startup delay (existing-core recovery keeps unit restarts on IPC failures). The separate `lf_routing` table must enforce declared node tuples even after base firewall reloads (with no exception for established direct replies), and recovery checks the same expiring tuple on output and replies—run `proxy-routing` and `proxy-routing-vm` when touching these paths.

The `dnsmasq` patch is an inline `--replace-fail` in `network.nix`: under `strict-order` retries, the final server's UDP `SERVFAIL`/`REFUSED` (and its DNSSEC EDE) must reach the client, using the `sent-to` array index so duplicate endpoints work—check `forward.c` on upgrades and keep the network VM DNS tests passing. Unbound uses unmodified `pkgs.unbound-with-systemd` (keep `--enable-systemd` for `Type=notify`, and keep DNSSEC/CD, anchor init, TLS auth, reply filtering, stale-cache caps, and probes aligned with [DNS](dns.md); Chrony changes must keep NTS and cert-time checks).

With `localDnssec`, [core-validator.nix](../system/config/network/dns/core-validator.nix) runs separate application and infrastructure validators in the core namespace with independent budgets, persistent anchors, and client CD refusal. Validation and upstream selection stay separate: `rules` keeps domestic splitting (`geosite:cn` directly to domestic validating DoH endpoints, other names via `lf-remote` to Cloudflare/Google DoH, and node bootstrap on its own encrypted resolver; `lf-dns-transport` keeps `mihomoDomesticValidatingDnsUpstreams` rather than inheriting **A**'s DoT list), while `proxy-only` binds both validator transports to authenticated literal-address DoH through the pruned remote catch-all with no direct bootstrap, and real-address source/backend DNS stays on its fixed Unix bridge and budget. Keep TUN UID exclusion paired with the validator's loopback-only firewall and app denial on validator/transport ports, make sure Mihomo validates before public fake-IP synthesis and public dial pinning (clearing AD on synthetic replies and preserving HTTPS/SVCB parameters), and run `proxy-assurance-vm` when changing these paths.

Firefox customization wraps the native browser with immutable policy, AutoConfig, fonts, preflight, and outer confinement—Home Manager or Firefox bumps can alter preference or wrapper semantics without failing a patch, so verify across three test tiers:

- `browser-privacy`: packaged policies, launcher, desktop/MIME entries, preference locks, DoH/proxy values, sandbox environment, and existing-profile/process handling.
- `browser-sandbox-vm`: real Wayland browser, system CA trust (via read-only bundle without exposing `/etc/static`), file/device access (rejecting regular files and unrelated GPU nodes), audio, document portals (per-app view), both installed launchers under fixture routing states, window reuse, and fail-closed portal revocation/cleanup.
- `browser-ech-vm` and `browser-ech-uniform-vm`: HTTP proxy and transparent paths, HTTPS records, ECH vs. GREASE, key/no-ECH retries, untrusted/cert rejection, cover-name target preservation, and **A**/**C** behavior.

On sandbox or kernel updates, review `bubblewrap`, `xdg-dbus-proxy`, Landlock, seccomp, and portal identity; on Firefox/NSS bumps, recheck the `CONNECT` gate, native type-65 resolver API, and retry rules against [the browser guide](browser.md#ech-discovery-and-proxy-compatibility). Also ensure `dnsEncryption="strict"` agrees across coordinator, broker, and static firewall (retaining split DNS, MagicDNS, and literal portals, and testing both transport policies). On NVIDIA systems, compare `about:support` inside and outside the sandbox with a fresh profile under the same package/privacy policy (confirming hardware WebRender, DMA-BUF, and EGL resolution inside the sandbox; check hardware video decoding separately against an unsandboxed baseline).

## Firewall ownership and loading

[Firewall lifecycle](network.md#firewall-lifecycle) owns exact table family/name pairs; only `proxy-control migrate-firewall` deletes `inet filter`, `ip proxymode_nat`, and `ip6 proxymode_nat` after operator confirmation, appending cleanup to the same locked transaction without leaving persistent cleanup state. Keep the shared policy lock, single-transaction static+safety load, failed-transaction retention, bootstrap fallback on first-load failure, rule retention on unit stop, and **C**-before-**B** gating on proxy intent (even when the core stays alive across reload/migration). Keep `inet lf_policy_guard` separate from `inet lf_filter` (using temporary packet priority bits cleared at both ends of each monitored hook, installed atomically with B/C chains).

Validating and HTTP-only configs try `tunProbeNames` in order (synthetic-only TUN configs generate a fresh name), with up to three client DNS probe rounds inside a 20-second budget (HTTP-only probes must parse an IPv4 answer, not just a CNAME or non-empty diagnostics). Reserve `degraded` for advisories after all hard checks pass. With authorization leases enabled, keep kernel timeouts and **C** request/reply guards in all four expiry chains, consuming each fresh epoch/boot/config/sleep-bound proof once, and keeping sleep revocation and lease-off cleanup aligned with readiness and `nft` event filtering (`proxy-assurance-vm` covers frozen/stopped supervisors, existing dual-stack TCP/UDP, bidirectional expiry, and retained recovery). When changing `nftables`, systemd loading, or coordination, run `proxy-firewall-vm`, complete-rule variants, `proxy-control-vm`, and `proxy-network-vm`.

The [drop summary](operations.md#drop-counters) in [inspect.py](../system/config/network/diagnostics/inspect.py) maps chains and `nft` JSON expressions to reason/role groups—keep that mapping synced if you rename chains, reorder guest interfaces, or alter source/port expressions, and run `proxy-regressions`, `proxy-control-vm`, and `proxy-guest-vm` (keeping permission/parse errors distinct from zero counts).

## Link identity

[link-identity.nix](../system/config/network/identity/link-identity.nix), [wired.nix](../system/config/network/identity/wired.nix), and [link_identity.py](../system/config/network/identity/link_identity.py) use upstream NetworkManager/systemd/dhcpcd APIs with no source patches. On upgrades, verify `nmcli` property names/enums, both hostname switches, `stable-id` substitutions, DHCP identifiers, and IPv6 address generation while keeping the identity-only backup whitelist, private state, inactive/autoconnect guards, and refusal to overwrite manual edits (rebuilds never apply profile targets automatically). Keep `dhcpcd`'s upstream generated config and service hardening intact when extending its single-interface command/PID/reload, ensure its DHCPv6 socket coexists with NetworkManager's Wi-Fi client, and run `network-identity-config`, `network-identity-vm`, `proxy-control-vm`, and `proxy-network-vm` together (see [the identity guide](identity.md) for persistence prep).

## Upgrade workflow

### Source compatibility checks

[sources.json](../system/config/network/maintenance/sources.json) tracks maintained entrypoints, invariants, and regression checks per component, while [fingerprints.nix](../system/config/network/maintenance/fingerprints.nix) hashes selected package sources, vendored dependency hashes, and primary Nixpkgs recipes. Normal system builds enforce these baselines—even same-version source or recipe changes require compatibility checks (and a Mihomo bump pulls in vendored `sing-tun` and `socks`).

Preview lock and baseline changes (use `nix flake update <input>` to bump a single input, and `nix build --dry-run .#nixosConfigurations.lfour.config.system.build.toplevel` to preview downloads/builds):

```sh
python3 scripts/check-maintenance.py --plan-pins
```

Generate a detailed component plan (append component names like `mihomo sing-tun socks`, `gost`, `clash`, `dns`, `firefox`, `tailscale`, `firewall`, or `link-identity` for local edits or unchanged inputs):

```sh
nix eval --json .#nixosConfigurations.lfour.config.my.proxy.upgradeSources \
  > /tmp/network-upgrade-selected.json
python3 system/config/network/maintenance/upgrades.py plan \
  system/config/network/maintenance/sources.json /tmp/network-upgrade-selected.json \
  > /tmp/network-upgrade-plan.json
```

Run the planned checks:

```sh
jq -r '.checks[] | ".#checks.x86_64-linux." + .' /tmp/network-upgrade-plan.json \
  | xargs -r nix build --no-link
```

After reviewing upstream sources and recipes, run `python3 scripts/check-maintenance.py --sync-pins` (confirm the version/hash diffs and enter `y`—if network fingerprints didn't change, only the root inventory updates), then run the affected component checks, `proxy-upgrade-review`, and a candidate system build. Remember that recipe fingerprints only cover the primary package expression rather than the full transitive closure, so still scan lock diffs for kernel or graphics-stack changes, and keep the component inventory updated when adding patches or entrypoints.

### Upgrade steps

1. Keep a known-good system generation, then update the target flake input.
2. Compare lock diffs, Nixpkgs recipes, upstream sources, and vendored dependencies.
3. Update affected patch insertion points, filesets, and build phases (retiring upstreamed fixes while keeping their regression tests).
4. If a configuration contract changed, update root policy, GUI Merge, health checks, controller behavior, and listener identities together.
5. Run the affected component checks below (plus control and refresh VMs for refresh/audit/startup-default changes), then build the candidate system.
6. After deploying, restart the old browser, run `clash-off` and `clash-on`, and verify readiness and application traffic (under `auto`, check acceptance and rejected-candidate fallback too).
7. Update source baselines, the customization inventory, and affected guides to match.

## Validation

The deployment-copy script has its own check (requiring Python, Bash, and `rsync`):

```sh
python3 tests/deployment/test_deployment.py scripts/push-to-dir.sh
```

The checks below are exposed as `checks.x86_64-linux.*` in [tests/checks.nix](../tests/checks.nix) (VM checks run in isolated guests and need KVM for hardware acceleration):

| `checks.x86_64-linux.*` | Scope |
|---|---|
| `proxy-control` | Temporary-file snapshot authorization, expiry, restart, hostile inputs, permissions, and single-use B lease proofs / sleep & generation rejection |
| `proxy-refresh` | Offline audit, bounded HTTP checks, local update state, and notification deduplication |
| `proxy-source` | Registered fetch/render, read-only comparison, automatic approval bindings, reboot import, candidate prechecks, and runtime recovery |
| `proxy-source-config` | Source/browser configuration, private DNS paths, and separation of download and policy capabilities |
| `proxy-refresh-vm` | Real auto/enforce helper/core: healthy acceptance, rejected-candidate fallback, pinned restarts, rollback, and desired-state recovery |
| `proxy-upgrade-review` | Selected source/dependency/recipe baselines, component coverage, check-name validation, and source compatibility checks (input gate also runs in normal system builds) |
| `network-identity-config` | Identity migration boundaries, actual `nmcli` parsing/hostname restoration, wired variants, service scope, and persistence (target validation also runs in normal builds) |
| `network-identity-vm` | Real wired/Wi-Fi DHCPv6 coexistence, fresh dual-stack identities, UUID/epoch/random rotation, stable/fixed/permanent behavior, profile precedence, restore, retained host keys, and per-profile ARP cleanup |
| `proxy-arp-vm` | ARP reply scope and counters, gateway conflicts, B lease revocation, link recovery, and IPv6 regression checks |
| `proxy-regressions` | Health/state/rules/DNS leases, bounded client DNS retries, budget, and supervisor regressions |
| `proxy-profile`, `proxy-gui` | Generated profile variants, policy/template agreement, GUI defaults, process wrapper, and desktop command interception |
| `proxy-gui-vm` | Unchanged production GUI under WebKit automation with renamed account and custom home: mode/TUN/System Proxy/PAC/guard no-ops, preserved OS state and core PID, no control errors, GUI exit, and root routing transactions |
| `proxy-routing` | Boot-scoped immutable selection, full proof and epoch binding, failed preparation, sanitized status, and real-DNS deadline/fallback refusal |
| `proxy-routing-vm` | Registered sources and production namespace: bidirectional `rules`/`proxy-only` switching, original rules restored, physical-peer node-only evidence, real DNS/source refresh, old replies, guest refusals, idempotence, concurrent selection, off/on, reboot, and kernel expiry |
| `proxy-routing-recovery-vm` | Cached source recovery in C, DNS and HTTP leaf failover, and inactive routing after returning to A |
| `proxy-single-egress-vm` | Two controlled exits: `rules` selector separation, fixed-leaf dual-stack TCP/UDP and DNS observations, actual Firefox and strict inlet, unsupported/lost UDP, old flows, concurrent refresh, uplink/node failure, plus routing and lease regressions |
| `proxy-ip-tunnel-vm` | Dual-stack WireGuard echo, fixed TCP/UDP exit, relay restricted to declared peer, source acceptance, lease revocation, and peer rotation |
| `proxy-rules`, `proxy-egress-rules` | Complete compatibility/restricted mode rules parsed by the build-time LKL `nft` environment |
| `proxy-firewall-vm` | A/C ownership, C isolation during main table or chain loss and failed repair, external-table preservation, rejected-load rollback, and migration |
| `proxy-dns-strict-rules` | Complete strict DNS firewall rules parsed in LKL |
| `proxy-dns-config` | Generated DNS and verifier script syntax across default, strict, dual-stack, IPv6-only, and HTTP-only configurations |
| `proxy-dns-strict-vm` | Real helper/core, shared DNS/proxy filtering, strict outage/override/lease and leftover-set refusal, private split permission, and literal portal recovery |
| `proxy-assurance-vm` | A/B local DNSSEC, DNS cutover and probe failure, independent DNS budgets, and dual-stack B isolation during rule loss, lease expiry, and suspend |
| `proxy-guest-config`, `proxy-guest-rules` | Production/dedicated-account configuration and rootless launcher syntax; complete mixed-role rules in LKL (guest validation also required by normal system builds) |
| `proxy-guest-vm` | Three real routed networks, DHCP/DNS, IPv4/ULA, spoofed sources, actual `pasta`/RootlessKit/subuid sockets, fake-IP refusal, stale DNS conntrack, bidirectional grant revocation, and C with a live core |
| `proxy-hermes-containers-vm` | Real legacy/new Docker, socket ACL, offline image, account grants, TCP/UDP, unsupported IPv6 refusal, Hermes host proxy, A/B/C, and recovery |
| `proxy-core-netns-vm` | Independent core namespace prototype: TUN/veth, dual-stack nodes, native/SOCKS UDP, DNS/fake-IP, marks/raw packets, host policy ownership, revocation, and restart |
| `proxy-egress-config` | Canonical CIDRs and bounded grant/time scopes (also required by normal system builds) |
| `proxy-egress-vm` | Restricted counterpart of the Tailscale VM: scoped dual-stack private/multicast/DNS/DoT/NTS traffic, UID/interface/port denials, established grant revocation, and C |
| `proxy-egress-control-vm` | Restricted policy before real production TUN/NAT, including allowed/denied private destinations |
| `tailscale-transport` | Selected upstream Linux bypass constant and expected `magicsock`/STUN socket marking paths (also required by normal system builds) |
| `proxy-tailscale-vm` | Real Tailscale listener and CLI, dual-stack UDP/STUN on both uplinks, listener restart and replacement, port reservation, authorization expiry, strict-mode blocking, and remote desktop port policy |
| `proxy-control-config` | Production/test identities, helper capabilities, and generated listeners agree |
| `proxy-core` | Patched core build and Go tests for parser, router, target/cache, actual adapter leaf, DIRECT, UDP, DNS, SOCKS framing, and `sing-tun` interval endpoints |
| `proxy-target-vm` | Real helper/core, distinct local/node loopback traps, HTTP/CONNECT/SOCKS, internal-name pinning, scoped remote grant, Rule/Global, and A/C behavior |
| `proxy-gost` | Patched relay build and vendored HTTP parser tests |
| `browser-privacy` | Final native browser policy/locks and offline launch behavior |
| `browser-sandbox-vm` | Real Wayland Firefox, system-root TLS and invalid-certificate rejection, parent isolation, audio, portal grants, link reuse, generation/restart, and fail-closed proxy lifecycle |
| `browser-ech-vm` | Confined real Firefox/helper/core, native HTTPS discovery, ECH vs. GREASE, key/no-ECH retry, untrusted rejection, certificate errors, cover-name target preservation, and A/C behavior |
| `browser-ech-uniform-vm` | The same real browser paths and policy consumption with `proxyOnly=true` |
| `proxy-control-vm` | Real production helper/launcher/netns, capabilities, localhost broker, overlap/link/resume recovery, authorization, DNS, and bidirectional C revocation |
| `proxy-managed-netns-vm` | Registered TLS sources, preserved core/authority/established TLS connections after rejection, automatic rotation, structural rejection, full readiness, strict inlet, C revocation, and source switching in the production core namespace |
| `proxy-uniform-vm` | Production helper/core under uniform proxy policy: fresh TCP/UDP DIRECT refusal, dual-stack UDP, native IPv6 TUN, and recovery |
| `proxy-network-vm` | Broad kernel fault injection: modes, DNS, leases, sleep, guests, TPROXY, and recovery |

For example, build related packages and quick checks together:

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

`proxy-control-vm` and `proxy-managed-netns-vm` test the production helper and core namespace, `proxy-network-vm` covers broader fault injection, and `proxy-core-netns-vm` runs an isolated prototype topology. On memory-constrained machines, build VM targets one at a time with `--max-jobs 1`. After activating, verify readiness and real app traffic on the live host.
