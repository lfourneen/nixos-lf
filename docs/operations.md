# Network operations

English | [简体中文](CN/operations_CN.md) | [Documentation](README.md)

## Switching modes

Use the Nushell commands `clash-on` and `clash-off`. They record privileged intent, manage the GUI/helper lifecycle and wait for coherent state. Closing the GUI alone leaves proxy intent active and therefore enters C when the core stops.

`sudo proxy-control on` and `sudo proxy-control off` are the underlying system controls. `on` opens the single-use configuration-import window and starts the helper; the GUI still needs to supply/start the core. `off` clears authorization and stops the helper. The desktop commands also handle GUI processes. Starting only `clash-verge` in A does not authorize a core.

```sh
proxy-control status proxy
proxy-control wait proxy
proxy-control status direct
firefox --check
proxy-inspect
```

`status` returns readiness JSON; `wait` waits for the requested mode's shared readiness conditions. `proxy-status` in Nushell combines mode, DNS, relay, verifier, alarms and a live probe. `egress-audit` supplies additional active diagnostics. `proxy-inspect` only reads state, service resources and link/queue/offload statistics; it sends no probe traffic.

## Remote desktop

Sunshine is enabled and starts with `graphical-session.target`, exposing the current session as **Desktop**. After deployment, log into the desktop and open `https://localhost:47990` on the computer to set credentials and approve Moonlight's pairing PIN. Both the phone and computer must be logged into the same permitted tailnet. Add the computer's Tailscale IP manually in Moonlight; discovery is not required. The same overlay address works on LAN and over the internet, subject to the tailnet's access policy. Direct access by physical LAN IP is not enabled by the default empty `trustedLanCidrs`.

The web UI accepts only local clients; it is not a default tailnet firewall allowance. UPnP is disabled. Streaming uses the explicit tailnet TCP/UDP port lists in [hardening-options.nix](../system/config/hardening-options.nix). Configure phone-to-computer grants in the Tailscale admin console separately; that policy is not stored in this repository. A and healthy B allow remote desktop; C cuts the session, including existing UDP flows and decrypted tailnet input. Use `clash-off` locally when direct A is intended.

[sunshine.nix](../system/programs/sunshine.nix) retains the global CUDA build variant. The locked 2026.914.233613 CUDA package is available from the already configured `https://cache.nixos-cuda.org`. A substituter must provide the exact derivation selected by the lock and package options; adding a CUDA cache does not guarantee every CUDA package/version. Before upgrades, verify downloading the selected package with `nix build --max-jobs 0 --no-link .#nixosConfigurations.lfour.config.services.sunshine.package`; this refuses local compilation. Credentials and pairing state remain in the user's Sunshine directory; the Desktop entry and safety settings are declarative. Hardware capture/encoding and phone streaming require validation in the deployed graphical session.

```sh
systemctl --user status sunshine.service
journalctl --user -b -u sunshine.service
sudo tailscale netcheck
sudo tailscale ping <phone-tailnet-name-or-IP>
```

For the optional restricted B policy, Unbound DoT switch and time destination pins, see [Direct egress](egress.md). Restricted B retains negotiated Sunshine media; C always cuts remote control.

On Tailscale upgrades, run `tailscale-transport`, `proxy-rules`, `proxy-egress-rules`, `proxy-tailscale-vm` and `proxy-egress-vm`. The source contract check is also required by normal system builds. `0x80000` is Tailscale's upstream Linux socket mark, stable across network changes but reviewed on upgrades. If the mark is unavailable or bind-to-device is forced, B does not reopen all-root UDP: diagnose marking/permissions and use intentional A if needed.

## State and diagnosis

