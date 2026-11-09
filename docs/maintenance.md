# Network patch and customization maintenance

English | [简体中文](CN/maintenance_CN.md) | [Documentation](README.md)

## Build integration

Package versions come from [flake.lock](../flake.lock), not the desktop updater. Stable Nixpkgs supplies Gost, dnsmasq, Unbound and Firefox; unstable supplies Mihomo, the Clash Verge GUI and its separately packaged helper. The current locked set is Mihomo 1.19.33, vendored MetaCubeX sing-tun 0.4.28, Gost 3.3.2, Clash Verge GUI 2.5.2, helper 2.3.3, dnsmasq 2.93 and Unbound 1.26.2. GUI, helper and core versions are independent.

[clash-verge.nix](../system/programs/clash/clash-verge.nix) builds the customized core/helper and places them in the GUI package. [gost-relay.nix](../system/programs/systemd/gost-relay.nix) builds the relay package. Their `lib.fileset.toSource` inputs include only patchers, custom Go code and tests, so unrelated checkout changes do not rebuild those Go packages. **Add any new patch payload/test to the corresponding fileset.** A file present in the checkout but absent from the source set never reaches the builder.

Go patchers run in `postConfigure`, after `buildGoModule` has populated `vendor/`. The helper's Rust patch runs in `postPatch`, appending to inherited Nixpkgs packaging. Source anchors require exact matches; a build failure after an upgrade is a request to compare upstream behavior, not a reason to weaken the anchor or skip a patch.

## Customization inventory

| Target | Repository entry | Purpose / upgrade focus |
|---|---|---|
| Mihomo parser | [patch_mihomo.py](../system/programs/systemd/netsec/patch_mihomo.py), [managed_profile.go](../system/programs/systemd/netsec/managed_profile.go) | Final managed-policy enforcement, policy digest, known legacy normalization, no unconditional DIRECT catch-all |
| Mihomo controller | [managed_route.go](../system/programs/systemd/netsec/managed_route.go), [policy_middleware.go](../system/programs/systemd/netsec/policy_middleware.go) | Same-schema PATCH decoding and runtime write allowlist |
| Mihomo egress / DNS / logs | [target_guard.go](../system/programs/systemd/netsec/target_guard.go), `patch_mihomo.py` | Checked/pinned domain targets, optional direct-dial refusal, rule-DNS metadata, actual global-mode route chain and snapshot-only geodata |
| Mihomo SOCKS parser | [patch_socks.py](../system/programs/systemd/netsec/patch_socks.py), [socks_addr_test.go](../system/programs/systemd/netsec/socks_addr_test.go) | Integer offsets avoid byte overflow on long domain names |
| Vendored sing-tun | [patch_sing_tun.py](../system/programs/systemd/netsec/patch_sing_tun.py) | Maximum-address nft interval endpoint and TUN-bound OUTPUT TCP DNAT |
| Vendored Gost HTTP handler | [patch_gost.py](../system/programs/systemd/netsec/patch_gost.py), [gost_http.go](../system/programs/systemd/netsec/gost_http.go) | Bare numeric IPv6 CONNECT compatibility with upstream Go HTTP parsing |
| Clash Verge helper | [patch_clash_service.py](../system/programs/clash/patch_clash_service.py) | Pin root execution, fixed managed logs and constrained socket/startup file operations |
| Clash Verge package/launch | [clash-verge.nix](../system/programs/clash/clash-verge.nix), [core launcher](../system/programs/clash/clash-verge-core-launcher.sh), [stage tool](../system/programs/clash/clash-verge-stage.py), [GUI launcher](../system/programs/clash/clash-verge-gui.py), [GUI defaults](../system/programs/clash/clash-verge-defaults.sh) | Authorized snapshots, privilege drop, GUI singleton handling and HTTPS delay-test default; upstream GUI binary reused |
| dnsmasq | [network.nix](../system/config/network.nix), inline `src/forward.c` replacement | Return terminal strict-order UDP SERVFAIL/REFUSED, including DNSSEC EDE |
| Firefox package | [firefox.nix](../home/programs/firefox/firefox.nix), [guard.py](../home/programs/firefox/guard.py) | Immutable policy/AutoConfig/fonts and managed launcher; no Gecko source patch |
| Unbound / Chrony / networking | [network.nix](../system/config/network.nix), [network-time.nix](../system/config/network-time.nix), [systemd modules](../system/programs/systemd) | Declarative policy and repository-owned coordinators; no local Unbound/Chrony source patch |
| Tailscale transport | [tailscale-transport.nix](../system/config/tailscale-transport.nix), [tailscale_contract.py](../system/programs/systemd/netsec/tailscale_contract.py) | Upstream socket-mark contract enforced by normal system builds; review mark/fallback/socket changes and run the real CLI VM on upgrades; no Tailscale source patch |
| Direct egress | [hardening-options.nix](../system/config/hardening-options.nix), [network.nix](../system/config/network.nix), [egress_policy.py](../system/programs/systemd/netsec/egress_policy.py) | Actor/socket identity, pre-NAT grants, canonical CIDR validation, scoped time/DoT and shared root/GUI private exclusions; compare compatibility and restricted modes |
| Sunshine | [sunshine.nix](../system/programs/sunshine.nix) | Graphical-session Desktop service, local administration and no UPnP; retain CUDA, verify the exact package in the configured CUDA cache with local builds disabled |

