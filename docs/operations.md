# Network operations

English | [简体中文](CN/operations_CN.md) | [Documentation](README.md)

## GUI control ownership

The Clash Verge GUI uses Nixpkgs's upstream build. The separately compiled Rust helper carries the local service patches.
Its System Proxy, TUN and core-mode controls remain clickable in the home page, settings, tray and hotkeys, and may update local GUI preferences.
They are silently discarded before they reach the OS or the managed core:

- System Proxy, PAC and proxy-guard helper calls are replaced at launch by write-discarding shims.
  Absolute helper paths are masked in a private mount namespace, with a memory GSettings backend and KDE version-5 names as fallback.
  OS proxy settings, including startup and exit cleanup, stay unchanged.
- Controller `PATCH /configs` requests for `mode` or `tun`, and `PUT /configs` reloads, return 204 without being applied.
  Other configuration writes, rule disabling, upgrades and restarts remain forbidden.
- The helper identifies callers from kernel Unix peer credentials. GUI stop/restart requests cannot interrupt the managed core; controlled-source starts belong to root.
- The managed core pins its mode to `rule` and its whole TUN section to the root policy before validation, so a local preference cannot change live state or make a restart fail.

The GUI may display its local preference. Use the controller, `proxy-status` or the managed core for the actual state.
Node selection inside managed groups, connection closing, delay tests, status reads and logs remain available.

Use `clash-rules` or `clash-proxy-only` to request the system routing submode.
Both render core `mode: rule`; the GUI's Rule indicator is the core's rule evaluator, not the selected routing submode.
Use `proxy-status` for the network overview and the active routing submode in B.
Missing or corrupt policy state does not enable GUI control writes.

## Source registration

The private source catalog is the Sops-encrypted [system/secrets/proxy.yaml](../system/secrets/proxy.yaml), rendered at activation to root-private `/run/secrets/proxy_source` (mode 0400).

In the Sops editor, `proxy_source` is a JSON string containing the sources array:

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

Each source has one main `subscription` and optional HTTP providers.
Give an additional subscription its own ID.
Providers within a source must match the names and URLs declared by that subscription.

Optional endpoint settings include up to four exact HTTPS `redirects` and a `user-agent` value, defaulting to `clash.meta`.
A source's `"gui-profile":"REMOTE_PROFILE_UID"` maps the exact UID from the GUI's local `profiles.yaml`.
The mapping authorizes selection, without importing the GUI's URL or YAML.

### Add or switch a source

Run `sops edit system/secrets/proxy.yaml` and add one entry to `sources`. Use a unique ID. The catalog supports 1–256 entries. Deploy it and add the requested source:

```sh
sudo nixos-rebuild switch --flake .#lfour
sudo clash-source add SOURCE_ID
```

Compare the reported `digest` and `previous` revision with the entry you intend to register, then type `yes` to confirm. The broker registers only that entry and checks that the reviewed definition and the previous revision are still current. The digest covers the canonical entry. The command prints digests and source IDs, while the URL and credentials stay private.

`add` downloads through the unprivileged worker, selects the downloaded source and waits for the existing verifier. A requests proxy operation through the protected startup transaction. In B the existing routing policy is used and no separate `clash-on` or `clash-rules` is needed. An explicit command starts immediately in A or B. The 90-second stability gate still applies to automatic updates. If C is active, recover B or explicitly choose A before downloading.

The terminal result gives `requested_source`, `active_source`, `result` and `reason`. An accepted import reports `accepted` and exits zero. Rejection, fallback, rollback and a verification timeout exit nonzero. Successful verification still requires the normal health observation period. Use `proxy-status` to see the active source and `Dropped leaves` count. If the import falls back, the active source can differ from the requested source.

Use `sudo clash-source use SOURCE_ID` to fetch, select and verify an existing registration. Add `--revision REVIEWED_DIGEST` to require an exact registered revision. Fetch failure preserves the prior selection. In `proxy-only`, the configured exit still has to match. Accept changes to a pinned exit in `rules` before selecting the new exit.

The lower-level `review FILE --id SOURCE_ID` and `register FILE --id SOURCE_ID --digest REVIEWED_DIGEST --previous PREVIOUS_DIGEST` commands remain available. Use `none` as the previous revision for a new entry. Review without `--id` lists the catalog digests, and registration requires an ID when more than one entry exists.

Replacing a registration invalidates its previous download and approval. A running core keeps its original source identity. The settled snapshot retains its original revision for comparisons, while a new startup or rollback requires a snapshot from the current registration. Download and accept the new revision before relying on offline startup. `sudo clash-source list` reports the selection, active authorization, registered revisions, settled digests and local verification result.

Removing an entry from the Sops catalog leaves its registration intact. Run `sudo clash-source revoke SOURCE_ID` to remove the local registration and discard its queued work and snapshots. Use `--revision REVIEWED_DIGEST` to bind the operation to a specific revision. Revoking the active source enters C, clears the B lease and stops its core. Revoking an inactive source leaves the active authorization in place.

With runtime routing, select a registered replacement in C with `sudo clash-source use SOURCE_ID --select-only`. Run `sudo proxy-control recover` to recover the selected source through the existing C recovery path.

