# Network operations

English | [简体中文](CN/operations_CN.md) | [Documentation](README.md)

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

### Register a source

Run `sops edit system/secrets/proxy.yaml` and add an object to `sources` for each subscription.
IDs must be unique, and the catalog supports 1–256 entries.
Rebuild and activate the configuration to update `/run/secrets/proxy_source`, then review and register the source by ID:

1. Review the source and note the returned digest:

   ```sh
   sudo clash-source review /run/secrets/proxy_source --id primary
   ```

2. Replace `REVIEWED_DIGEST` and register the source:

   ```sh
   sudo clash-source register /run/secrets/proxy_source --id primary --digest REVIEWED_DIGEST --previous none
   ```

3. Fetch the registered configuration:

   ```sh
   sudo systemctl start clash-source-refresh.service
   ```

4. Request proxy mode:

   ```sh
   clash-on
   ```

`review FILE` without `--id` lists every entry’s digest and previous registered revision, without endpoints or credentials.
A catalog containing multiple entries requires `--id` for registration.
Approving one entry grants nothing to the others.
The canonical revision covers that entry, so adding or editing another source does not change it.

`review` returns the previous revision of that ID.
Use it instead of `none` when updating an existing registration.
A mismatched revision or candidate digest refuses registration.
Updating one source invalidates only its download pointer and semantic approval, retaining its settled audit baseline.
The first registration becomes selected.
Later registrations remain available without changing the selection.
Existing single-source state migrates on the next broker command.

### Add and switch sources

For a newly added `secondary`, review its own digest and register it once.

These commands assume the updated catalog has already been deployed:

Review and register it:

```sh
sudo clash-source review /run/secrets/proxy_source --id secondary
sudo clash-source register /run/secrets/proxy_source --id secondary --digest REVIEWED_DIGEST --previous none
```

Then inspect the registered sources and select one:

```sh
sudo clash-source list
sudo clash-source use secondary
```

Later switches use only `sudo clash-source use primary` or `sudo clash-source use secondary`.
No re-registration is needed.
Deleting an entry from the encrypted catalog does not revoke an existing registration.
Use the explicit `revoke` command below.

Use `list` to inspect the requested source, registered revisions and settled digests.
`use` fetches a source before selecting it and scheduling refresh.
`--revision REVIEWED_DIGEST` refuses a registration that has changed since review.
Fetch failure leaves the previous selection in place.

`proxy-status` shows the running source as `Active source`.
It can differ from the requested source while the candidate is being checked or an earlier snapshot is running as fallback.
Each source keeps its own accepted baseline across switches.

To remove an unused source and its state, run `sudo clash-source revoke secondary`.
Select another source before revoking the current one.
To stop a running fallback immediately, use `clash-off`.

The `clash-source-select.path` unit watches the GUI's `profiles.yaml`.
Switching to a mapped UID requests the corresponding registered source.
Unknown profiles leave the selection unchanged.
Changing a GUI URL, YAML or script grants no additional authority.
Inspect `journalctl -u clash-source-select -u clash-source-refresh` when the GUI selection and active source differ.
In A, selection and fetching do not request B.
Use `clash-on` deliberately.

`clash-source refresh` only prepares a candidate.
The system service checks it before deciding whether to replace the managed core while proxy intent is active.
An explicit `clash-on` uses the accepted snapshot when available and records one pending update request.
The coordinator starts that background request after B passes its health and enforcement checks and remains healthy for 90 seconds under the same boot, snapshot and policy.
A transition out of healthy B restarts this wait.
Automatic jobs triggered during C record `deferred`, preserve the request for B and make no download attempt.
This does not relax C or give the download account a public-egress exception.
Its root timer starts about two minutes after boot and then runs every 30 minutes, independently of the GUI.
Each background job retries a failed download up to three times with 15 seconds between attempts.
The 180-second service timeout caps the job; it does not delay foreground startup.
Refresh defers while an import or rollback is being settled.
The GUI's `update_interval` controls only its display copy: startup raises existing intervals shorter than 1440 minutes to 1440 and preserves longer intervals and disabled schedules.

