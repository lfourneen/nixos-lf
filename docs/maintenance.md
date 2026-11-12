# Network patch and customization maintenance

English | [简体中文](CN/maintenance_CN.md) | [Documentation](README.md)

## Build integration

Package versions come from [flake.lock](../flake.lock), not the desktop updater. Stable Nixpkgs supplies Gost, dnsmasq, Unbound and Firefox; unstable supplies Mihomo, the Clash Verge GUI and its separately packaged helper. The current locked set is Mihomo 1.19.33, vendored MetaCubeX sing-tun 0.4.28, Gost 3.3.2, Clash Verge GUI 2.5.2, helper 2.3.3, dnsmasq 2.93 and Unbound 1.26.2. GUI, helper and core versions are independent.

[clash-verge.nix](../system/programs/clash/clash-verge.nix) builds the customized core/helper and places them in the GUI package. [default.nix](../system/programs/gost/default.nix) builds the relay package. Their `lib.fileset.toSource` inputs include only patchers, custom Go code and tests, so unrelated checkout changes do not rebuild those Go packages. **Add any new patch payload/test to the corresponding fileset.** A file present in the checkout but absent from the source set never reaches the builder.

Go patchers run in `postConfigure`, after `buildGoModule` has populated `vendor/`. The helper's Rust patch runs in `postPatch`, appending to inherited Nixpkgs packaging. Source anchors require exact matches; a build failure after an upgrade is a request to compare upstream behavior, not a reason to weaken the anchor or skip a patch.

## Customization inventory

| Target | Repository entry | Purpose / upgrade focus |
|---|---|---|
| Mihomo parser | [patch_mihomo.py](../system/programs/clash/mihomo/patches/patch_mihomo.py), [managed_profile.go](../system/programs/clash/mihomo/config/managed_profile.go) | Final managed-policy enforcement, policy digest, known legacy normalization, no unconditional DIRECT catch-all |
| Mihomo controller | [managed_route.go](../system/programs/clash/mihomo/controller/managed_route.go), [policy_middleware.go](../system/programs/clash/mihomo/controller/policy_middleware.go) | Same-schema PATCH decoding and runtime write allowlist |
| Mihomo egress / DNS / logs | [target_guard.go](../system/programs/clash/mihomo/guard/target_guard.go), `patch_mihomo.py` | Checked/pinned targets, local/remote leaf authorization, optional direct-dial refusal, rule-DNS metadata, actual global-mode route chain and snapshot-only geodata |
| Mihomo SOCKS parser | [patch_socks.py](../system/programs/clash/mihomo/patches/patch_socks.py), [socks_addr_test.go](../system/programs/clash/mihomo/tests/socks_addr_test.go) | Integer offsets avoid byte overflow on long domain names |
| Vendored sing-tun | [patch_sing_tun.py](../system/programs/clash/mihomo/patches/patch_sing_tun.py) | Maximum-address nft interval endpoint and TUN-bound OUTPUT TCP DNAT |
| Vendored Gost HTTP handler | [patch_gost.py](../system/programs/gost/patches/patch_gost.py), [gost_http.go](../system/programs/gost/src/gost_http.go) | Bare numeric IPv6 CONNECT compatibility with upstream Go HTTP parsing |
| Clash Verge helper | [patch_clash_service.py](../system/programs/clash/service/patch_clash_service.py) | Pin root execution, fixed managed logs and constrained socket/startup file operations |
| Clash Verge package/launch | [clash-verge.nix](../system/programs/clash/clash-verge.nix), [core launcher](../system/programs/clash/service/clash-verge-core-launcher.sh), [stage tool](../system/programs/clash/service/clash-verge-stage.py), [GUI launcher](../system/programs/clash/gui/clash-verge-gui.py), [GUI defaults](../system/programs/clash/gui/clash-verge-defaults.sh) | Authorized snapshots, privilege drop, GUI singleton handling and HTTPS delay-test default; upstream GUI binary reused |
| dnsmasq | [default.nix](../system/config/network/network.nix), inline `src/forward.c` replacement | Return terminal strict-order UDP SERVFAIL/REFUSED, including DNSSEC EDE |
| Firefox package | [firefox.nix](../home/programs/firefox/firefox.nix), [guard.py](../home/programs/firefox/guard.py), [sandbox.py](../home/programs/firefox/sandbox.py), [scope.c](../home/programs/firefox/scope.c) | Immutable policy/AutoConfig/fonts, preflight, outer namespaces, filtered bus, portal identity and Landlock/seccomp; no Gecko source patch |
| Unbound / Chrony / networking | [default.nix](../system/config/network/network.nix), [time.nix](../system/config/network/time.nix), [network modules](../system/config/network) | Declarative policy and repository-owned coordinators; no local Unbound/Chrony source patch |
| Link identity | [default.nix](../system/config/network/identity/default.nix), [link_identity.py](../system/config/network/identity/link_identity.py), [partition.nix](../system/hardware/partition.nix) | Coordinated profile identities, private migration/restore, native wired lifecycle and DHCPv6 coexistence; no upstream source patch |
| Firewall lifecycle | [default.nix](../system/config/network/network.nix), [coordinator.nix](../system/config/network/proxy/coordinator.nix), [bootstrap.nix](../system/config/network/firewall/bootstrap.nix) | Exact table ownership, locked atomic replacement, failure retention and explicit table migration |
| Tailscale transport | [transport.nix](../system/config/network/tailscale/transport.nix), [tailscale_contract.py](../system/config/network/tailscale/tailscale_contract.py) | Upstream socket-mark contract enforced by normal system builds; check mark/fallback/socket behavior and run the real CLI VM on upgrades; no Tailscale source patch |
| Direct egress | [options.nix](../system/config/network/options.nix), [default.nix](../system/config/network/network.nix), [egress_policy.py](../system/config/network/firewall/egress_policy.py) | Actor/socket identity, pre-NAT grants, canonical CIDR validation, scoped time/DoT and shared root/GUI private exclusions; compare compatibility and restricted modes |
| Guest trust | [policy.nix](../system/config/network/guests/policy.nix), [guest_policy.py](../system/config/network/guests/guest_policy.py), [socket-guest.nix](../system/config/network/dns/socket-guest.nix), [default.nix](../system/programs/virtualisation/default.nix) | Interface/source roles, canonical grants, primary/delegated UID ownership, pre-NAT real-address DNS, bidirectional revocation and rootless launcher |
| Sunshine | [sunshine.nix](../system/programs/sunshine.nix) | Graphical-session Desktop service, local administration and no UPnP; retain CUDA, verify the exact package in the configured CUDA cache with local builds disabled |