## Mihomo

The shared domain filter lives in [dns_filter.go](../system/programs/systemd/netsec/dns_filter.go), [target_guard.go](../system/programs/systemd/netsec/target_guard.go) and [dns-blocklist.nix](../system/config/dns-blocklist.nix). Its hooks in `dns.newHandler` and `Resolver.ExchangeContext` must precede hosts, fake-IP, cached replies and bootstrap upstream queries, including non-address RR types. Application targets must pass the suffix filter before private-domain exemptions. Preserve dnsmasq's longest-suffix and equal-specificity exception semantics and reject unreadable/invalid immutable list data. Include filter payloads/tests in the package fileset and retain the `./dns` Go tests.

`patch_mihomo.py` inserts the policy at the final `ParseRawConfig`, controller router/PATCH handler, direct adapter, DNS metadata/dialer, TCP dial, UDP mapping, geodata downloader and global route log. Review **all** of these insertion points on a version bump. A compiling parser patch alone cannot prove the actual dial or write API still passes through the intended gate.

The root policy is generated by [proxy-profile.nix](../system/config/proxy-profile.nix); [cvr-merge.nix](../home/config/cvr-merge.nix), controller output and [health.py](../system/programs/systemd/netsec/health.py) must agree with it. New upstream `RawTun` fields require explicit classification in the schema-coverage tests. Check listener/tunnel fields, defaults and aliases as well as fields exposed by `GET /configs`. Keep duplicate/noncanonical PATCH rejection and validation/application on the same decoded schema.

Target checks run after routing/sniffing and before the actual TCP/UDP dial; checked IPs must remain pinned through a remote proxy. Reused UDP associations require validation for new destination mappings without resolving every later packet again. `LFRuleDNS` distinguishes rule-routed DNS from internal bootstrap, so `proxyOnly` cannot accidentally exempt application DNS. Review changed metadata, adapter names and group leaves against the direct-dial tests.

Managed mode uses `LF_MANAGED_PROXY_POLICY`; its absence deliberately retains upstream behavior for development fixtures. The production helper must supply it. Keep policy hashing distinct from the root-owned snapshot digest. The geodata download gate must also cover parser validation and replacement of a missing/corrupt database.

`patch_socks.py` changes arithmetic in `adapter/inbound/util.go` and copies boundary tests into that package. Its header records an upstream reference. If an upstream release implements the correction, compare long-domain decoding and malformed input behavior, retain the regression tests and remove only the redundant patch invocation/payload entries.

Managed fake-IP must forward HTTPS/SVCB records with their parameters intact while retaining address synthesis and the shared filter. Keep sniffing disabled in both Rule and uniform modes; cached profiles must inherit the root policy so an ECH public name cannot replace the requested target.

## sing-tun inside Mihomo

The target is `vendor/github.com/metacubex/sing-tun`, not an independently installed sing-tun package. A Mihomo update can change it even when no explicit sing-tun setting changes. Inspect Mihomo's `go.mod`, module replacement directives and generated vendor tree.

The patch has **two independent parts**:

1. `redirect_nftables_exprs.go`: a range ending at the maximum IPv4/IPv6 address has no valid successor. Omit its end marker instead of producing an inverted/empty nft interval. The patcher records a SagerNet reference; check the MetaCubeX fork's actual code before removing this part.
2. `redirect_nftables_rules.go`: in managed non-mark auto-redirect mode, local OUTPUT TCP DNAT uses the TUN's own address instead of loopback. This preserves `SO_BINDTODEVICE(TUN)` sockets with `route_localnet=0`. The PREROUTING path and other redirect modes keep their upstream behavior.

Review both IP families, configured exclusions, hook conditions and redirect-port selection. The firewall's C self-test reply exceptions and the health probe depend on this path. Source matching/package compilation cannot verify kernel rerouting; include the control/network VMs for sing-tun upgrades.