The `clash-source-select.path` unit watches the GUI's `profiles.yaml`.
Switching to a mapped UID requests the corresponding registered source.
Unknown profiles leave the selection unchanged.
Changing a GUI URL, YAML or script grants no additional authority.
Inspect the request, download and application logs when the GUI selection and active source differ.

```bash
journalctl -u clash-source-select -u clash-source-download -u clash-source-apply
```

In A, a GUI request downloads and selects the source for the next `clash-on`.

`clash-source refresh` only prepares a candidate.
To request the automatic download and application flow directly, start `clash-source-refresh.service`.
An explicit `clash-on` uses the accepted snapshot when available and records one pending update request.
The coordinator starts the downloader after B remains healthy and verified for 90 seconds under the same boot, snapshot, policy and routing selection.
A transition out of healthy B restarts this wait.
In C, the request remains queued and `proxy-status` reports the deferral. `clash-off` cancels pending requests and applications.
The root timer checks when the selected source is due starting about two minutes after boot, independently of the GUI.
A download job allows up to three attempts, spaced 15 seconds apart, with a 210-second service timeout.
While an import or rollback is being settled, the downloaded candidate waits for application by digest.
At launch, the wrapper maps registered profiles to the local controlled subscription endpoint. Relaunch an already-open copy of Verge once to load the mapping. Verge's refresh button uses the same queue, and the endpoint reports success once the candidate takes effect or the broker proves it equivalent to the effective snapshot. See `proxy-status` for failed download and application verdicts. Verge runs its native timer while open in B. The root timer reads the selected profile's saved interval and automatic-update setting when Verge is closed. The default interval is 30 minutes. An interval of zero disables scheduled refresh.

If the current snapshot remains authorized and intact, with fresh full health, readiness and any required B lease,
a rejected or unchanged candidate leaves the running core and existing connections in place.
Invalid current authority or failed health follows C recovery instead.
Activation enters C before replacing the core through helper IPC, repeats import checks and requires proxy health before B returns.

Worker failures identify `fetch`, `parse`, `render` or `pin-addresses`.
`Subscription download` in `proxy-status` adds a fixed DNS, TLS, connection or HTTP failure category and an HTTP status when available.
`Subscription download` also reports when a candidate is waiting to apply. `Profile refresh` reports the audit and health verdict.
`Dropped leaves` counts import exclusions by reason. `Leaf session sample` reports authentication refusals and unavailable sessions observed during bounded runtime checks.
After authorized TLS filtering, the worker may drop unresolved nodes and literal loopback placeholders up to `max(1, remaining_node_count // 5)`.
DNS answers containing non-public addresses and other non-public literal endpoints refuse the candidate.
Fetch/parse/audit failure preserves a valid runtime fallback.
C stays active if no valid fallback remains.

### Recover a registered source

With runtime routing enabled and proxy intent retained in C, apply the selected source's latest downloaded candidate with:

```sh
sudo proxy-control recover --cached
```

The command checks the download before stopping the core. The download must differ from the accepted snapshot and match the current registration and routing policy. The normal audit and health checks gate B.
The command and `proxy-status` report why a checked cache is unavailable.
If no compatible download is available, fetch a fresh candidate:

```sh
sudo proxy-control recover
```

C continues to block ordinary application and guest internet access during both operations.
A dedicated resolver validates DNSSEC and uses the configured authenticated DoT upstreams. The download worker uses pinned addresses for registered HTTPS origins, validates their TLS certificates and follows only exact registered redirects.
The firewall limits recovery traffic by service identity, physical interface, source address, destination and port. The kernel lease expires within 55 seconds and cuts existing recovery connections. Uplink change notifications and suspend revoke it early.
The command revokes the lease before staging the candidate under the requested routing policy. Download failure keeps C and retains the accepted baseline. B requires the normal content audit and health checks.
For `proxy-only`, the download must still contain the selected fixed exit. To adopt a different exit from the source, follow the exit selection workflow below.
On installations without runtime routing, use the manual source workflow from A.

### Inspect subscription changes

Normal node rotation is imported automatically on this host.
To inspect the selected source's candidate against its accepted baseline, run:

```sh
clash-diff
```

The command reads local snapshots through sudo and does not download, write review or approval records, or change network mode in A, B or C.
It shows node addresses, ports, TLS names, groups and rules, with authentication values and nested transport values hidden.
Use `clash-diff --json` for structured output.
This is an optional diagnostic command, not an update prerequisite.

`proxy-status` shows `Profile refresh` with its result and reason, audit categories and counts, and `Last auto approval` with categories, unique node count and time.
`fallback` means the rejected candidate was not imported and an earlier snapshot remains active.
`verifying` means import is waiting for health acceptance.
A structural block requires correcting the source content or the relevant local policy, rather than approving routine rotation.
The five-minute desktop notification timer reports only download failures, refused imports, rollbacks and health failures through Noctalia.
Successful updates and nonblocking audit findings remain in `proxy-status` without popups.
Notifications use fixed categories without node names, subscription URLs or credentials.

### Portal login

For portal login, deliberately enter A and run `firefox-portal http://192.168.1.1/` with the actual literal portal address.
It has fresh data, no extensions, a single-IP port-80/443 relay and a 15-minute lifetime.
Redirects to another address fail.
Explicitly start another session for that address after reviewing it.
Closing the session removes its data.
Daily Firefox can stay in A.
Resume B before launching identity Firefox.
Keep an offline bootable generation and registration-key backup.
This command does not create a recovery generation or alter DNS fallback policy.