## Mihomo

The shared domain filter lives in [dns_filter.go](../system/programs/clash/mihomo/guard/dns_filter.go), [target_guard.go](../system/programs/clash/mihomo/guard/target_guard.go) and [blocklist.nix](../system/config/network/dns/blocklist.nix). Its hooks in `dns.newHandler` and `Resolver.ExchangeContext` must precede hosts, fake-IP, cached replies and bootstrap upstream queries, including non-address RR types. Application targets must pass the suffix filter before private-domain exemptions. Preserve dnsmasq's longest-suffix and equal-specificity exception semantics and reject unreadable/invalid immutable list data. Include filter payloads/tests in the package fileset and retain the `./dns` Go tests.

`patch_mihomo.py` inserts the policy at the final `ParseRawConfig`, controller router/PATCH handler, direct adapter, DNS metadata/dialer, TCP dial, actual adapter leaf, UDP mapping, geodata downloader and global route log. Check **all** of these insertion points on a version bump. The integration checks exercise policy enforcement at the actual dial and controller write handlers.

The root policy is generated by [profile.nix](../system/config/network/proxy/profile.nix); [cvr-merge.nix](../home/config/clash/cvr-merge.nix), controller output and [health.py](../system/config/network/proxy/health.py) must agree with it. New upstream `RawTun` fields require explicit classification in the schema-coverage tests. Check listener/tunnel fields, defaults and aliases as well as fields exposed by `GET /configs`. Keep duplicate/noncanonical PATCH rejection and validation/application on the same decoded schema.