If the current snapshot remains authorized and intact, with fresh full health, readiness and any required B lease,
a rejected or unchanged candidate leaves the running core and existing connections in place.
Invalid current authority or failed health follows C recovery instead.
Activation enters C before replacing the core through helper IPC, repeats import checks and requires proxy health before B returns.

Worker failures identify `fetch`, `parse`, `render` or `pin-addresses`.
`Subscription download` in `proxy-status` adds a fixed DNS, TLS, connection or HTTP failure category and an HTTP status when available.
A completed download does not establish runtime acceptance. Content audit and health results appear separately.
Unresolvable nodes may be dropped up to `max(1, node_count // 5)`.
Private answers still refuse the candidate.
Fetch/parse/audit failure preserves a valid runtime fallback.
No fallback leaves C.
If C prevents source recovery, deliberately select A, fetch the registered source, then request B.
DNS does not downgrade automatically.

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
Closing the GUI alone leaves proxy intent active and therefore enters C when the core stops.

`sudo proxy-control on` and `sudo proxy-control off` are the underlying system controls.
`on` opens the single-use configuration-import window and starts the helper.
The controlled-source host starts the core from its registered candidate.
Compatibility mode still lets the GUI supply/start it.
`off` clears authorization and stops the helper.
The desktop commands also handle GUI processes.
Starting only `clash-verge` in A does not authorize a core.

| Command | Purpose |
|---|---|
| `proxy-control preflight` | Check configuration and assets without changing intent |
| `proxy-policy` | Show network permissions and policy |
| `proxy-control status auto --check` | Check readiness for the requested mode |
| `proxy-control status proxy` | Show proxy status |
| `proxy-control wait proxy` | Wait for proxy readiness |
| `proxy-control status direct` | Show direct-access status |
| `firefox --check` | Check browser prerequisites without opening a window |

`status auto` follows the requested mode.
`--check` returns nonzero when the system is not ready, and `wait` reports progress every ten seconds.

| Tool | Output |
|---|---|
| `proxy-status` | Mode, DNS, readiness, refresh reason, audit counts and last automatic approval |
| `clash-diff` | Candidate changes against the selected source's accepted baseline |
| `proxy-status --plain` | Plain text suitable for logs |
| `proxy-status --json` | Structured status |
| `proxy-inspect` | Passive service, link, queue and resource diagnostics |
| `egress-audit` | Additional active network probes |

Use `--no-color` or `NO_COLOR` to disable color in the status panel.

`proxy-policy` shows native UID/interface/CIDR grants together with core DIRECT ranges, remote target grants and subscription trust.
These are independent permission planes.
A restricted native policy combined with broad core DIRECT ranges still allows clients reaching that local proxy inlet to use those core ranges.
For host application LAN restriction, configure both `directEgress.mode="restricted"` and `localTargetCidrs=[]`, then declare the required grants.
`proxy-inspect` reports command-failure categories and exit codes without exposing arbitrary service stderr.

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
Broker state must be `applied`.
`suspended`/`revoked` and DNS `degraded-dot-only` are not ready.
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
| [options.nix](../system/config/network/options.nix) | Host/guest private access, per-interface guest trust/sources, dedicated socket guest accounts, host-service ports, hotspot, tailnet and Wi-Fi MAC policy |
| [cvr-merge.nix](../home/config/clash/cvr-merge.nix) | Matching Clash GUI Merge template and routing enhancement |
| [firefox.nix](../home/programs/firefox/firefox.nix), [options.nix](../home/programs/firefox/options.nix) | Native browser, networkPath, nvidiaAcceleration, privacy, policy, fonts and outer sandbox |

TUN defaults: `Mihomo`, table 2022, rule priority 9000, internal marks 8227/8228, fallback priority 32768, file descriptor 0.
Parser, template and health share these values.
`tunMode=false` selects HTTP-only/redir-host operation, dropping the requirement for a TUN.
Fake-IP network is derived from `fakeIpRange`.
Avoid independent edits to consumers.

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
Only workloads submitted to the dedicated Docker backend use its restricted account.

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