## Switching modes

Use the Nushell commands `clash-on` and `clash-off`.
They record privileged intent, manage the GUI/helper lifecycle and wait for coherent state.
With runtime routing, `clash-on` allows up to 90 seconds per core for DNS and TUN warmup and fresh readiness proof.
If a restored controlled snapshot repeatedly fails data-plane checks, `clash-on` can try one compatible cached update for the same registration in C.
This host enables `my.proxy.startupRecoveryFetch`, which allows one scoped download in C when the cache is unavailable. The option defaults to disabled.
The recovery attempt shares a 210-second readiness deadline measured from the first core startup. Routine updates still wait for 90 seconds of healthy B.
Closing the GUI leaves proxy intent and the managed core active. Use `clash-off` to stop managed proxy access.

On this host, `clash-rules` and `clash-proxy-only` select a routing submode and request B in one command.
They return a terminal success message naming the mode, or a nonzero result with the failed stage and available reason.
Restarting from C, starting with the retained `proxy-only` selection, switching routing explicitly, or recovering a registered source allows up to 90 seconds to confirm readiness after core startup.
They do not launch the GUI, send desktop notifications or run `proxy-status`.
In B and C, `proxy-status` shows requested and last applied routing, the transaction epoch, and the observed core mode, TCP leaf and DNS, UDP and ICMP coverage.
The underlying commands are `sudo proxy-control routing rules` and `sudo proxy-control routing proxy-only`.
`clash-off` retains the choice for this boot; `clash-on` reuses it, and reboot defaults to rules.
Proxy-only requires an accepted pinned source. If preparation or full health verification fails, C protects applications while the requested selection remains available for retry.