| File | Meaning |
|---|---|
| `/run/proxy-mode/state.json` | Atomic mode, reason, policy version, revision and update time |
| `/run/proxy-mode/health.json` | Probe verdict and per-layer durations |
| `/run/proxy-mode/heartbeat.json` | Last successful supervisor round |
| `/run/netsec/state.json` | Verification verdict, checked generation and freshness |
| `/run/dns-upstream/state.json` | DNS mode/reason, encryption policy, session, budget, deadline and override |
| `/run/dns-policy/state.json` | Broker lease state and applied generation |
| `/run/gost-relay/state.json` | Relay observation and applied generation |
| `/run/netsec/alerts/*.json` | Per-owner alarms; `/run/netsec/failed` lists owners |
| `/run/proxy-mode/intent`, `core-seen` | Boot-scoped operator intent and crash-detection marker |
| `/run/proxy-mode/authorized-config`, `config-digest`, `authorize-window` | Root-only snapshot authority and single-use import grant |

Atomic JSON is the consumer interface. Separate `status`, `reason`, `version`, `result` and `verified-version` files are compatibility mirrors. Reason-only updates change `revision`; kernel-policy commits change `policy_version` (`version` is its alias). Results spanning a newer generation are discarded.

Readiness requires current-generation mode, DNS, broker and relay observations, no active alarm and fresh verification. Broker state must be `applied`; `suspended`/`revoked` and DNS `degraded-dot-only` are not ready. Consumer freshness defaults to 120 seconds; verifier freshness defaults to 420 seconds, covering its five-minute timer and bounded execution. Boot identity and monotonic timestamps reject cross-boot, future and stale records.

| Reason | Where to look |
|---|---|
| `request`, `reload` | Transition awaiting core health and verification |
| `unauthorized-core`, `core-gone` | Operator intent, launcher/helper and core cgroup |
| `no-tun`, `tun` | Device, managed routing table/rules, real route selection and fake-IP HTTP path |
| `config` | Snapshot digest, managed API values and TCP/UDP listener identity |
| `route` | Actual logged leaf is DIRECT/REJECT or inconsistent with the selected graph |
| `obs`, `obs:1/2`, `obs:2/2` | HTTP completion, exact source-port log and selection observations |
| `resolver-error`, `split-error` | DNS listener identity or approved Wi-Fi profile/lease |

```sh
journalctl -b -u proxy-mode -u dns-upstream -u gost-relay -u clash-verge
cat /run/proxy-mode/health.json
cat /run/dns-upstream/state.json
cat /var/lib/dns-upstream/history.log
```

Some service logs and authority files require sudo. Do not publish snapshots or subscription-bearing configuration as diagnostic output.

## Coordination and firewall repair

Socket-directory events, TUN link monitoring, nft events, NetworkManager dispatch and dhcpcd hooks wake the supervisors. Adaptive polling backs them up (two seconds during transitions, capped at five seconds in enforced modes and 60 seconds in direct mode); the mode supervisor has a 90-second watchdog. A cancelled probe does not count as a failed observation.

Build-time [expected rules](../system/programs/systemd/proxy-mode.nix) are generated with the same nft implementation inside an LKL userspace kernel. The verifier compares owned tables, hooks, priorities, policies and full ordered expressions. Handles, counters, set-element order and tightly bounded authorized DNS leases are normalized. Read/parse errors are failures, never empty successes. A correctly installed C passes even with no core.

`nftables-verify` publishes failure/alarms but exits zero so repair/rebuild jobs can proceed. Check the JSON verdict, not just unit exit status. Each component clears only its own alarm. Queued verification requests survive a busy round.

```sh
sudo systemctl restart nftables.service
sudo systemctl start nftables-verify.service
```

Restart/reload reinstalls the owned static policy plus the safety mode atomically; proxy intent goes through C before health reopens B. Stopping nftables leaves its policy installed. `nftables-verify-repair.service` performs explicit repair and recheck; `nftables-recover` retries a failed nftables unit. Avoid flushing the global ruleset.

## Configuration