Target checks run after routing/sniffing and before the actual TCP/UDP dial; checked IPs must remain pinned through a remote proxy. Internal suffixes and literals must also pass leaf authorization; keep this at the actual selected child rather than checking `Unwrap` and dialing a group again. Reused UDP associations retain the actual leaf and checked target provenance, validate new mappings, and recheck current inlet/port/grants on cached mappings without resolving every later packet again. Preserve mapped-IP, metadata, all RFC 6052 prefix lengths, custom fake-IP and infrastructure-scope tests. `LFRuleDNS` distinguishes rule-routed DNS from internal bootstrap, so `proxyOnly` cannot accidentally exempt application DNS. Check metadata, adapter names and group leaves against the direct-dial tests.

Managed mode uses `LF_MANAGED_PROXY_POLICY`; its absence deliberately retains upstream behavior for development fixtures. The production helper must supply it. Keep policy hashing distinct from the root-owned snapshot digest. The geodata download gate must also cover parser validation and replacement of a missing/corrupt database.

`patch_socks.py` changes arithmetic in `adapter/inbound/util.go` and copies boundary tests into that package. Its header records an upstream reference. If an upstream release implements the correction, compare long-domain decoding and malformed input behavior, retain the regression tests and remove only the redundant patch invocation/payload entries.

Managed fake-IP must forward HTTPS/SVCB records with their parameters intact while retaining address synthesis and the shared filter. Keep sniffing disabled in both Rule and uniform modes; cached profiles must inherit the root policy so an ECH public name cannot replace the requested target.

The optional guest TPROXY listener has its own bind-address handling and IPv4 socket selection in `listener/listener.go` and `listener/tproxy`. Preserve loopback mixed/DNS/controller behavior with `allow-lan=false`, passive TPROXY ownership checks and real TCP/UDP guest tests when updating these paths.

## sing-tun inside Mihomo

The target is `vendor/github.com/metacubex/sing-tun`, not an independently installed sing-tun package. A Mihomo update can change it even when no explicit sing-tun setting changes. Inspect Mihomo's `go.mod`, module replacement directives and generated vendor tree.

The patch has **three independent parts**:

1. `redirect_nftables_exprs.go`: a range ending at the maximum IPv4/IPv6 address has no valid successor. Omit its end marker instead of producing an inverted/empty nft interval. The patcher records a SagerNet reference; check the MetaCubeX fork's actual code before removing this part.
2. `redirect_nftables_rules.go`: in managed non-mark auto-redirect mode, local OUTPUT TCP DNAT uses the TUN's own address instead of loopback. This preserves `SO_BINDTODEVICE(TUN)` sockets with `route_localnet=0`. Other redirect modes retain their upstream behavior.
3. `redirect_nftables_rules.go`: PREROUTING excludes packets marked `0x233` by the owned B-only guest TPROXY rules. Their transparent socket and original target must survive later auto-redirect lookup. The mark is a shared contract with `network.nix`; ordinary guests cannot transmit a host packet mark over the wire.

Check both IP families, configured exclusions, hook conditions, the guest mark and redirect-port selection. The guest mark lookup must precede TUN policy rules; the declared priority is 100. The firewall's C self-test reply exceptions and the health probe depend on this path. The control/network VMs exercise kernel rerouting and belong to the sing-tun upgrade check set.

## Gost

The patch targets the vendored `github.com/go-gost/x/handler/http` inside `pkgs.gost`. On upgrade, check both Gost's version and its `go-gost/x` dependency. `lfReadRequest` rewrites only a numeric unbracketed IPv6 CONNECT target with a decimal nonzero port. `net/http.ReadRequest` retains HTTP validation, and the returned reader preserves an already buffered tunnel payload.

Keep bracketed IPv6, ordinary requests, invalid ports/framing and immediate payload cases in [gost_http_test.go](../system/programs/gost/tests/gost_http_test.go). Also retain the relay's single-owner checks, process reaping and resource limits; a parser fix does not replace lifecycle protection. The actual service must use `my.proxy.gostPackage`, not an unpatched package from PATH.

## Clash Verge helper and launchers