Use `clash-egress` to list accepted-source exit IDs and `clash-egress APPROVED_NODE_ID` to select one and request strict B.
Replace `APPROVED_NODE_ID` with a complete listed ID. The root equivalents are `sudo proxy-control egress list` and `sudo proxy-control egress APPROVED_NODE_ID`.
Exit changes close old streams and associations through C. GUI node selection remains available in rules; strict mode requires this root transaction.
The selected exit survives off/on for this boot. Source changes to that node require accepting the update in rules and selecting its new ID.
See [routing policy](proxy.md#routing-policy) for the policy boundary.

`sudo proxy-control on` and `sudo proxy-control off` are the underlying system controls.
`on` opens the single-use configuration-import window and starts the helper.
The controlled-source host starts the core from its registered candidate.
Compatibility mode still lets the GUI supply/start it.
`off` clears authorization and stops the helper.
The desktop commands also handle GUI processes.
Starting only `clash-verge` in A does not authorize a core.

| Command | Purpose |
|---|---|
| `sudo proxy-control preflight` | Check controlled-source configuration and assets without changing intent |
| `proxy-policy` | Show network permissions and policy |
| `proxy-control status auto --check` | Check readiness for the requested mode |
| `proxy-control status proxy` | Show proxy status |
| `proxy-control wait proxy` | Wait up to 30 seconds for proxy readiness |
| `proxy-control status direct` | Show direct-access status |
| `firefox --check` | Check browser prerequisites without opening a window |

`status auto` follows the requested mode.
`--check` returns nonzero when the system is not ready, and `wait` reports progress every ten seconds.

| Tool | Output |
|---|---|
| `proxy-status` | Mode, DNS, readiness, failed probe layer, refresh result and audit counts |
| `clash-diff` | Candidate changes against the selected source's accepted baseline |
| `proxy-status --plain` | Plain text suitable for logs |
| `proxy-status --json` | Structured status |
| `proxy-inspect` | Passive service, link, queue and resource diagnostics |
| `proxy-inspect --posture` | Listener bindings, declared ingress and local trust settings |
| `egress-audit` | Additional active network probes |

Use `--no-color` or `NO_COLOR` to disable color in the status panel.

`proxy-policy` shows native UID/interface/CIDR grants together with core DIRECT ranges, remote target grants and subscription trust.
These are independent permission planes.
A restricted native policy combined with broad core DIRECT ranges still allows clients reaching that local proxy inlet to use those core ranges.
For host application LAN restriction, configure both `directEgress.mode="restricted"` and `localTargetCidrs=[]`, then declare the required grants.
`proxy-inspect` reports command-failure categories and exit codes without exposing arbitrary service stderr.

`proxy-inspect --posture` shows IPv4 and IPv6 listeners in the caller's network namespace.
It reports binding and identity separately from the declared ingress rules for LAN, tailnet and routed guests.
The ingress columns describe policy, while live reachability also depends on network state, peer authorization and service configuration.
A proxy-port hint is a prompt for review. The command does not connect to listeners.
Use `--json` to include structured results and inspect available Unix socket metadata for the declared control, browser and container endpoints.

For startup trust overrides, use `proxy-inspect --posture --pid PID` or `sudo proxy-inspect --posture --privileged` for the selected system units.
The report includes only tracked variable names and comparison status. It also shows the system CA bundle digest, SSH state and a summary of available Tailscale settings.
Unavailable fields remain unknown. Run the command in the desktop session to include its stored proxy mode when available.
The desktop configuration asserts that OpenSSH stays disabled and that its declared host ingress omits TCP/22.

## Remote desktop

Sunshine is enabled and starts with `graphical-session.target`, exposing the current session as **Desktop**.
After deployment, log into the desktop and open `https://localhost:47990` on the computer to set credentials and approve Moonlight's pairing PIN.
Both the phone and computer must be logged into the same permitted tailnet.
Add the computer's Tailscale IP manually in Moonlight.
Discovery is not required.
The same overlay address works on LAN and over the internet, subject to the tailnet's access policy.
Direct access by physical LAN IP is not enabled by the default empty `trustedLanCidrs`.

The web UI accepts only local clients.
It is not a default tailnet firewall allowance.
UPnP is disabled.
Streaming uses the explicit tailnet TCP/UDP port lists in [options.nix](../system/config/network/options.nix).
Configure phone-to-computer grants in the Tailscale admin console separately.
That policy is not stored in this repository.
A and healthy B allow remote desktop.
C cuts the session, including existing UDP flows and decrypted tailnet input.
Use `clash-off` locally when direct A is intended.

[sunshine.nix](../system/programs/sunshine.nix) selects the CUDA build variant.
The configured `https://cache.nixos-cuda.org` substituter supplies binaries matching the derivation selected by the lock and package options.
Check cache availability with `nix build --max-jobs 0 --no-link .#nixosConfigurations.lfour.config.services.sunshine.package`.
This command disables local compilation.
Credentials and pairing state reside in the user's Sunshine directory.
The Desktop entry and service settings are declarative.
Check hardware capture/encoding and phone streaming in the deployed graphical session.

```sh
systemctl --user status sunshine.service
journalctl --user -b -u sunshine.service
sudo tailscale netcheck
sudo tailscale ping <phone-tailnet-name-or-IP>
```

For the optional restricted B policy, Unbound DoT switch and time destination pins, see [Direct egress](egress.md).
Restricted B retains negotiated Sunshine media.
C always cuts remote control.

Tailscale marking failures leave B blocking unmarked root UDP.
Check service permissions and socket marks.
Use `clash-off` locally if direct access is needed.
Upgrade checks are listed in [maintenance](maintenance.md#validation).
Dedicated guest accounts retain their separate boundary.

## State and diagnosis

| File | Meaning |
|---|---|
| `/run/proxy-mode/state.json` | Atomic mode, reason, policy version, revision and update time |
| `/run/proxy-mode/routing.json`, `routing-active` | Boot-bound requested/applied submode, epoch, immutable policy digest and phase; selected policy/health/audit artifacts |
| `/run/proxy-mode/routing-firewall.json` | Root-only expected outer firewall model and selected transport script digest |
| `/run/proxy-mode/health.json` | Probe scope, full verdict, separate HTTP/route/DNS/TUN diagnostics, proof sources and per-layer durations |
| `/run/proxy-mode/heartbeat.json` | Last successful supervisor round |
| `/run/netsec/state.json` | Verification verdict, checked generation and freshness |
| `/run/dns-upstream/state.json` | DNS mode/reason, encryption policy, session, budget, deadline and override |
| `/run/dns-policy/state.json` | Broker lease state and applied generation |
| `/run/gost-relay/state.json` | Relay observation and applied generation |
| `/run/netsec/alerts/*.json` | Per-owner alarms. `/run/netsec/failed` lists owners |
| `/run/proxy-mode/intent`, `core-seen` | Boot-scoped operator intent and crash-detection marker |
| `/run/proxy-mode/authorized-config`, `config-digest`, `authorize-window` | Root-only snapshot authority and single-use import grant |
| `/run/proxy-mode/profile-refresh.json` | Last refresh result, digests, fixed reason, audit counts, last automatic approval and 20-entry history. Mode 0644 |
| `/run/proxy-mode/profile-refresh-pin.json` | Rejected candidate digest, health-failure marker and one-hour TTL. Mode 0644 |
| `/run/proxy-mode/source-fetch.json` | Boot-bound download outcome, fixed failure category and optional HTTP status. Mode 0644 |
| `/run/proxy-mode/profile-refresh-verifying.json` | Root-only pending transaction with the previous snapshot path |
| `/run/proxy-mode/profile-refresh-settled.json`, `profile-refresh-commit.json`, `profile-refresh-restart.json` | Root-only settled authority, interrupted commit recovery and pending core recovery |
| `/run/proxy-mode/effective-config.json`, `boot-id` | Normalized effective YAML digest bound to the shared boot identity. `effective-config-sha256` is a compatibility mirror |
| `/run/proxy-mode/health-observations.json` | Last 20 full probe outcomes in this boot, with sanitized stages, layer timings and snapshot digests |
| `/run/proxy-mode/health-failure.json` | Last failed probe with fixed reason, optional sanitized `failure_stage`, layer timings, boot identity and snapshot digest. Preserved after recovery |

Atomic JSON is the consumer interface.
Separate `status`, `reason`, `version`, `result` and `verified-version` files are compatibility mirrors.
Reason-only updates change `revision`.
Kernel-policy commits change `policy_version`, also exposed as `version`.
Results spanning a newer generation are discarded.

Readiness requires current-generation mode, DNS, broker and relay observations, no active alarm and fresh verification.
Verifier results `ok` and `degraded` both confirm that the hard checks passed.
`degraded` retains an advisory and appears as `Passed · warnings`.
Failed or unknown results block readiness.
Broker state must be `applied`. B requires DNS status `proxy`. With strict DNS, A accepts `dot` or `dot-priming`.
Consumer freshness defaults to 120 seconds.
Verifier freshness defaults to 420 seconds, covering its five-minute timer and bounded execution.
Boot identity and monotonic timestamps reject cross-boot, future and stale records.

| Reason | Where to look |
|---|---|
| `request`, `reload` | Transition awaiting core health and verification |
| `unauthorized-core`, `core-gone` | Operator intent, launcher/helper and core cgroup |
| `no-tun`, `tun` | Device, managed routing table/rules, real route selection and fake-IP HTTP path |
| `config` | Snapshot digest, managed API values and TCP/UDP listener identity |
| `route` | Actual logged leaf is DIRECT/REJECT or inconsistent with the selected graph |
| `obs`, `obs:1/3`, `obs:2/3`, `obs:3/3` | HTTP completion, exact source-port log and selection observations |
| `resolver-error`, `split-error` | DNS listener identity or approved Wi-Fi profile/lease |
| DNS `proxy-priming`, `degraded-proxy` | B is confirming the selected core resolver or its probes failed. Check the core DNS listener and selected DoH path |
| DNS `degraded-dot-only` | Check A's authenticated DoT upstreams and DNSSEC probe result |

```sh
journalctl -b -u proxy-mode -u dns-upstream -u gost-relay -u clash-verge
cat /run/proxy-mode/health.json
cat /run/dns-upstream/state.json
cat /var/lib/dns-upstream/history.log
```

Some service logs and authority files require sudo.
Do not publish snapshots or subscription-bearing configuration as diagnostic output.

### Drop counters

Run `sudo proxy-inspect --drops` for a focused JSON view of existing refusal counters in `inet lf_filter`.
Ordinary `proxy-inspect` includes the same view under `drops`.
The command reads local state and nftables without changing rules, resetting counters or sending probes.

```sh
sudo proxy-inspect --drops | jq '{available,error,policy,state_consistent,groups:[.groups[] | select((.packets // 0) > 0)]}'
```

Each group identifies a stable `reason`, a `role`, its `chain`, and counted packets/bytes.
Routed guest roles use the configured interface names.
The policy record supplies the observed mode, transition reason and generation.
`state_consistent=false` means the state was unavailable or changed during collection.
This view does not establish readiness.

| Reason | Counted refusal |
|---|---|
| `mode-c-output`, `mode-c-input` | C output or reply/input guards |
| `guest-source-policy` | Guest source outside its declared scope, including an undeclared address family |
| `guest-target-policy` | Routed guest protected or unsupported target |
| `dns-client-policy` | Application access to an internal resolver inlet |
| `dns-listener-identity`, `proxy-listener-identity` | Replies from an unauthorized local socket UID |
| `proxy-inlet-access` | Attempts to connect directly to protected local proxy ports |

Counts cover only drops with counters in the listed chains.
`uncounted_rules` reports selected drops without counters.
`installed=false` and null counts mean the chain is absent.
Zero counts do not prove that an inlet is enabled or healthy.
Reading/parsing failure yields `available=false`, a fixed error category and no groups.
`--drops` exits 1.
Without the required nftables privileges, the error is `permission-denied`.

Counters start at each rule's latest installation or flush.
Reload, repair and mode changes can reset some of them.
Compare snapshots with that lifecycle and the policy generation in mind.
Historical totals do not attribute a particular connection or its original/NAT destination to the current mode.
Output has a fixed group count for the configured roles and contains no packet log, target addresses or raw rules.
Packet volume changes numeric totals only.

### Refresh status

`proxy-status` reports the update schedule in `Auto update`, the latest refresh in `Profile refresh` and `Update result`, and GUI-copy information in `Profile vs runtime`.
Controlled sources treat the GUI copy as display-only. Compatibility imports show the GUI-to-snapshot digest comparison.
`proxy-policy` includes `profile_refresh`.
`proxy-inspect` exposes up to 20 sanitized refresh history entries.

`Subscription download` reports fetching and download errors separately from import.
`deferred` means network protection postponed the attempt; it is not a failed download and does not trigger a popup.
`Content audit` reports automatic content approval without claiming health acceptance.
`restored` means startup reused an accepted snapshot under the current policy.
`verifying` means the imported snapshot is awaiting its health result.
A failed latest update is shown separately from subscription age, preserving the previous acceptance time.
Controlled sources use root records for these decisions; GUI update timestamps do not report root success.
`fallback` means the candidate was rejected and the prior authorized snapshot was retained.
`rolled_back` means snapshot authority was restored and a core replacement was requested.
Check the network readiness fields to confirm that the core recovered.
A digest difference describes different content.
It does not establish which file is newer.

Subscription changes use the same root policy.
New hosts overrides need matching `subscriptionAuditAllowedHosts` entries.
Switching a subscription does not expand that permission.
Probe targets and expected routes are configurable.
If protected routes remain indeterminate, `enforce` retains the prior snapshot.
Use the audit categories to identify the constraint before changing policy.

The user notification timer reads local files every five minutes.
It combines update and health failures into one message per round.
Refresh failures are deduplicated by full result and digest, download failures by attempt time, and health failures by state transition.
Successful updates, health recovery, audit warnings, age and GUI-copy differences do not trigger popups.
Deduplication state is stored privately in `$XDG_STATE_HOME/clash-verge/refresh-notified.json` and advances only after delivery succeeds.
The timer does not fetch subscriptions.

### Subscription reachability

Run `proxy-subscription --check --json` to check the current subscription explicitly.
The command reads the URL from the GUI index and reports HTTP status, transport, elapsed time, bytes, HTTPS and redirect count.
Supply `--etag` to send a conditional GET.
Without a validator it sends an ordinary GET.

The check first uses the direct path permitted by the current network policy and tries the configured mixed port after a transport failure.
A complete HTTP response such as 403 is reported without a proxy retry.
The command does not write profiles, import configuration or advance update timestamps.

The total deadline defaults to 15 seconds and covers name resolution, both transport attempts, redirects and body reads.
`--timeout` accepts 1–120 seconds.
Responses are limited to 8 MiB and three same-origin redirects.
Cross-origin redirects and HTTPS downgrades are refused.
Output excludes URLs, response bodies and validator contents.
`proxy-inspect --deep` adds service restart counts and a fixed journal query for privileged troubleshooting.

## Coordination and firewall repair

Socket-directory events, TUN link monitoring, nft events, NetworkManager dispatch and dhcpcd hooks wake the supervisors.
Adaptive polling backs them up: two seconds during transitions, capped at five seconds in enforced modes and 60 seconds in direct mode.
The mode supervisor has a 90-second watchdog.
A cancelled probe does not count as a failed observation.

Build-time [expected rules](../system/config/network/proxy/coordinator.nix) are generated with the same nft implementation inside an LKL userspace kernel.
The verifier compares owned tables, hooks, priorities, policies and full ordered expressions.
Handles, counters, set-element order and tightly bounded authorized DNS leases are normalized.
Read/parse errors are failures, never empty successes.
A correctly installed C passes even with no core.

`nftables-verify` publishes failure/alarms but exits zero so repair/rebuild jobs can proceed.
Check the JSON verdict, not just unit exit status.
Each component clears only its own alarm.
Queued verification requests survive a busy round.

```sh
sudo systemctl restart nftables.service
sudo systemctl start nftables-verify.service
```

Restart/reload reinstalls the owned static policy plus the safety mode atomically.
Proxy intent goes through C before health reopens B.
The independent guard keeps proxy-intended traffic blocked when the main table disappears. Before a live rollback to a generation that predates `inet lf_policy_guard`, enter explicit A with `clash-off` to clear its per-packet requirements.
Stopping nftables leaves its policy installed.
`nftables-verify-repair.service` performs explicit repair and recheck.
`nftables-recover` retries a failed nftables unit.
Avoid flushing the global ruleset.

## Firewall table migration

`proxy-control migrate-firewall` explicitly removes `inet filter`, `ip proxymode_nat` and `ip6 proxymode_nat` while installing the [reserved tables](network.md#firewall-lifecycle).
Ordinary startup, reload and repair leave these three names untouched.
Run migration only after confirming that the tables belong to this configuration.

Inspect each migration target and confirm its owner:

```sh
sudo nft list table inet filter
sudo nft list table ip proxymode_nat
sudo nft list table ip6 proxymode_nat
```

With ownership confirmed, run the migration and verify the installed policy:

```sh
sudo proxy-control migrate-firewall
sudo systemctl start nftables-verify.service
```

The root command holds the operator and policy locks and commits the three deletions, static rules and safety mode in one nft transaction.
A rejected transaction preserves the installed rules and migration targets.
Missing targets are accepted, so repeated execution is supported while those names remain assigned to this configuration.

Proxy intent or a running core installs C first.
The supervisor opens B after health checks pass.
Direct intent with no core installs A.
Migration preserves intent, authorization and the running core.
Use the diagnostics above to check mode and verification JSON.
Cleanup occurs only during this command.
Tables owned by other services stay under their owners' control.

## Core namespace recovery

The host `Mihomo` interface is the managed veth.
Inspect the actual core TUN and auto-redirect table with `sudo ip -n lf-clash-core link` and `sudo ip netns exec lf-clash-core nft list table inet mihomo`.
Use `proxy-control status proxy --check` for the complete readiness contract.
A host interface alone does not establish readiness.

`clash-on` reconstructs the private namespace after stopping the old helper and preserves the configured source authorization workflow.
Existing core connections are closed.
Physical uplink changes and resume restart the already authorized core automatically.
If a LAN or VPN overlaps the internal link, configure a free private /30 and /64 via `my.proxy.coreLink`, changing each subnet and its host/core endpoints together.
Never remove the overlap check to restore access.
The internal MACs are distinct local unicast addresses and are independent of physical MAC randomization.
Keep a previous system generation before activation.

To return to a generation that uses a host-owned TUN:

1. Run `sudo proxy-control off` and wait for A.
2. Stop `clash-core-network.service`. Its cleanup removes its owned namespace, veth and route priorities. It refuses to delete foreign interfaces or a legacy TUN, even with a stale ownership marker.
3. Activate the chosen generation and start it through the normal `clash-on` workflow.

## Configuration

| Source | Settings |
|---|---|
| [machine.nix](../system/config/machine.nix) | User, interfaces/match keys, UIDs, ports, mark, address ranges and upstream endpoints |
| [options.nix](../system/config/network/proxy/options.nix) | TUN, route table 2022, fake-IP pool, probe URLs, boot intent, proxyOnly, dnsEncryption, shared domain filtering, localTargetCidrs, remoteTargetGrants, nat64Prefixes, guests, split DNS and freshness |
| [profile.nix](../system/config/network/proxy/profile.nix) | Root-owned managed DNS/TUN/profile contract |
| [routing.nix](../system/config/network/proxy/routing.nix) | `runtimeRouting`, immutable rules/proxy-only policies and `proxyOnlyDnsUpstreams` |
| [options.nix](../system/config/network/options.nix) | Host/guest private access, per-interface guest trust/sources, dedicated socket guest accounts, host-service ports, hotspot, tailnet and Wi-Fi MAC policy |
| [cvr-merge.nix](../home/config/clash/cvr-merge.nix) | Matching Clash GUI Merge template and routing enhancement |
| [firefox.nix](../home/programs/firefox/firefox.nix), [options.nix](../home/programs/firefox/options.nix) | Native browser, networkPath, nvidiaAcceleration, privacy, policy, fonts and outer sandbox |

TUN defaults: `Mihomo`, table 2022, rule priority 9000, internal marks 8227/8228, fallback priority 32768, file descriptor 0.
Parser, template and health share these values.
`tunMode=false` selects HTTP-only/redir-host operation, dropping the requirement for a TUN.
Fake-IP network is derived from `fakeIpRange`.
Avoid independent edits to consumers.

`runtimeRouting` defaults to false in the module and is enabled in this host configuration.
It requires controlled sources, local DNSSEC, strict encrypted DNS, an isolated TUN, kernel authorization leases and `proxyOnly=false` as the rules boot default.
The older `proxyOnly` option remains a build-time policy for configurations without runtime switching.
`proxyOnlyDnsUpstreams` accepts authenticated literal-address HTTPS resolvers and always uses the remote group.
Accepted source snapshots are kept independently under `approved/rules` and `approved/proxy-only`, including the original Rule data and pinned nodes needed for local re-rendering.
When probe settings change, runtime routing re-renders from digest-verified original subscription rules and audits the result under the current policy.
If a legacy snapshot's original rules cannot be recovered safely, refresh the registered source and import the new download.

For core access to a local/internal service, [target authorization](proxy.md#domain-targets) separates local DIRECT CIDRs from remote node grants.
An internal suffix only permits a locally resolved private answer.
It does not authorize node-side resolution or dialing.
Remote grants use the actual leaf name, core inlet, transport and destination ports.
Gost CONNECT arrives as `HTTPS`.
Rebuild and deliberately import/restart the managed core after changing these options.
A retains literal portal access.
C remains blocked.
Native [direct-egress grants](egress.md) are independent.

For separate guest trust classes, select a development bridge explicitly and grant only the needed private service to other guests.
A host-socket backend additionally needs its own fixed account.

Choose a free UID and nonoverlapping subordinate ranges:

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

The example addresses are placeholders.
Built-in bridge source/gateway defaults come from `my.machine`.
Extra `guestInterfaces` require their own canonical, disjoint `guestNetworks` source CIDRs.
Add `hostTcpPorts` / `hostUdpPorts` on a role only when a service should be reachable at its declared gateways.
IPv6 needs explicit ULA sources/gateways.
Public guest IPv6 stays refused.
A hotspot remains restricted unless its own trust is explicitly changed.

Install/reconcile the complete nft policy before starting pasta or rootless Docker under `guestnet`.
The Docker user unit clears inherited explicit proxy variables for restricted accounts and uses the declared rootless package/settings.
Stop existing backends before adding/changing identities or delegated UID mappings.
To revoke private access for running backends, keep the identity declared, remove its grants and reload the firewall.

Both tracked directions are cut.
Changing `socketGuestUsers` alone does not migrate desktop containers.
These accounts use the separate real-address DNS path, which has no plaintext/split-DNS fallback.
See [guest boundaries](network.md#guests-and-containers).

Wi-Fi profiles explicitly setting a cloned MAC override `my.hardening.wifi.clonedMacAddress`.
Otherwise the default `stable-ssid` retains the same MAC for the same SSID.
`permanent` selects the hardware address, while `preserve` keeps the current address.
For coordinated changes, declare `my.hardening.wifi.identities`, inspect `network-identity plan UUID`, then disable autoconnect and disconnect before `apply` or `restore`.
See [Link identity](identity.md) for the complete procedure, wired policies and DHCP state preparation.

## VirtualBox

VirtualBox uses its native driver and graphical interface alongside the existing QEMU/KVM and libvirt tools. Sign in again after activating the configuration to receive `vboxusers` membership in your desktop session.

In the VM network settings, select **Host-only Adapter** and choose `vboxnet0`. Set the guest to use DHCP. If the VirtualBox Network Manager has its built-in DHCP server enabled for `vboxnet0`, disable it so the system DHCP service can provide the lease.

Guests use `192.168.56.1` as their gateway and managed DNS server. Public IPv4 goes direct in A and through TPROXY in healthy B. Mode C blocks public traffic. The restricted policy for `guestNetworks.vboxnet0` governs private targets and host services. Attaching another adapter changes the VM's network paths.

```sh
systemctl status vboxnet0.service vboxnet-dnsmasq.service
VBoxManage list hostonlyifs
```

## Hermes containers

[`system/programs/hermes/`](../system/programs/hermes) groups the agent configuration, skills, mail CLI and container backend.
The backend runs `hermes-docker.service` in the dedicated account's user manager.
`hermes-containers.socket` and `.service` expose a fixed Unix stream to Hermes.
The account's private runtime and data directory retain their own permissions.

| Resource | Default location |
|---|---|
| Hermes agent data and workspace | `/var/lib/hermes` |
| Restricted backend home / Docker data | `/var/lib/hermes-containers` / `/var/lib/hermes-containers/docker` |
| Hermes Docker API | `unix:///run/hermes-containers/docker.sock` |

Gateway, dashboard and Hermes login shells set `DOCKER_HOST` to that API.
Its socket is mode 0660 and restricted to the backend account and `hermes-container-clients` members.
The desktop keeps its own Docker endpoint.
Local terminal/MCP/browser tools running directly as Hermes keep the host proxy policy.

The backend clears inherited proxy variables and uses the filtered real-address DNS path.
A permits public IPv4 directly, B follows host public routing through the managed core, and C cuts application traffic.
Private/metadata/fake-IP targets and host proxy inlets have no default grant.
The default Docker bridge has no external IPv6 path.
Other supported native backend paths retain the UID guard's dual-stack checks.

```sh
sudo systemctl status hermes-containers.socket hermes-containers.service
sudo systemctl --user --machine=hermes-containers@.host status hermes-docker.service
sudo -u hermes env DOCKER_HOST=unix:///run/hermes-containers/docker.sock docker info --format '{{.DockerRootDir}}'
```

Before starting the new account's user manager, the retirement unit stops Hermes' old user Docker and refuses startup if an old dockerd remains.
The global Docker user unit excludes Hermes and the dedicated backend.
The latter uses its own unit.
The new backend keeps the reserved-port bind restrictions inherited from `user.slice`.
The agent data in `/var/lib/hermes` and the old account's Docker images and volumes stay in place.
Images and volumes are not imported automatically.

To migrate the required container data:

1. Stop application writes and export images and volumes using the old account's UID mapping before activation.
2. Load images and restore volumes through the new Docker endpoint.
3. Check the restored data before removing the old data or UID mapping.

A recursive ownership change is not a substitute for this migration.
Before returning to an older backend configuration, stop the dedicated daemon and both API units.

## Hermes time MCP

The `time` MCP keeps its stdio interface and uses the configured NixOS timezone.
The stdio bridge clears its environment and connects to `/run/hermes-time/mcp.sock`, accessible to root and the `hermes-time-clients` group.
Each connection starts a `hermes-time@.service` instance in `untrusted.slice` under the dedicated `hermes-time` account.
The server has a read-only Nix store, temporary `/work`, and a private network namespace limited to Unix sockets.

The gateway and dashboard clear inherited proxy and CA override keys at systemd startup and use the declared system CA bundle.
Compatibility mode restores the approved local Gost proxy for the gateway. Networked MCP and browser tools use their declared per-tool options.
`proxy-inspect --posture` reports the startup environment. Hermes loads its profile `.env` in-process, where overrides can take effect after startup.

```sh
systemctl status hermes-time.socket
systemctl list-units 'hermes-time@*.service'
```

## Offline recovery

If direct access is intended, use `clash-off`.
For portal/DNS recovery in A under the default `dnsEncryption="opportunistic"`, inspect DNS state/history, then explicitly enable the bounded-renewal override if needed:

```sh
sudo touch /run/dns-upstream/force-plaintext
# Remove after recovery; reboot also clears it.
sudo rm /run/dns-upstream/force-plaintext
```

This permits plaintext DNS and continues billing the session budget.
It cannot override C's egress guard.
See [DNS](dns.md) for the exact scope.

With `my.proxy.dnsEncryption="strict"`, the override is ignored and no plaintext fallback is granted.
`clash-off` still selects A with encrypted DNS.
Try a literal HTTP portal address, another uplink, or repair the authenticated DoT configuration.
A portal requiring unavailable public DNS needs an intentional policy change, for example an already built `opportunistic` generation selected offline.
Prepare such a generation before relying on strict mode while travelling.
Private split DNS and MagicDNS remain available under their normal approvals and mode constraints.

To recover a broken system generation:

1. Select an older entry in the systemd-boot menu. The menu retains eight entries.
2. If the required generation is absent from the menu, inspect the retained profiles in `/nix/var/nix/profiles/system-*-link`. As root, select its store path and install its boot entry:

   ```sh
   nix-env -p /nix/var/nix/profiles/system --set /nix/store/<nixos-system-path>
   /nix/var/nix/profiles/system/bin/switch-to-configuration boot
   ```

3. If no installed system boots, start a NixOS ISO, unlock and mount root and the ESP, then enter the installation with `nixos-enter --root /mnt`.

## Configuration checks

[Patch maintenance](maintenance.md#validation) lists checks for configuration and dependency changes.