| Source | Settings |
|---|---|
| [machine.nix](../system/config/machine.nix) | User, interfaces/match keys, UIDs, ports, mark, address ranges and upstream endpoints |
| [proxy-options.nix](../system/config/proxy-options.nix) | TUN, route table 2022, fake-IP pool, probe URLs, boot intent, proxyOnly, dnsEncryption, shared domain filtering, localTargetCidrs, remoteTargetGrants, nat64Prefixes, guests, split DNS and freshness |
| [proxy-profile.nix](../system/config/proxy-profile.nix) | Root-owned managed DNS/TUN/profile contract |
| [hardening-options.nix](../system/config/hardening-options.nix) | Host/guest private access, explicit host-service ports, hotspot, tailnet and Wi-Fi MAC policy |
| [cvr-merge.nix](../home/config/cvr-merge.nix) | Matching Clash GUI Merge template and routing enhancement |
| [firefox.nix](../home/programs/firefox/firefox.nix), [options.nix](../home/programs/firefox/options.nix) | Native browser, networkPath, privacy, policy, fonts and launcher |

TUN defaults: `Mihomo`, table 2022, rule priority 9000, internal marks 8227/8228, fallback priority 32768, file descriptor 0. Parser, template and health share these values. `tunMode=false` selects HTTP-only/redir-host operation, dropping the requirement for a TUN. Fake-IP network is derived from `fakeIpRange`; avoid independent edits to consumers.

For core access to a local/internal service, [target authorization](proxy.md#domain-targets) separates local DIRECT CIDRs from remote node grants. An internal suffix only permits a locally resolved private answer; it does not authorize node-side resolution or dialing. Remote grants use the actual leaf name and core inlet (Gost CONNECT arrives as `HTTPS`), transport and destination ports. Rebuild and deliberately import/restart the managed core after changing these options. A retains literal portal access; C remains blocked. Native [direct-egress grants](egress.md) are independent.

For a Wi-Fi profile requiring the hardware MAC:

```sh
nmcli connection modify uuid <UUID> 802-11-wireless.cloned-mac-address permanent
# Restore the declared default on the next activation:
nmcli connection modify uuid <UUID> 802-11-wireless.cloned-mac-address ""
```

`preserve` retains the current MAC; it does not restore the hardware address. An explicit profile setting takes precedence over `my.hardening.wifi.clonedMacAddress`. Networks sharing an SSID share the default stable identity.

## Offline recovery

If direct access is intended, use `clash-off`. For portal/DNS recovery in A under the default `dnsEncryption="opportunistic"`, inspect DNS state/history, then explicitly enable the bounded-renewal override if needed:

```sh
sudo touch /run/dns-upstream/force-plaintext
# Remove after recovery; reboot also clears it.
sudo rm /run/dns-upstream/force-plaintext
```

This permits plaintext DNS and continues billing the session budget; it cannot override C's egress guard. See [DNS](dns.md) for the exact scope.

With `my.proxy.dnsEncryption="strict"`, the override is ignored and no plaintext fallback is granted. `clash-off` still selects A with encrypted DNS. Try a literal HTTP portal address, another uplink, or repair the authenticated DoT configuration. A portal requiring unavailable public DNS needs an intentional policy change, for example an already built `opportunistic` generation selected offline. Prepare such a generation before relying on strict mode while travelling. Private split DNS and MagicDNS remain available under their normal approvals and mode constraints.

For a broken generation, select an older systemd-boot entry. The boot menu retains eight entries to fit the ESP. A store path retained by garbage collection can also be reactivated offline: inspect `/nix/var/nix/profiles/system-*-link`, set the system profile with `nix-env -p /nix/var/nix/profiles/system --set /nix/store/<nixos-system-…>`, then run `/nix/var/nix/profiles/system/bin/switch-to-configuration boot` as root. If nothing boots, unlock/mount root and the ESP from a NixOS ISO and run the recovery inside `nixos-enter --root /mnt`.

## Configuration checks

[Patch maintenance](maintenance.md#validation) maps changes to checks, distinguishing temporary-file tests, package builds, isolated kernel VMs and host activation. Run the relevant set together after editing. Copying the checkout with `scripts/push-to-dir.sh` deploys build inputs and test sources; it neither runs the checks nor copies `docs/`.