The GUI `.unwrapped` derivation is intentionally reused and asserted equal to upstream. Changes are applied to the separately built Rust helper, installed wrapper paths and runtime scripts. Check Nixpkgs's helper derivation and inherited patches too: replacing `postPatch` or copying the wrong helper would discard integration behavior.

The helper patch covers `src/core/manager.rs`, `server.rs`, `logger.rs` and `reconcile.rs`. On upgrade, enumerate all executable launch, log-writer, socket chmod/unlink and saved-process reconciliation paths. Managed execution must resolve to `LF_CLASH_CORE_PATH` with root-owned files/ancestors; root-owned sticky store ancestors are allowed. Logs must stay in the private service directory, regardless of caller settings. Client paths/PIDs must not become root file/process operations.

The common spawn path requires a living child and a connectable fixed controller socket within 15 seconds. Candidate parsing and the additional audit share an 11-second preparation budget, reserving time to start the core or fall back. The managed IPC handler has a 20-second budget. Cancellation and readiness timeout stop the newly spawned process group, including validators, to prevent a late configuration commit. Preserve this relationship when changing helper or IPC dependencies.

The prebuilt GUI retains its upstream IPC client and short request timeout. The helper serializes starts and reuses completed results for identical requests for 30 seconds. The retry signature includes the fixed GUI file's identity and change timestamps; a success also requires the same running PID and reachable fixed socket. Changed input, explicit authorization or a stopped core requires a new launch. This avoids repeated core restarts during GUI transport retries without rebuilding the GUI.

Keep [runtime.nix](../system/programs/clash/service/runtime.nix), [identity.nix](../system/programs/clash/service/identity.nix), the launcher and stage tool consistent. The top runtime directory's group must match systemd's runtime ownership to avoid recursive reownership on restart. Do not add the writable core cache to the root helper's `StateDirectory`; it is created separately. Preserve snapshot directories across helper restarts, keep authority/validation logs private and check the actual core's five capability sets after the real helper launch.

The startup defaults helper also normalizes `profiles.yaml`: it raises only existing integer `update_interval` values from 1 through 1439 to 1440, leaves `allow_auto_update`, local profiles, comments and unknown fields untouched, and runs under a per-user lock while checking that the GUI is stopped. The outer launcher holds its lock through normalization until the GUI process is observable. It never creates a missing `option` or interval.

`clash-verge-stage.py` handles candidate checks, snapshot authority and health acceptance. `profile-refresh-verify.timer` runs every ten seconds. The mode supervisor publishes `/run/proxy-mode/boot-id`, and `refresh.nix` defines the five-minute user notification timer. The audit worker and status tools use PyYAML supplied explicitly by Nix. Keep the worker unprivileged, preserve the fixed JSON schema and reason vocabulary, and pass `subscriptionAuditAllowedHosts` from root configuration rather than deriving permissions from a subscription.

Nixpkgs currently exposes the underlying core as `.verge-mihomo-wrapped`; the package replaces public `verge-mihomo` with the authorized launcher. Inspect wrapper layout on GUI/package updates. GUI process identity must remain compatible with `clash-on/off`, and obsolete same-user singletons must not retain an old package's core path. HTTPS delay defaults are set through writable preferences, without recompiling the GUI application.

## DNS and browser integration

The dnsmasq patch is an inline `--replace-fail` in `network.nix`. In strict-order retry handling, the final server's UDP SERVFAIL/REFUSED must reach the client, including its EDE. The condition uses the sent-to array index because server endpoints can repeat. Check terminal-error, retry and duplicate-server behavior against the new `forward.c`; keep the real DNS response tests in the network VM.

Unbound uses unmodified `pkgs.unbound-with-systemd`. Preserve `--enable-systemd` if introducing a future override: the unit is `Type=notify`. Keep DNSSEC/CD behavior, persistent anchor initialization, TLS authentication, reply filtering, stale-cache limits and probe verdicts aligned with [DNS](dns.md). Chrony changes must preserve NTS requirement and certificate-time checks.

Firefox customization wraps the packaged browser with immutable policy, AutoConfig, fonts, preflight and outer confinement. An update can alter Home Manager's final package/wrapper or preference semantics without any source patch failing. Check the final launcher and desktop/MIME entries, policy locks, DoH/proxy values, sandbox environment and existing-profile/process behavior with `browser-privacy`.

