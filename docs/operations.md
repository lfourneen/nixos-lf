# Network operations

English | [简体中文](CN/operations_CN.md) | [Documentation](README.md)

## GUI control ownership

The Clash Verge GUI uses the upstream Nixpkgs build, while its separately compiled Rust helper carries local service patches. System Proxy, TUN, and core-mode buttons stay clickable in the home page, settings, tray, and hotkeys (and may update local GUI preferences), but they are silently discarded before reaching the OS or managed core:

- **Launch shims.** System Proxy, PAC, and proxy-guard helper calls are replaced at launch with write-discarding shims. A private mount namespace masks absolute helper paths (with a memory GSettings backend and KDE 5 names as fallback), leaving OS proxy settings—including startup and exit cleanup—untouched.
- **Controller policy.** `PATCH /configs` for `mode` or `tun`, and `PUT /configs` reloads, return `204` without applying; other config writes, rule disabling, upgrades, and restarts return `403`.
- **Caller identity.** The helper checks kernel Unix peer credentials: GUI stop/restart requests cannot interrupt the managed core, and controlled-source starts belong to root.
- **Managed parser.** Before validation, the core pins `mode` to `rule` and overwrites the entire `tun` section with the root policy, so a local preference can't change live state or break a restart.

Because the GUI only reflects its local preferences, check the controller, `proxy-status`, or the managed core for actual state (missing or corrupt policy state never opens up GUI control writes). Selecting nodes inside managed groups, closing connections, delay tests, status reads, and logs all work normally. Use `clash-rules` or `clash-proxy-only` to switch routing submodes—both render core `mode: rule`, so Verge's Rule indicator only means the core is evaluating rules, not which routing submode is active (check `proxy-status` for that).

## Source registration

The private source catalog lives in Sops-encrypted [system/secrets/proxy.yaml](../system/secrets/proxy.yaml) and renders at activation to root-private `/run/secrets/proxy_source` (mode `0400`). In the Sops editor, `proxy_source` is a JSON string holding a `sources` array:

```yaml
proxy_source: |-
  {
    "sources": [
      {
        "id": "primary",
        "subscription": {"url": "https://subscription-a.example.invalid/config"},
        "providers": {}
      },
      {
        "id": "secondary",
        "subscription": {"url": "https://subscription-b.example.invalid/config"},
        "providers": {}
      }
    ]
  }
```

Each source has one main `subscription` and optional HTTP `providers` (extra subscriptions need their own ID; providers within a source must match the names and URLs declared by that subscription). Optional endpoint fields include up to four exact HTTPS `redirects` and a `user-agent` (default `clash.meta`). Setting `"gui-profile":"REMOTE_PROFILE_UID"` maps an exact UID from Verge's local `profiles.yaml`, authorizing selection without importing the GUI's URL or YAML.

### Add or switch a source

Run `sops edit system/secrets/proxy.yaml`, append an entry with a unique ID to `sources` (1–256 entries supported), deploy it, and register the new source:

```sh
sudo nixos-rebuild switch --flake .#lfour
sudo clash-source add SOURCE_ID
```