## Gost

The patch targets the vendored `github.com/go-gost/x/handler/http` inside `pkgs.gost`. On upgrade, check both Gost's version and its `go-gost/x` dependency. `lfReadRequest` rewrites only a numeric unbracketed IPv6 CONNECT target with a decimal nonzero port. `net/http.ReadRequest` retains HTTP validation, and the returned reader preserves an already buffered tunnel payload.

Keep bracketed IPv6, ordinary requests, invalid ports/framing and immediate payload cases in [gost_http_test.go](../system/programs/systemd/netsec/gost_http_test.go). Also retain the relay's single-owner checks, process reaping and resource limits; a parser fix does not replace lifecycle protection. The actual service must use `my.proxy.gostPackage`, not an unpatched package from PATH.

## Clash Verge helper and launchers

The GUI `.unwrapped` derivation is intentionally reused and asserted equal to upstream. Changes are applied to the separately built Rust helper, installed wrapper paths and runtime scripts. Check Nixpkgs's helper derivation and inherited patches too: replacing `postPatch` or copying the wrong helper would discard integration behavior.

The helper patch covers `src/core/manager.rs`, `logger.rs` and `reconcile.rs`. On upgrade, enumerate all executable launch, log-writer, socket chmod/unlink and saved-process reconciliation paths. Managed execution must resolve to `LF_CLASH_CORE_PATH` with root-owned files/ancestors; root-owned sticky store ancestors are allowed. Logs must stay in the private service directory, regardless of caller settings. Client paths/PIDs must not become root file/process operations.

Keep [runtime.nix](../system/programs/clash/runtime.nix), [identity.nix](../system/programs/clash/identity.nix), the launcher and stage tool consistent. The top runtime directory's group must match systemd's runtime ownership to avoid recursive reownership on restart. Do not add the writable core cache to the root helper's `StateDirectory`; it is created separately. Preserve snapshot directories across helper restarts, keep authority/validation logs private and check the actual core's five capability sets after the real helper launch.

Nixpkgs currently exposes the underlying core as `.verge-mihomo-wrapped`; the package replaces public `verge-mihomo` with the authorized launcher. Inspect wrapper layout on GUI/package updates. GUI process identity must remain compatible with `clash-on/off`, and obsolete same-user singletons must not retain an old package's core path. HTTPS delay defaults are set through writable preferences, without recompiling the GUI application.

## DNS and browser integration

The dnsmasq patch is an inline `--replace-fail` in `network.nix`. In strict-order retry handling, the final server's UDP SERVFAIL/REFUSED must reach the client, including its EDE. The condition uses the sent-to array index because server endpoints can repeat. Review terminal-error, retry and duplicate-server behavior against the new `forward.c`; keep the real DNS response tests in the network VM.

Unbound uses unmodified `pkgs.unbound-with-systemd`. Preserve `--enable-systemd` if introducing a future override: the unit is `Type=notify`. Keep DNSSEC/CD behavior, persistent anchor initialization, TLS authentication, reply filtering, stale-cache limits and probe verdicts aligned with [DNS](dns.md). Chrony changes must preserve NTS requirement and certificate-time checks.

Firefox customization wraps the packaged browser with immutable policy, AutoConfig, fonts and preflight. An update can alter Home Manager's final package/wrapper or preference semantics without any source patch failing. Check the final launcher and desktop/MIME entries, policy locks, DoH/proxy values, sandbox environment and existing-profile/process behavior with `browser-privacy`.

Also run `browser-ech-vm` for the default HTTP and optional transparent paths. Review the selected Firefox CONNECT gate, native type-65 API and NSS retry/certificate behavior. Keep automation's native-DNS test switch inside the temporary profile, and distinguish authenticated retry without ECH from failed certificate validation. The [browser guide](browser.md#ech-discovery-and-proxy-compatibility) describes these paths; the test's local CA and keys are isolated fixtures.

`dnsEncryption="strict"` must agree across the coordinator, broker and static firewall: no automatic or forced fallback, no plaintext leases, and no fallback-set accept rule. Retain approved split DNS, MagicDNS and literal portal access. Test both transport policies; the default recovery budget remains a separate behavior.

## Upgrade workflow

1. Identify the supplying flake input and update the intended input in the checkout. Review the lock diff and the Nixpkgs package expression, upstream source and vendored dependency changes. Preserve a working system generation.
2. Compare every affected insertion point above. Rebase local behavior or retire a redundant fix while retaining meaningful regression coverage. Keep exact-match failures; update filesets and build phases when files move.
3. Update root policy, Merge template, health/controller expectations, listener identities and VM fixtures together where their contract changes. Production identities/packages must remain those exercised by the small control VM.
4. Run the relevant check set together after editing. Build the candidate system without activation; deploy only after checks pass. On activation, restart the old browser and perform a deliberate `clash-off` → `clash-on` import before checking readiness and real traffic.
5. Update the inventory/version descriptions and affected guides to match the resulting implementation.