Run `browser-sandbox-vm` for the real Wayland browser, system-CA TLS acceptance and untrusted/wrong-host certificate rejection, parent filesystem/device/socket denials, audio, portal identity/document grants, profile-specific link reuse, generation rejection, restart and proxy failure. Check bubblewrap, xdg-dbus-proxy, Landlock ABI, seccomp and xdg-desktop-portal identity semantics on dependency or kernel upgrades. Keep the per-app document view and host-only instance records intact. Device-selection checks must reject regular files and unrelated GPUs’ primary nodes; NVIDIA permissions follow the declared driver choice and can be disabled separately. Verify the dereferenced NixOS CA bundle while keeping `/etc/static` hidden. The system-trust test uses a fresh browser profile without imported CAs so NSS/p11-kit obtains trust from the system CA bundle. Also run `browser-ech-vm` inside confinement for the default HTTP and optional transparent paths. Check the selected Firefox CONNECT gate, native type-65 API and NSS retry/certificate behavior. Keep automation's native-DNS test switch inside the temporary profile, and distinguish authenticated retry without ECH from failed certificate validation. The [browser guide](browser.md#ech-discovery-and-proxy-compatibility) describes these paths; the test's local CA and keys are isolated fixtures.

`dnsEncryption="strict"` must agree across the coordinator, broker and static firewall: no automatic or forced fallback, no plaintext leases, and no fallback-set accept rule. Retain approved split DNS, MagicDNS and literal portal access. Test both transport policies; the default recovery budget remains a separate behavior.

On NVIDIA systems, also check the real Wayland compositor and PipeWire/Pulse backend using a fresh profile. Compare the same package and privacy policy with and without outer confinement: `about:support` should retain the expected hardware WebRender and DMA-BUF capabilities when NVIDIA access is selected. Confirm that the EGL platform configuration resolves inside the sandbox. Hardware video decoding has separate driver and Gecko requirements; check decoding separately with the same package and an unsandboxed baseline.

## Firewall ownership and loading