Check the printed `digest` and `previous` revision against the entry you meant to register, then type `yes` to confirm (the broker registers only that entry after rechecking that the definition and previous revision haven't changed; URLs and credentials stay private). `add` fetches via the unprivileged worker, selects the source, and waits for the verifier—in **A**, it requests proxy mode via the protected startup transaction; in **B**, it keeps the active routing policy without needing `clash-on` or `clash-rules` (explicit commands start immediately in **A** or **B**, bypassing the 90-second auto-update gate; in **C**, recover **B** or switch to **A** first).

The terminal prints `requested_source`, `active_source`, `result`, and `reason` (`accepted` exits 0; rejection, fallback, rollback, and verification timeout exit nonzero). Even after verification passes, the normal health observation window still runs—check `proxy-status` for `Active source` and `Dropped leaves` (on fallback, `active_source` can differ from what you requested).

To fetch, select, and verify an already-registered source, run `sudo clash-source use SOURCE_ID` (add `--revision REVIEWED_DIGEST` to require an exact match; fetch failures keep the prior selection, and in `proxy-only` the configured exit must still match—accept exit changes in `rules` before picking a new exit). The lower-level `review FILE --id SOURCE_ID` and `register FILE --id SOURCE_ID --digest REVIEWED_DIGEST --previous PREVIOUS_DIGEST` commands are still available (`none` as previous revision for a new entry; `review` without `--id` lists the catalog, while `register` requires `--id` when multiple entries exist).

Replacing a registration invalidates its old download and approval: a running core keeps its original source identity and the settled snapshot keeps its revision for comparisons, while a new startup or rollback needs a snapshot matching the current registration (fetch and accept the new revision before relying on offline startup; `sudo clash-source list` shows selection, active authority, revisions, settled digests, and local verification). Removing an entry from Sops leaves its local registration intact—run `sudo clash-source revoke SOURCE_ID` (optionally `--revision REVIEWED_DIGEST`) to remove it and discard queued work and snapshots (revoking the active source enters **C**, clears **B**'s lease, and stops the core; revoking an inactive source leaves the active one alone). With runtime routing in **C**, you can pick a registered replacement via `sudo clash-source use SOURCE_ID --select-only` and then run `sudo proxy-control recover`.

`clash-source-select.path` watches the GUI's `profiles.yaml` and requests the matching registered source when you switch to a mapped UID (unknown profiles do nothing, and editing GUI URLs/YAML/scripts grants no authority). If the GUI selection and active source disagree, check the logs:

```bash
journalctl -u clash-source-select -u clash-source-download -u clash-source-apply
```

In **A**, a GUI selection fetches and stages the source for the next `clash-on`.

`clash-source refresh` only prepares a candidate; to trigger the automatic fetch-and-apply pipeline directly, start `clash-source-refresh.service`. An explicit `clash-on` starts with the accepted snapshot when available and queues one pending update—once **B** stays healthy for 90 seconds under the same boot, snapshot, policy, and routing selection, the coordinator kicks off the downloader (leaving healthy **B** resets the timer; **C** keeps requests queued, and `clash-off` cancels pending requests). The root timer also checks whether the selected source is due starting about two minutes after boot, even with the GUI closed (up to three fetch attempts 15 seconds apart, 210-second service timeout; if an import or rollback is still settling, the candidate waits to apply by digest). At launch, the wrapper points registered profiles at the local controlled endpoint (restart an already-open Verge once to pick it up); Verge's refresh button uses that same queue and reports success once the candidate takes effect or proves equivalent to the running snapshot. Verge runs its own timer while open in **B**, and the root timer reads the profile's saved interval and auto-update toggle when Verge is closed (default 30 minutes; `0` disables scheduled refresh).

If the current snapshot stays authorized and intact with fresh full health, readiness, and any required **B** lease, a rejected or unchanged candidate leaves the running core and existing connections untouched (invalid authority or failed health triggers **C** recovery instead). Applying an actual change enters **C** before swapping the core over helper IPC, reruns import checks, and waits for proxy health before returning **B**. Worker failures break down into `fetch`, `parse`, `render`, or `pin-addresses` (`Subscription download` in `proxy-status` adds fixed DNS/TLS/connection/HTTP categories, HTTP status, and pending-apply status; `Profile refresh` shows audit/health verdicts; `Dropped leaves` counts exclusions; `Leaf session sample` shows auth refusals and unavailable sessions from bounded runtime probes). After authorized TLS filtering, up to `max(1, remaining_node_count // 5)` unresolvable nodes and loopback placeholders may be dropped; non-public DNS answers or literal endpoints reject the candidate, keeping a valid fallback if one exists or holding **C** otherwise.

### Recover a registered source

With runtime routing enabled and proxy intent active in **C**, you can apply the selected source's latest downloaded candidate:

```sh
sudo proxy-control recover --cached
```

This checks the download before stopping the core (it must differ from the accepted snapshot and match the current registration and routing policy, with normal audit/health checks gating **B**; both the command and `proxy-status` explain why a cached candidate was rejected). If no compatible download exists, fetch a fresh one:

```sh
sudo proxy-control recover
```

During both operations, **C** keeps ordinary app and guest internet access blocked. A dedicated resolver validates DNSSEC over configured DoT upstreams, the worker uses pinned addresses for registered HTTPS origins with TLS verification and exact redirects, and the firewall scopes recovery traffic by service identity, physical interface, source, destination, and port under a 55-second kernel lease (cut early on uplink changes or suspend). Download failure holds **C** and preserves the accepted baseline; in `proxy-only`, the download must still include the selected fixed exit (to adopt a different exit, follow the exit workflow below; on setups without runtime routing, use the manual workflow from **A**).

### Inspect subscription changes

Normal node rotation is imported automatically here. If you want to inspect how the selected source's candidate differs from its accepted baseline, run:

```sh
clash-diff
```

This reads local snapshots via `sudo` without downloading, writing approvals, or changing network mode in **A**, **B**, or **C**. It prints node addresses, ports, TLS names, groups, and rules while redacting credentials and nested transport values (use `clash-diff --json` for JSON)—it's purely diagnostic, not required before updates.

`proxy-status` displays `Profile refresh` (result, reason, audit categories/counts) and `Last auto approval` (categories, unique node count, timestamp). `fallback` means the rejected candidate was skipped and the old snapshot stayed active; `verifying` means an import is waiting on health acceptance. Structural blocks mean you need to fix the source content or local policy rather than approving routine rotation. Every five minutes, a desktop notification timer reads local state and surfaces only download failures, refused imports, rollbacks, and health failures through Noctalia using fixed categories (no node names, URLs, or credentials)—successful updates, recoveries, audit warnings, age, and GUI-copy differences never pop up notifications.

### Portal login

To log into a captive portal, switch explicitly to **A** (`clash-off`) and run `firefox-portal http://192.168.1.1/` with the actual literal portal IP. It opens an extension-free disposable profile behind a single-IP port-80/443 relay for up to 15 minutes (redirects to other IPs fail—review the target and open a separate session if needed), wiping its data on exit. Daily Firefox can keep using **A**; switch back to **B** before launching `firefox-identity`. Keep an offline-bootable generation and a backup of your registration key—`firefox-portal` doesn't create a recovery generation or alter DNS fallback policy.

## Switching modes

Use `clash-on` and `clash-off` in Nushell. With runtime routing, `clash-on` allows up to 90 seconds per core startup for DNS/TUN warmup and fresh readiness proof; if a restored snapshot repeatedly fails data-plane checks, `clash-on` can try one compatible cached update in **C** (and with `my.proxy.startupRecoveryFetch` enabled here, one scoped download in **C** if the cache isn't usable—all sharing a 210-second readiness deadline from the first core start). Closing the GUI leaves proxy intent and the core active; run `clash-off` to stop managed proxy access.

`clash-rules` and `clash-proxy-only` pick a routing submode and request **B** in one step (allowing up to 90 seconds after core startup to confirm readiness), printing the result or failure stage in the terminal without opening the GUI, sending notifications, or running `proxy-status` (underlying commands: `sudo proxy-control routing rules` and `sudo proxy-control routing proxy-only`). `clash-off` preserves the choice for the current boot, `clash-on` reuses it, and rebooting resets to `rules`. `proxy-only` requires an accepted pinned source—if preparation or full health fails, **C** stays up and the selection remains ready to retry.

Use `clash-egress` to list accepted-source exit IDs and `clash-egress APPROVED_NODE_ID` (full ID from the list) to select one and request strict **B** (root equivalents: `sudo proxy-control egress list` and `sudo proxy-control egress APPROVED_NODE_ID`). Switching exits goes through **C** to close old streams and associations, survives `clash-off`/`clash-on` for the current boot, and requires accepting source updates in `rules` first if the chosen node's definition changes (see [routing policy](proxy.md#routing-policy)).

Underneath, `sudo proxy-control on` opens the single-use import window and starts the helper (using the registered candidate with controlled sources, or GUI-supplied config in compatibility mode), while `sudo proxy-control off` clears authority, stops the helper, and verifies direct firewall/routing policy (`clash-off` also closes the GUI in the background; check direct DNS health separately with `proxy-status`). Launching `clash-verge` alone in **A** never authorizes a core.

| Command | Purpose |
|---|---|
| `sudo proxy-control preflight` | Check controlled-source config and assets without changing intent |
| `proxy-policy` | Show network permissions and policy |
| `proxy-control status auto --check` | Check readiness for the requested mode |
| `proxy-control status proxy` | Show proxy status |
| `proxy-control wait proxy` | Wait up to 30 seconds for proxy readiness |
| `proxy-control status direct` | Show direct-access status |
| `firefox --check` | Check browser prerequisites without opening a window |

`status auto` tracks the requested mode, `--check` exits nonzero when not ready, and `wait` prints progress every 10 seconds.

| Tool | Output |
|---|---|
| `proxy-status` | Mode, DNS, readiness, failed probe layer, refresh result, and audit counts |
| `clash-diff` | Candidate changes against the selected source's accepted baseline |
| `proxy-status --plain` | Plain text suitable for logs |
| `proxy-status --json` | Structured status |
| `proxy-inspect` | Passive service, link, queue, and resource diagnostics |
| `proxy-inspect --posture` | Listener bindings, declared ingress, and local trust settings |
| `egress-audit` | Additional active network probes |

Pass `--no-color` or set `NO_COLOR` to strip colors from `proxy-status`.

`proxy-policy` displays native UID/interface/CIDR grants alongside core `DIRECT` ranges, remote target grants, and subscription trust. Remember those are separate planes: a restricted native policy paired with broad core `DIRECT` ranges still lets clients hitting a local proxy inlet use those core ranges—to lock down host LAN access completely, set both `directEgress.mode="restricted"` and `localTargetCidrs=[]`, then grant only what you need. `proxy-inspect` reports command-failure categories and exit codes without leaking arbitrary service stderr.

`proxy-inspect --posture` lists IPv4 and IPv6 listeners in the caller's network namespace separately from declared ingress rules for LAN, tailnet, and routed guests (a proxy-port hint flags items to review without connecting to them; `--json` adds structured results and Unix socket metadata for control, browser, and container endpoints). To inspect startup trust overrides, run `proxy-inspect --posture --pid PID` or `sudo proxy-inspect --posture --privileged` (the report shows tracked variable names and comparison status, system CA bundle digest, SSH state, and readable Tailscale settings; run from the desktop session to include its stored proxy mode). The desktop config asserts that OpenSSH stays disabled and declared host ingress omits TCP/22.

## Remote desktop

Sunshine starts with `graphical-session.target` and exposes the session as **Desktop**. After deploying, log into the desktop, open `https://localhost:47990` locally to set credentials and approve Moonlight's pairing PIN, log both phone and computer into the same permitted tailnet, and add the computer's Tailscale IP manually in Moonlight (discovery isn't needed; `trustedLanCidrs` defaults to empty so physical LAN IPs aren't exposed).

The web UI accepts local clients only, UPnP is off, and streaming uses the explicit tailnet TCP/UDP port lists in [options.nix](../system/config/network/options.nix) (emptying a protocol list closes all incoming services for it; removing a port cuts existing peer connections on that port while leaving other authorized services and outbound connections intact). Configure phone-to-computer grants separately in the Tailscale admin console. **A** and healthy **B** allow remote desktop, whereas **C** cuts the session (including live UDP flows and decrypted tailnet input)—use `clash-off` locally if you want direct **A**.

[sunshine.nix](../system/programs/sunshine.nix) picks the CUDA build variant, with `https://cache.nixos-cuda.org` providing binaries matching the lockfile (verify cache hits without local builds via `nix build --max-jobs 0 --no-link .#nixosConfigurations.lfour.config.services.sunshine.package`). Credentials and pairing state live in the user's Sunshine directory, while the Desktop entry and service settings are declarative—verify hardware capture/encoding and phone streaming in the live graphical session:

```sh
systemctl --user status sunshine.service
journalctl --user -b -u sunshine.service
sudo tailscale netcheck
sudo tailscale ping <phone-tailnet-name-or-IP>
```

See [Direct egress](egress.md) for optional restricted **B** (which keeps negotiated Sunshine media), the Unbound DoT toggle, and time destination pins (**C** always cuts remote control). If Tailscale socket marking fails, **B** blocks unmarked root UDP—check service permissions and socket marks, or use `clash-off` locally for direct access (upgrade checks are in [maintenance](maintenance.md#validation); dedicated guest accounts keep their own boundary).

## State and diagnosis

| File | Meaning |
|---|---|
| `/run/proxy-mode/state.json` | Atomic mode, reason, policy version, revision, and update time |
| `/run/proxy-mode/routing.json`, `routing-active` | Boot-bound requested/applied submode, epoch, immutable policy digest, and phase; selected policy/health/audit artifacts |
| `/run/proxy-mode/routing-firewall.json` | Root-only expected outer firewall model and selected transport script digest |
| `/run/proxy-mode/health.json` | Probe scope, full verdict, separate HTTP/route/DNS/TUN diagnostics, proof sources, and per-layer durations |
| `/run/proxy-mode/heartbeat.json` | Last successful supervisor round |
| `/run/netsec/state.json` | Verification verdict, checked generation, and freshness |
| `/run/dns-upstream/state.json` | DNS mode/reason, encryption policy, session, budget, deadline, and override |
| `/run/dns-policy/state.json` | Broker lease state and applied generation |
| `/run/gost-relay/state.json` | Relay observation and applied generation |
| `/run/netsec/alerts/*.json` | Per-owner alarms (`/run/netsec/failed` lists owners) |
| `/run/proxy-mode/intent`, `core-seen` | Boot-scoped operator intent and crash-detection marker |
| `/run/proxy-mode/authorized-config`, `config-digest`, `authorize-window` | Root-only snapshot authority and single-use import grant |
| `/run/proxy-mode/profile-refresh.json` | Last refresh result, digests, fixed reason, audit counts, last automatic approval, and 20-entry history (mode `0644`) |
| `/run/proxy-mode/profile-refresh-pin.json` | Rejected candidate digest, health-failure marker, and one-hour TTL (mode `0644`) |
| `/run/proxy-mode/source-fetch.json` | Boot-bound download outcome, fixed failure category, and optional HTTP status (mode `0644`) |
| `/run/proxy-mode/profile-refresh-verifying.json` | Root-only pending transaction with the previous snapshot path |
| `/run/proxy-mode/profile-refresh-settled.json`, `profile-refresh-commit.json`, `profile-refresh-restart.json` | Root-only settled authority, interrupted commit recovery, and pending core recovery |
| `/run/proxy-mode/effective-config.json`, `boot-id` | Normalized effective YAML digest bound to the shared boot identity (`effective-config-sha256` is a compatibility mirror) |
| `/run/proxy-mode/health-observations.json` | Last 20 full probe outcomes in this boot, with sanitized stages, layer timings, and snapshot digests |
| `/run/proxy-mode/health-failure.json` | Last failed probe with fixed reason, optional sanitized `failure_stage`, layer timings, boot identity, and snapshot digest (preserved after recovery) |

Atomic JSON is the primary consumer interface (standalone `status`, `reason`, `version`, `result`, and `verified-version` files are compatibility mirrors). Reason-only updates bump `revision`, kernel-policy commits bump `policy_version` (`version`), and results from older generations are ignored.

Readiness requires current-generation mode, DNS, broker, and relay observations, zero active alarms, and fresh verification (`ok` and `degraded` both mean hard checks passed, with `degraded` showing `Passed · warnings`; failed or unknown verdicts block readiness). Broker state must be `applied`, **B** requires DNS status `proxy`, and **A** under strict DNS accepts `dot` or `dot-priming`. Consumer freshness defaults to 120 seconds, verifier freshness to 420 seconds (covering its 5-minute timer and bounded run), and boot IDs plus monotonic timestamps reject cross-boot, future, and stale records.

| Reason | Where to look |
|---|---|
| `request`, `reload` | Transition awaiting core health and verification |
| `unauthorized-core`, `core-gone` | Operator intent, launcher/helper, and core cgroup |
| `no-tun`, `tun` | Device, managed routing table/rules, real route selection, and fake-IP HTTP path |
| `config` | Snapshot digest, managed API values, and TCP/UDP listener identity |
| `route` | Actual logged leaf is `DIRECT`/`REJECT` or inconsistent with the selected graph |
| `obs`, `obs:1/3`, `obs:2/3`, `obs:3/3` | HTTP completion, exact source-port log, and selection observations |
| `resolver-error`, `split-error` | DNS listener identity or approved Wi-Fi profile/lease |
| DNS `proxy-priming`, `degraded-proxy` | **B** is confirming the selected core resolver or its probes failed; check the core DNS listener and selected DoH path |
| DNS `degraded-dot-only` | Check **A**'s authenticated DoT upstreams and DNSSEC probe result |

```sh
journalctl -b -u proxy-mode -u dns-upstream -u gost-relay -u clash-verge
cat /run/proxy-mode/health.json
cat /run/dns-upstream/state.json
cat /var/lib/dns-upstream/history.log
```

Some logs and authority files require `sudo`—never share snapshots or subscription-bearing configs in diagnostic output.

### Drop counters

Run `sudo proxy-inspect --drops` for a focused JSON view of existing refusal counters in `inet lf_filter` (`proxy-inspect` includes the same under `drops`), reading local state and `nftables` without modifying rules, resetting counters, or sending probes:

```sh
sudo proxy-inspect --drops | jq '{available,error,policy,state_consistent,groups:[.groups[] | select((.packets // 0) > 0)]}'
```

Each group reports a stable `reason`, `role` (routed guests use configured interface names), `chain`, and packet/byte counts alongside the observed policy mode, reason, and generation (`state_consistent=false` means state was unreadable or changed mid-collection; this view doesn't prove readiness).

| Reason | Counted refusal |
|---|---|
| `mode-c-output`, `mode-c-input` | **C** output or reply/input guards |
| `guest-source-policy` | Guest source outside its declared scope, including an undeclared address family |
| `guest-target-policy` | Routed guest protected or unsupported target |
| `dns-client-policy` | Application access to an internal resolver inlet |
| `dns-listener-identity`, `proxy-listener-identity` | Replies from an unauthorized local socket UID |
| `proxy-inlet-access` | Attempts to connect directly to protected local proxy ports |

Counts only cover drops with counters in those chains (`uncounted_rules` reports selected drops without counters; `installed=false` with `null` counts means the chain is missing, and zero counts don't prove an inlet is enabled or healthy). Read/parse errors return `available=false`, a fixed error category (`permission-denied` if lacking `nftables` privileges), empty groups, and exit `1`. Counters start from each rule's last install or flush—reload, repair, and mode changes can reset parts of them—and historical totals never attribute a specific connection or original/NAT destination to the current mode. Output group count is fixed by configured roles with no packet logs, target addresses, or raw rules.

### Refresh status

`proxy-status` splits update info across `Auto update` (schedule), `Profile refresh`/`Update result` (latest refresh), and `Profile vs runtime` (GUI copy—display-only for controlled sources, or GUI-to-snapshot digest diff for compatibility imports), while `proxy-policy` includes `profile_refresh` and `proxy-inspect` exposes up to 20 sanitized history entries.

`Subscription download` separates fetching/download errors from import: `deferred` means network protection postponed the attempt (not a failure, no popup), `Content audit` shows automatic content approval (not health acceptance), `restored` means startup reused an accepted snapshot, `verifying` means import is waiting for health acceptance, `fallback` means the rejected candidate was skipped in favor of the prior snapshot, and `rolled_back` means authority was restored and a core swap requested (check readiness fields to confirm core recovery). Failed latest updates are shown separately from subscription age so the prior acceptance time stays visible (controlled sources follow root records; GUI timestamps don't prove root success), and differing digests only mean differing content, not which file is newer. Subscription switches share the same root policy without expanding `subscriptionAuditAllowedHosts`; if protected routes stay indeterminate, `enforce` keeps the old snapshot so you can inspect the audit category before adjusting policy.

Every five minutes, the user notification timer reads local files and combines update/health failures into one message per round—deduplicating refresh failures by result and full digest, download failures by attempt time, and health failures by state transition (storing state privately in `$XDG_STATE_HOME/clash-verge/refresh-notified.json` only after delivery succeeds, and never fetching subscriptions or popping up on success, recovery, audit warnings, age, or GUI-copy diffs).

### Subscription reachability

Run `proxy-subscription --check --json` to test the current subscription directly: it reads the URL from the GUI index and reports HTTP status, transport, elapsed time, bytes, HTTPS, and redirect count (pass `--etag` for conditional GET). It tries the permitted direct path first and falls back to the configured mixed port only on transport failure (complete HTTP replies like `403` are reported straightaway without retrying over proxy), without writing profiles, importing config, or advancing timestamps. The default total deadline is 15 seconds (`--timeout` accepts 1–120 s), capped at 8 MiB and three same-origin redirects (cross-origin redirects and HTTPS downgrades are refused), and output omits URLs, bodies, and validators. `proxy-inspect --deep` adds service restart counts and a fixed journal query for privileged troubleshooting.

## Coordination and firewall repair

Socket-directory events, TUN link monitoring, `nft` events, NetworkManager dispatch, and `dhcpcd` hooks wake the supervisors, backed by adaptive polling (2 s during transitions, up to 5 s in enforced modes, 60 s in direct mode) and a 90-second mode-supervisor watchdog (cancelled probes don't count as failures).

Build-time [expected rules](../system/config/network/proxy/coordinator.nix) are generated with the same `nft` binary inside an LKL userspace kernel. The verifier compares owned tables, hooks, priorities, policies, and full ordered expressions (normalizing handles, counters, set-element order, and tightly bounded DNS leases; read/parse errors fail, and a properly installed **C** passes even with no core). `nftables-verify` publishes failures/alarms while exiting `0` so repair/rebuild jobs can continue—always check the JSON verdict, not just the unit exit code (each component clears only its own alarm, and queued verification requests survive busy rounds).

```sh
sudo systemctl restart nftables.service
sudo systemctl start nftables-verify.service
```

Restart/reload reinstalls the owned static policy plus the safety mode atomically (proxy intent passes through **C** before health reopens **B**, and `inet lf_policy_guard` keeps proxy-intended traffic blocked if the main table vanishes—before a live rollback to a generation predating `inet lf_policy_guard`, switch to **A** with `clash-off` first to clear its per-packet checks). Stopping `nftables` leaves its policy active; `nftables-verify-repair.service` explicitly repairs and rechecks, while `nftables-recover` retries a failed `nftables` unit and resumes enabled network/time services whose boot jobs were cancelled by the firewall dependency. Never flush the global ruleset.

## Firewall table migration

`proxy-control migrate-firewall` explicitly removes `inet filter`, `ip proxymode_nat`, and `ip6 proxymode_nat` while installing the [reserved tables](network.md#firewall-lifecycle) (normal startup, reload, and repair leave those three names alone). Only run it after confirming those tables actually belong to this configuration:

```sh
sudo nft list table inet filter
sudo nft list table ip proxymode_nat
sudo nft list table ip6 proxymode_nat
```

Once confirmed, run the migration and verify:

```sh
sudo proxy-control migrate-firewall
sudo systemctl start nftables-verify.service
```

This root command holds the operator and policy locks and commits all three deletions, static rules, and the safety mode in one `nft` transaction (leaving existing rules untouched if rejected, and tolerating missing targets so re-runs work). Proxy intent or a running core installs **C** first before **B** opens; direct intent with no core installs **A**—preserving intent, authorization, and any running core, while leaving other services' tables untouched.

## Core namespace recovery

On the host, the `Mihomo` interface is the managed `veth`—inspect the actual core TUN and `auto-redirect` table with `sudo ip -n lf-clash-core link` and `sudo ip netns exec lf-clash-core nft list table inet mihomo`, and use `proxy-control status proxy --check` for the full readiness contract (a host interface alone doesn't prove readiness).

`clash-on` stops the old helper, rebuilds the private namespace, preserves source authorization, and drops existing core connections; physical uplink changes and resume restart the authorized core automatically. If a LAN or VPN overlaps the internal link, assign an unused private `/30` and `/64` via `my.proxy.coreLink` (adjusting each subnet and its host/core endpoints together—never disable the overlap check). Internal MACs are distinct local unicast addresses independent of physical MAC randomization. Keep a working previous generation before activating changes.

To roll back to an older generation that uses a host-owned TUN:

1. Run `sudo proxy-control off` and wait for **A**.
2. Stop `clash-core-network.service` (its cleanup removes only its own namespace, `veth`, and route priorities, refusing to delete foreign interfaces or a legacy TUN).
3. Activate the chosen generation and start it with `clash-on`.

## Configuration

| Source | Settings |
|---|---|
| [machine.nix](../system/config/machine.nix) | User, interfaces/match keys, UIDs, ports, mark, address ranges, and upstream endpoints |
| [options.nix](../system/config/network/proxy/options.nix) | TUN, route table 2022, fake-IP pool, probe URLs, boot intent, `proxyOnly`, `dnsEncryption`, shared domain filtering, `localTargetCidrs`, `remoteTargetGrants`, `nat64Prefixes`, guests, split DNS, and freshness |
| [profile.nix](../system/config/network/proxy/profile.nix) | Root-owned managed DNS/TUN/profile contract |
| [routing.nix](../system/config/network/proxy/routing.nix) | `runtimeRouting`, immutable rules/proxy-only policies, and `proxyOnlyDnsUpstreams` |
| [options.nix](../system/config/network/options.nix) | Host/guest private access, per-interface guest trust/sources, dedicated socket guest accounts, host-service ports, hotspot, tailnet, and Wi-Fi MAC policy |
| [cvr-merge.nix](../home/config/clash/cvr-merge.nix) | Matching Clash GUI Merge template and routing enhancement |
| [firefox.nix](../home/programs/firefox/firefox.nix), [options.nix](../home/programs/firefox/options.nix) | Native browser, `networkPath`, `nvidiaAcceleration`, privacy, policy, fonts, and outer sandbox |

TUN defaults (`Mihomo`, table 2022, rule priority 9000, internal marks 8227/8228, fallback priority 32768, file descriptor 0) are shared across parser, template, and health checks; `tunMode=false` selects HTTP-only/`redir-host` without a TUN, and the fake-IP network is derived from `fakeIpRange` (don't edit consumers separately).

`runtimeRouting` defaults to `false` in the module and is enabled here—requiring controlled sources, local DNSSEC, strict encrypted DNS, an isolated TUN, kernel authorization leases, and `proxyOnly=false` as the `rules` boot default (legacy `proxyOnly` remains a build-time option without runtime switching). `proxyOnlyDnsUpstreams` takes authenticated literal-address HTTPS resolvers always routed through the remote group. Accepted snapshots are kept separately under `approved/rules` and `approved/proxy-only` with the raw `Rule` data and pinned nodes needed for local re-rendering when probe settings change (if an old snapshot's raw rules can't be recovered safely, refresh the registered source and import the new download).

Core access to internal services is handled separately by [target authorization](proxy.md#domain-targets) (`localTargetCidrs` for local `DIRECT`, `remoteTargetGrants` for remote leaves, and `nat64Prefixes` for translation prefixes; internal suffixes only permit locally resolved private answers without granting node-side lookup or dialing; Gost `CONNECT` arrives as `HTTPS`, and policy changes need a rebuild plus deliberate core restart; **A** keeps literal portals, **C** stays blocked, and native [direct-egress grants](egress.md) remain independent).

For separate guest trust classes, mark a development bridge explicitly and grant other guests only the private services they need; host-socket backends need their own dedicated account with a free UID and non-overlapping subordinate ranges:

```nix
my.hardening = {
  guestNetworks.virbr0.trust = "trusted";
  guestPrivateExceptions = [{
    interface = "waydroid0";
    cidr = "192.168.1.20/32";
    tcpPorts = [ 443 ];
  }];
  socketGuestUsers = [ "guestnet" ];
  socketGuestPrivateExceptions = [{
    user = "guestnet";
    interface = "ens1";
    cidr = "192.168.1.20/32";
    tcpPorts = [ 443 ];
  }];
};
users.users.guestnet = {
  isNormalUser = true;
  uid = 1100;
};
```

Replace the placeholder addresses above. Built-in bridge sources/gateways come from `my.machine`, extra `guestInterfaces` need canonical, disjoint `guestNetworks` source CIDRs, add `hostTcpPorts`/`hostUdpPorts` only when a service should listen on its declared gateway, IPv6 requires explicit ULA sources/gateways (public guest IPv6 stays refused), and hotspots stay restricted unless their own trust is changed. Install the full `nft` policy before starting `pasta` or rootless Docker under `guestnet` (the Docker user unit strips inherited explicit proxy variables for restricted accounts and uses the declared rootless package/settings), stop running backends before changing identities or delegated UID mappings, and to revoke private access for a running backend, keep the identity declared, remove its grants, and reload the firewall (cutting both tracked directions). Changing `socketGuestUsers` alone won't migrate desktop containers; those accounts use the separate real-address DNS path with no plaintext/split-DNS fallback (see [guest boundaries](network.md#guests-and-containers)).

Wi-Fi profiles with an explicit cloned MAC override `my.hardening.wifi.clonedMacAddress` (which otherwise defaults to `stable-ssid`, keeping the same MAC per SSID; `permanent` uses hardware MAC, `preserve` keeps the current one). For coordinated changes, declare `my.hardening.wifi.identities`, run `network-identity plan UUID`, disable autoconnect, and disconnect before `apply` or `restore` (see [Link identity](identity.md) for full steps, wired policy, and DHCP state setup).

## VirtualBox

VirtualBox uses its native driver and GUI alongside QEMU/KVM and libvirt—log out and back in after activating to join `vboxusers`. Attach the VM's NIC to **Host-only Adapter** on `vboxnet0` using DHCP (if VirtualBox's Network Manager has its own DHCP server enabled on `vboxnet0`, disable it so the system DHCP service handles leases). Guests get `192.168.56.1` as gateway and managed DNS: public IPv4 goes direct in **A**, through TPROXY in healthy **B**, and is blocked in **C**, while `guestNetworks.vboxnet0` governs private targets and host services (attaching another adapter changes the VM's network paths).

```sh
systemctl status vboxnet0.service vboxnet-dnsmasq.service
VBoxManage list hostonlyifs
```

## Hermes containers

[`system/programs/hermes/`](../system/programs/hermes) holds the agent config, skills, mail CLI, and container backend. The backend runs `hermes-docker.service` in the dedicated account's user manager, while `hermes-containers.socket`/`.service` expose a fixed Unix stream to Hermes with the account's private runtime and data directories isolated.

| Resource | Default location |
|---|---|
| Hermes agent data and workspace | `/var/lib/hermes` |
| Restricted backend home / Docker data | `/var/lib/hermes-containers` / `/var/lib/hermes-containers/docker` |
| Hermes Docker API | `unix:///run/hermes-containers/docker.sock` |

Gateway, dashboard, and Hermes login shells point `DOCKER_HOST` at that `0660` socket (restricted to the backend account and `hermes-container-clients`), while the desktop keeps its own Docker endpoint and local terminal/MCP/browser tools running directly as Hermes keep the host proxy policy. The backend clears inherited proxy variables and uses filtered real-address DNS: **A** allows public IPv4 directly, **B** routes public traffic through the managed core, **C** cuts application traffic, private/metadata/fake-IP targets and host proxy inlets have no default grant, and the default Docker bridge has no external IPv6 path (other supported native backend paths keep the UID guard's dual-stack checks).

```sh
sudo systemctl status hermes-containers.socket hermes-containers.service
sudo systemctl --user --machine=hermes-containers@.host status hermes-docker.service
sudo -u hermes env DOCKER_HOST=unix:///run/hermes-containers/docker.sock docker info --format '{{.DockerRootDir}}'
```

Before starting the new account's user manager, the retirement unit stops Hermes's old user Docker and refuses startup if an old `dockerd` is still running (the global Docker user unit excludes both Hermes and the dedicated backend, which uses its own unit and inherits `user.slice`'s reserved-port bind limits). Existing agent data in `/var/lib/hermes` and the old account's Docker images/volumes stay untouched—they are not imported automatically. To migrate container data:

1. Stop writes and export images and volumes using the old account's UID mapping before activation.
2. Load images and restore volumes via the new Docker endpoint.
3. Verify the restored data before removing the old data or UID mapping.

A recursive `chown` is not a substitute for exporting and restoring. Before rolling back to an older backend setup, stop the dedicated daemon and both API units.

## Hermes time MCP

The `time` MCP keeps its stdio interface using the configured NixOS timezone: the stdio bridge clears its environment and connects to `/run/hermes-time/mcp.sock` (accessible to root and `hermes-time-clients`), where each connection spawns a `hermes-time@.service` instance in `untrusted.slice` under `hermes-time` with a read-only Nix store, temporary `/work`, and a private network namespace limited to Unix sockets.

At systemd startup, the gateway and dashboard clear inherited proxy/CA override keys and use the declared system CA bundle (compatibility mode restores the approved local Gost proxy for the gateway, while networked MCP and browser tools use their declared per-tool options; `proxy-inspect --posture` reports the startup environment, and Hermes loads its profile `.env` in-process where overrides can apply after startup).

```sh
systemctl status hermes-time.socket
systemctl list-units 'hermes-time@*.service'
```

## Offline recovery

If you intentionally want direct access, run `clash-off`. For portal or DNS recovery in **A** under the default `dnsEncryption="opportunistic"`, check DNS state/history and enable the bounded-renewal override only if needed:

```sh
sudo touch /run/dns-upstream/force-plaintext
# Remove after recovery; reboot also clears it.
sudo rm /run/dns-upstream/force-plaintext
```

This allows plaintext DNS while still billing the session budget, and never overrides **C**'s egress guard (see [DNS](dns.md)). Under `my.proxy.dnsEncryption="strict"`, the override is ignored and no plaintext fallback is granted (`clash-off` still selects **A** with encrypted DNS)—try a literal HTTP portal IP, switch uplinks, or fix the declared DoT upstreams (if a portal depends on broken public DNS, boot into a pre-built `opportunistic` generation, so keep one handy before travelling; private split DNS and MagicDNS still follow their normal approvals and mode rules).

If a system generation itself won't boot or is broken:

1. Pick an older entry from the `systemd-boot` menu (it keeps eight entries).
2. If the generation you need isn't in the menu, check `/nix/var/nix/profiles/system-*-link` and install its boot entry as root:

   ```sh
   nix-env -p /nix/var/nix/profiles/system --set /nix/store/<nixos-system-path>
   /nix/var/nix/profiles/system/bin/switch-to-configuration boot
   ```

3. If nothing installed boots, boot a NixOS ISO, unlock and mount root and the ESP, and enter the system with `nixos-enter --root /mnt`.

## Configuration checks

See [Patch maintenance](maintenance.md#validation) for the checks covering configuration and dependency changes.