## Validation

The deployment script has a standalone temporary-directory check, run from the checkout with `python3 tests/test_deployment.py scripts/push-to-dir.sh`. It checks retained build inputs, excluded documentation, permissions and missing-input behavior without sudo or a host destination. It requires Python, Bash, rsync and the script's standard command-line tools; it stays separate from flake checks because `scripts/` is not deployed.

Use the attributes below from the checkout. Nix builds normally use the local daemon; the tests themselves do not need host root networking. VMs use isolated kernels and may need local KVM access. Their first dependency build is more expensive, so choose by affected behavior before running them together.

| Check (`checks.x86_64-linux.*`) | Scope |
|---|---|
| `proxy-control` | Temporary-file snapshot authorization, expiry, restart, hostile inputs and permissions |
| `proxy-regressions` | Health/state/rules/DNS leases, budget and supervisor regressions |
| `proxy-profile`, `proxy-gui` | Generated profile variants, policy/template agreement, GUI defaults and process wrapper |
| `proxy-rules`, `proxy-egress-rules` | Complete compatibility/restricted mode rules parsed by the build-time LKL nft environment |
| `proxy-dns-strict-rules` | Complete strict DNS firewall rules parsed in LKL |
| `proxy-dns-config` | Syntax of generated default/strict DNS coordinator scripts |
| `proxy-dns-strict-vm` | Real helper/core, shared DNS/proxy filtering, strict outage/override/lease and leftover-set refusal, private split permission and literal portal recovery |
| `proxy-egress-config` | Canonical CIDRs and bounded grant/time scopes; also required by normal system builds |
| `proxy-egress-vm` | Restricted counterpart of the Tailscale VM; scoped dual-stack private/multicast/DNS/DoT/NTS traffic, UID/interface/port denials, established grant revocation and C |
| `proxy-egress-control-vm` | Restricted policy before real production TUN/NAT, including allowed/denied private destinations |
| `tailscale-transport` | Selected upstream Linux bypass constant and reviewed magicsock/STUN socket marking paths; also required by normal system builds |
| `proxy-tailscale-vm` | Real selected Tailscale CLI, isolated dual-stack STUN, both uplinks, socket/packet/UID restrictions, B/C recovery and remote desktop port policy |
| `proxy-control-config` | Production/test identities, helper capabilities and generated listeners agree |
| `proxy-core` | Patched core build plus injected parser, router, target, direct/UDP/DNS and SOCKS Go tests |
| `proxy-gost` | Patched relay build and vendored HTTP parser tests |
| `browser-privacy` | Final native browser policy/locks and offline launch behavior |
| `browser-ech-vm` | Real Firefox/helper/core, native HTTPS discovery, ECH versus GREASE, key/no-ECH retry, untrusted rejection, certificate errors, cover-name target preservation and A/C behavior |
| `proxy-control-vm` | Real production helper/launcher, process capabilities, authorization, DNS and TUN/C recovery |
| `proxy-network-vm` | Broad kernel fault injection: modes, DNS, leases, sleep, guests, TPROXY and recovery |

For example, build related packages and quick checks in one invocation:

```sh
nix build --no-link .#checks.x86_64-linux.proxy-core \
  .#checks.x86_64-linux.proxy-gost .#checks.x86_64-linux.proxy-profile \
  .#checks.x86_64-linux.proxy-control .#checks.x86_64-linux.proxy-control-config \
  .#checks.x86_64-linux.proxy-regressions .#checks.x86_64-linux.proxy-gui
nix build --no-link .#checks.x86_64-linux.proxy-control-vm \
  .#checks.x86_64-linux.proxy-network-vm
nix build --no-link .#checks.x86_64-linux.proxy-egress-config \
  .#checks.x86_64-linux.proxy-egress-rules .#checks.x86_64-linux.proxy-egress-vm \
  .#checks.x86_64-linux.proxy-egress-control-vm
nix build --no-link .#nixosConfigurations.lfour.config.system.build.toplevel
```

The broad VM includes deliberate simulated/root-core fixtures; use the small control VM to verify production launch permissions. Neither changes the running host's routes/firewall. Package tests establish parser/policy behavior; they do not replace kernel-path tests. Host activation additionally needs privileged observations of actual process identity/capabilities, listener ownership, snapshot state and A → B/C transitions.