The [firewall lifecycle](network.md#firewall-lifecycle) reserves exact table family/name pairs. Activation, reload, mode changes and drift repair operate within that ownership boundary. The deletion list belongs exclusively to `proxy-control migrate-firewall`; the [migration procedure](operations.md#firewall-table-migration) requires the operator to confirm each target's ownership before deletion.

Preserve the shared policy lock and the single transaction containing static rules plus the safety fragment. The explicit migration adds its cleanup to that same transaction without leaving persistent cleanup state. A rejected nft transaction must retain the previous policy, and first-load failure must retain the independent bootstrap baseline. Stopping the nftables unit must leave protection installed. Proxy intent must pass through C before a health proof permits B, including when the core remains alive during reload or migration.

For changes to nftables, systemd loading or coordination, run `proxy-firewall-vm`, the relevant complete-rule variants, `proxy-control-vm` and `proxy-network-vm`. The dedicated VM checks A/C table ownership, external sets and ordered rules, failed transactions, lock contention and reuse of migration target names. The control VM checks the real helper/core, preservation of its TUN table, C enforcement and B recovery; the broad VM retains first-load and drift-recovery coverage.

## Link identity

[default.nix](../system/config/network/identity/default.nix) and [link_identity.py](../system/config/network/identity/link_identity.py) use upstream NetworkManager/systemd/dhcpcd APIs without source patches. Check actual nmcli property names and enum formatting, both hostname switches, stable-id substitutions, DHCP identifiers and IPv6 address generation. Preserve the identity-only backup whitelist, private state, inactive/autoconnect guards and refusal to overwrite manual edits; rebuilds do not apply profile targets automatically.

Retain the upstream generated dhcpcd configuration and service hardening when extending its single-interface command, PID file and reload. Its DHCPv6 socket must coexist with NetworkManager's Wi-Fi client. Run `network-identity-config`, `network-identity-vm`, `proxy-control-vm` and `proxy-network-vm` together; the identity VM requires fresh exchanges for each activation, including unchanged MACs. The [identity guide](identity.md) covers persistence preparation and reauthorization.

## Upgrade workflow

### Source compatibility checks

[sources.json](../system/config/network/maintenance/sources.json) groups the maintained entrypoints, invariants and regression checks by component. [default.nix](../system/config/network/maintenance/default.nix) fingerprints the selected package sources, available vendored dependency hashes and primary Nixpkgs package recipes. Normal system builds require these inputs to match the configured source baseline. A same-version source or recipe change still requires compatibility checks; a Mihomo change also selects its vendored sing-tun and SOCKS components.

Generate the current input inventory and inspect a plan from the checkout:

```sh
nix eval --json .#nixosConfigurations.lfour.config.my.proxy.upgradeSources \
  > /tmp/network-upgrade-selected.json
python3 system/config/network/maintenance/upgrades.py plan \
  system/config/network/maintenance/sources.json /tmp/network-upgrade-selected.json \
  > /tmp/network-upgrade-plan.json
```

The plan lists changed components, source entrypoints, invariants and checks. Add component names such as `mihomo sing-tun socks`, `gost`, `clash`, `dns`, `firefox`, `tailscale`, `firewall` or `link-identity` to request a plan for local customization changes or unchanged inputs. Execute the selected checks with:

```sh
jq -r '.checks[] | ".#checks.x86_64-linux." + .' /tmp/network-upgrade-plan.json \
  | xargs -r nix build --no-link
```

Compare upstream sources and packaging and complete the component checks before changing its baseline entries. Then run `proxy-upgrade-review` and build the candidate system. The plan tool never updates the baseline. Its schema requires each component's minimum source and regression coverage, and the flake checks that the named checks exist.

The gate checks source versions and fingerprints. Exact patch anchors, package tests and kernel/browser integration tests check the corresponding behavior. The recipe fingerprint covers the primary package expression, not the full transitive build closure; check the lock diff for other relevant changes, including the deployed kernel and graphics stack. Keep the component inventory current when adding a patch, dependency or entrypoint.

### Upgrade steps

1. Identify the supplying flake input and update the intended input in the checkout. Compare the lock diff and the Nixpkgs package expression, upstream source and vendored dependency changes. Preserve a working system generation.
2. Compare every affected insertion point above. Rebase local behavior or retire a redundant fix while retaining meaningful regression coverage. Keep exact-match failures; update filesets and build phases when files move.
3. Update root policy, Merge template, health/controller expectations, listener identities and VM fixtures together where their contract changes. Production identities/packages must remain those exercised by the small control VM.
4. Run the relevant check set together after editing, including `proxy-refresh`, `proxy-control`, `proxy-gui`, `proxy-control-vm` and `proxy-refresh-vm` for refresh/audit/defaults changes. Build the candidate system without activation; deploy only after checks pass. On activation, restart the old browser and perform a deliberate `clash-off` → `clash-on` import before checking readiness and real traffic. Under `auto`, also confirm a routine refresh settles as `accepted` and that a rejected candidate keeps the previous snapshot.
5. Update the inventory/version descriptions and affected guides to match the resulting implementation.

## Validation

The deployment script has a standalone temporary-directory check, run from the checkout with `python3 tests/deployment/test_deployment.py scripts/push-to-dir.sh`. It checks retained build inputs, excluded documentation, permissions and missing-input behavior without sudo or a host destination. It requires Python, Bash, rsync and the script's standard command-line tools; it stays separate from flake checks because `scripts/` is not deployed.

Use the attributes below from the checkout. Nix builds normally use the local daemon; the tests themselves do not need host root networking. VMs use isolated kernels; KVM execution requires local KVM access. Their first dependency build is more expensive, so choose by affected behavior before running them together.

| `checks.x86_64-linux.*` | Scope |
|---|---|
| `proxy-control` | Temporary-file snapshot authorization, expiry, restart, hostile inputs and permissions |
| `proxy-refresh` | Offline audit, bounded HTTP checks, local update state and notification deduplication |
| `proxy-refresh-vm` | Real auto/enforce helper/core: healthy acceptance, rejected-candidate fallback, pinned restarts, rollback and desired-state recovery |
| `proxy-upgrade-review` | Selected source/dependency/recipe baselines, component coverage, check-name validation and source compatibility checks; the input gate also runs in normal system builds |
| `network-identity-config` | Identity migration boundaries, actual nmcli parsing/hostname restoration, wired variants, service scope and persistence; target validation also runs in normal system builds |
| `network-identity-vm` | Real wired/Wi-Fi DHCPv6 coexistence, fresh dual-stack identities, UUID/epoch/random rotation, stable/fixed/permanent behavior, profile precedence, restore and retained host keys |
| `proxy-regressions` | Health/state/rules/DNS leases, budget and supervisor regressions |
| `proxy-profile`, `proxy-gui` | Generated profile variants, policy/template agreement, GUI defaults and process wrapper |
| `proxy-rules`, `proxy-egress-rules` | Complete compatibility/restricted mode rules parsed by the build-time LKL nft environment |
| `proxy-firewall-vm` | A/C ownership, external-table preservation, rejected-load rollback, migration locks, idempotence and migration-target name reuse |
| `proxy-dns-strict-rules` | Complete strict DNS firewall rules parsed in LKL |
| `proxy-dns-config` | Syntax of generated default/strict DNS coordinator scripts |
| `proxy-dns-strict-vm` | Real helper/core, shared DNS/proxy filtering, strict outage/override/lease and leftover-set refusal, private split permission and literal portal recovery |
| `proxy-guest-config`, `proxy-guest-rules` | Production/dedicated-account configuration and rootless launcher syntax; complete mixed-role rules in LKL. Guest validation is also required by normal system builds |
| `proxy-guest-vm` | Three real routed networks, DHCP/DNS, IPv4/ULA, spoofed sources, actual pasta/RootlessKit/subuid sockets, fake-IP refusal, stale DNS conntrack, bidirectional grant revocation and C with a live core |
| `proxy-egress-config` | Canonical CIDRs and bounded grant/time scopes; also required by normal system builds |
| `proxy-egress-vm` | Restricted counterpart of the Tailscale VM; scoped dual-stack private/multicast/DNS/DoT/NTS traffic, UID/interface/port denials, established grant revocation and C |
| `proxy-egress-control-vm` | Restricted policy before real production TUN/NAT, including allowed/denied private destinations |
| `tailscale-transport` | Selected upstream Linux bypass constant and expected magicsock/STUN socket marking paths; also required by normal system builds |
| `proxy-tailscale-vm` | Real selected Tailscale CLI, isolated dual-stack STUN, both uplinks, socket/packet/UID restrictions, B/C recovery and remote desktop port policy |
| `proxy-control-config` | Production/test identities, helper capabilities and generated listeners agree |
| `proxy-core` | Patched core build plus injected parser, router, target, actual adapter-leaf, direct/UDP/DNS and SOCKS Go tests |
| `proxy-target-vm` | Real helper/core, distinct local/node loopback traps, HTTP/CONNECT/SOCKS, internal-name pinning, scoped remote grant, Rule/Global and A/C behavior |
| `proxy-gost` | Patched relay build and vendored HTTP parser tests |
| `browser-privacy` | Final native browser policy/locks and offline launch behavior |
| `browser-sandbox-vm` | Real Wayland Firefox, system-root TLS and invalid-certificate rejection, parent isolation, audio, portal grants, link reuse, generation/restart and fail-closed proxy lifecycle |
| `browser-ech-vm` | Confined real Firefox/helper/core, native HTTPS discovery, ECH versus GREASE, key/no-ECH retry, untrusted rejection, certificate errors, cover-name target preservation and A/C behavior |
| `browser-ech-uniform-vm` | The same real browser paths and policy consumption with `proxyOnly=true` |
| `proxy-control-vm` | Real production helper/launcher, process capabilities, authorization, DNS, migration with a live TUN core and C-to-B recovery |
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

The broad VM includes deliberate simulated/root-core fixtures; use the small control VM to verify production launch permissions. Neither changes the running host's routes/firewall. Package tests establish parser/policy behavior; they do not replace kernel-path tests. Host activation additionally needs privileged observations of actual process identity/capabilities, listener ownership, snapshot state and A → B/C transitions.
