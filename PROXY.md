# Proxy Architecture

---

## 1. Data path

Egress is a single chain. Three kinds of application traffic converge on the Clash core (mihomo, service mode, runs as root); nftables decides which kind may leave, and locally generated public traffic is proxied, redirected or dropped, subject to the explicit infrastructure and private-network exceptions below. The one thing the firewall cannot judge is the core's own routing: a `DIRECT` match in the profile leaves from the real address by design, and the health gate only samples it (section 9).

```
   env-proxy aware          proxy-ignorant TCP        TUN-captured / fake-ip
   (nix-daemon, curl,       (anything that ignores    (flow routed into the
    Flatpak, hermes)         the env vars)             Mihomo device)
        |                         |                         |
        v                         v  nft REDIRECT           v
   127.0.0.1:33332  ------->  127.0.0.1:33333            TUN "Mihomo"
        |  gost-relay             |  gost-relay             |
        |  (proxy mode)           |  (proxy mode)           |
        +----------->  127.0.0.1:7897  <--------------------+
                       mihomo mixed port
                            |
                     profile rule match
                       /            \
                  DIRECT            proxy group / node
                     |                    |
                     +--------> uplink <--+   (mihomo marks its own
                                               sockets; the kill switch
                                               exempts exactly that mark)
```

DNS is a parallel chain (section 3.2): `systemd-resolved` → `dnsmasq :1054` → `mihomo :1053` (Mode B, fake-ip) or `unbound :1055` (Mode A, DoT). Any plaintext resolver queried on an uplink is redirected into `dnsmasq`.

## 2. Components

| Component | Listener / device | uid | Role |
|---|---|---|---|
| Clash Verge (mihomo) | `127.0.0.1:7897` mixed, `:1053` DNS, TUN `Mihomo` | root (exempted by packet mark) | proxy core, rule engine, DNS resolver |
| `gost-relay` | `127.0.0.1:33332` HTTP, `:33333` redirect | 987 | relay in front of the core; proxy in B, passthrough in A, closed in C or unknown state |
| `dnsmasq` | `127.0.0.1:1054` | 985 | single client-facing resolver; upstream is switched per mode |
| `unbound` | `127.0.0.1:1055` | 983 | encrypted DoT resolver used while mihomo is not the trusted upstream |
| `dhcpcd` | wired uplink DHCP | 986 | wired lease; source of the fallback DHCP resolvers |
| `proxy-mode` | — | root | decides the mode, commits the ruleset, publishes the mode version |
| `dns-upstream` | — | root | reconciles `dnsmasq`'s upstream, publishes DNS state and calls the `dns-policy` broker |
| `dns-policy` | — | root (`CAP_NET_ADMIN` only) | applies short-lived DNS destination leases in the kernel under the policy lock |
| `dns-resolved-policy` | — | root (no capabilities) | keeps the managed TUN out of resolved's default resolver selection |
| `nftables-verify` | — | root | compares live kernel state with the expected rules and reports a result |

Ports, uids, the packet mark (`6666` → `0x00001a0a`), the TUN device name and the TUN route table are single-sourced in `system/config/machine.nix` and `system/config/proxy-options.nix`; the ruleset, the services and the Clash template all read them from there.

## 3. How traffic flows

### 3.1 Host flows (Mode B)

1. **Env-proxy consumers** connect to `127.0.0.1:33332`; `gost-relay` forwards to `127.0.0.1:7897`. The session variables `http_proxy`/`https_proxy`/`all_proxy` (and their uppercase twins), GSettings (`home/config/dconf.nix`) and Flatpak overrides (`system/programs/flatpak.nix`) point here.
2. **Proxy-ignorant TCP** keeps its public destination; the `lf_proxymode_nat` output chains REDIRECT it to `127.0.0.1:33333`, which `gost-relay` also forwards to the core.
3. **Everything the core routes through the TUN** (including fake-ip answers) is read off the `Mihomo` device.
4. The core matches its profile. `DIRECT` and proxy-node sockets both leave on the uplink; the switch rule is `meta mark 0x00001a0a accept`, so the core can reach both nodes and explicitly DIRECT destinations.
5. `proxymode_drops` rejects other public uplink traffic after the explicit DNS/NTP, DHCP and root-UDP exceptions. The root-UDP exception preserves Tailscale hole punching; it matches all root UDP rather than a tailscaled process identity. Ordinary root TCP still needs the core mark or another explicit exception.

### 3.2 DNS

* `systemd-resolved` is NetworkManager's pinned resolver and forwards to `dnsmasq :1054`.
* `dns-upstream` rewrites `/run/dns-upstream/servers.conf` (dnsmasq `servers-file`) and reconciles every pass; a change is applied with a SIGHUP `reload` rather than a restart, and a failed apply stays pending and is retried.
* Mode B upstream: `mihomo :1053` (fake-ip). Mode A / blocked upstream: `unbound :1055` (DoT to AliDNS).
* Mihomo DNS settings come from `my.proxy.managedDns`: additional `direct-nameserver` and `proxy-server-nameserver-policy` resolvers are disabled, and the fake-IP filter mode and IPv6 fake-IP range are fixed. With TUN enabled, `tun.dns-mode` is explicitly pinned to `hijack` in the profile, parser/PATCH policy and health gate.
* The static `lf_nat` chains redirect every `:53` leaving an uplink (v4 and v6) into `dnsmasq`, so a hardcoded resolver in a portal/LAN/CGNAT range cannot leave in the clear.
* Plaintext fallback is a kernel lease, not a coordinator state: `dns-policy` adds the DHCP resolvers to `inet lf_filter dns_plaintext4` with a timeout bounded by the remaining window, and a static `output` base chain (`lf_dns_guard`, priority `-199` → `dns_egress`) drops every other `dnsmasq` upstream query. Stopping, killing or stalling `dns-upstream` therefore cannot keep plaintext DNS permitted past the lease. Split-DNS leases are keyed by interface/server, expire at the original observation plus 120 s and bind the approved UUID, ActiveConnection, ifindex, carrier-down counter and revocation epoch. The dispatcher revokes them at pre-up/pre-down/down; the broker rechecks the connection before granting.

Wired `ens1` remains managed by dhcpcd; NetworkManager excludes it by name and MAC and manages Wi-Fi `wlo1`. `netsec/nm_dns.py` reconciles the active managed Wi-Fi profile at pre-up/up/DHCP events: both IP families ignore automatically supplied DNS, unwanted explicit servers/search domains are cleared, and resolved uses the managed resolver with the `~.` routing domain. `my.proxy.splitDnsProfileUuids` preserves selected profiles for enterprise split DNS; this exemption does not change nftables egress policy. Plaintext fallback reads the original dhcpcd lease or NetworkManager `DHCP4.OPTION`, rather than the effective `IP4.DNS` list.

Split DNS supports only private/ULA upstreams already excluded from the TUN with `allowPrivateEgress`; dnsmasq binds conditional forwarding to the managed Wi-Fi interface. Unreadable or unsupported approved profiles enter `split-error`, disabling forwarding and raising an alarm. Stale connection snapshots are revoked rather than renewed. The whitelist is empty by default.

Only root diagnostics and the dnsmasq UID may query internal upstream ports 1053/1055. Port 1054 remains client-facing, including redirected uplink DNS. A static output guard rejects replies from other UIDs on these reserved DNS ports. The coordinator and health gate authenticate TCP/UDP listeners through `netsec/listeners.py`; local resolver identity failure disables default forwarding as `resolver-error` and cannot open plaintext fallback. Approved conditional forwarding remains subject to its split lease.

Mode A may temporarily use DHCP resolvers for portal/bootstrap recovery. Each opening lasts at most 120 s and must end before the connection session's 300 s deadline, measured from its first need for plaintext. The session key includes the boot ID, interface index, carrier-down counter and, for Wi-Fi, the NetworkManager ActiveConnection object path. A DHCP address/gateway/DNS update changes a parameter fingerprint, not the session budget; a service restart also retains the budget. `plaintextBootBudgetSeconds` can add a boot-wide deadline. `/run/dns-upstream/force-plaintext` is a persistent override while the file exists, until deletion or reboot. Session identity, expiry, remaining budget and override state are published in `/run/dns-upstream/state.json`.

## 4. Modes

`proxy-mode` is the state machine. It decides from live state (the core's cgroup, the TUN, the control socket, mihomo's own log); the recorded status is read back only to carry the change/hysteresis decision and for display:

| Predicate | Source | Meaning |
|---|---|---|
| `isClashOn` | `verge-mihomo` in `clash-verge.service`'s cgroup | the core is running |
| `intent` | `/run/proxy-mode/intent` (`proxy` \| `direct`), written by root | the last explicit mode request; absent = this boot decides |
| `tun_ready` | `Mihomo` device up + FIB `my.proxy.routeTable` + a route via it, for IPv4 and (when enabled) IPv6 | the TUN is actually capturing |
| `mode_direct` | `GET /configs` over the control socket | mihomo is in `direct` mode |
| `health_ok` | `netsec/health.py` checks TCP/UDP listener identities and managed API invariants (mode, the `managed-policy` digest, mark, ports, LAN exposure and TUN parameters), then tries complete HTTP 200/204 + exact-source-port/hostname log + selected-leaf delay proofs for `nodeProbeUrls`, followed by a fake-IP HTTP request through TUN/auto-redirect with its own exact-source-port route log and a final configuration fingerprint check | the managed core configuration, the **effective selected node**, and the transparent TCP data path pass within one shared deadline |

| | **Mode A** | **Mode B** | **Mode C** |
|---|---|---|---|
| entry | `clash-off` (explicit direct intent), or a boot where the core was never seen and no proxy intent is set | core on, TUN ready, node healthy | core on and any of: the TUN is not ready (device/FIB/route); `direct` mode; the whole helper/core cgroup vanished while proxy intent is set; one definite `config`/`route`/`node`/`tun` health failure, or two consecutive `obs` failures (a single transient `obs` no longer cuts) |
| nftables state | fragments empty | `proxyModeRules` loaded | `blockedModeRules` loaded |
| egress | direct, no kill switch | host public traffic proxied or dropped, with explicit exceptions; guest public traffic refused unless IPv4 TPROXY is enabled | only loopback, the core's mark, root's access to the Clash-facing ports (`7897` mixed, `1053` DNS, `33332`/`33333` gost) and to the fake-ip network on 53/80/443 for the transparent self-test (plus its loopback-source replies), DHCP on the uplinks, and DHCP/DNS replies to a guest bridge (bridge + gateway source + 67/53, deliberately **not** uid-scoped) survive; non-root traffic to those ports (TCP and UDP) and every other non-loopback/non-core flow are dropped. The guard is a base chain evaluated before mihomo's redirect, so a non-root TCP flow is cut while it still carries its real destination |
| DNS | `unbound` DoT | `mihomo` fake-ip | `unbound` (its own DoT egress is cut, so DNS stops) |

The mode is driven by boot-scoped operator intent: `clash-on` records proxy intent and `clash-off` records direct intent in `/run/proxy-mode/intent`, and `proxy-mode` writes `/run/proxy-mode/core-seen` when it first sees the core. A missing core stays Mode C whenever intent is `proxy`, or when it was never explicitly cleared and the core was seen this boot — so a crashed core, or the whole helper/core cgroup disappearing, is treated as a failure, never as a silent drop to Mode A. Only an explicit `clash-off` (direct intent) and a fresh boot with no intent return to Mode A. The `core-gone` path keeps a one-pass grace so the teardown in `clash-off` is not caught mid-flight.

### 4.1 The two fragments

Both are emitted by `system/config/network.nix`. The scope loader (`proxy-mode --reload`) concatenates the static ruleset with the mode's fragment and commits them in a **single `nft -f` transaction**, so a start or reload never leaves the fragment chains empty; `rules_off` flushes both.

* `proxyModeRules` — `proxymode_drops` (core-mark accept, DHCP clients pinned to uid 0/`dhcpcd`, all root UDP to preserve Tailscale hole punching, then drop), `proxymode_tail` (reverse default-deny for interfaces not pinned), `proxymode_forward` (guest refusal), and the `:33333` REDIRECT tables. The core keeps its mark and its nodes stay reachable.
* `blockedModeRules` — fills `lf_blocked_guard`, a **base chain on the `output` hook at priority `-200`**, i.e. evaluated before mihomo's own auto-redirect (`nat/output` `-150`) and before every static accept. It accepts loopback, the core's mark, root's access to the fake-ip network on 53/80/443 before auto-redirect, plus loopback-source replies to fake-IP (the route log checks that the core carried the self-test), DHCP clients pinned to uid 0/`dhcpcd` (server replies only for the explicitly enabled AP/subnet; L2-local), and a hop-limit-255 ICMPv6 NDP carve-out for link maintenance, then a terminal `drop`. Running *before* the redirect is what makes the cut real: once the redirect has run, a locally generated non-root TCP flow already carries `127.0.0.1:<inbound>` as its destination and the guard's port list would not match it. The guard is unconditional, so the normal fragment's LAN/DHCP/DNS/NTP accepts do not survive it; the normal fragment and the redirect tables are torn down, and `proxymode_forward` is refilled with the public-destination refusal for guests.

A separate static chain, `redirect_guard` (an `output` base chain at priority `-201`, before NAT), rejects an explicit loopback connection to gost's redirect port with a TCP RST; a flow that is transparently redirected still carries its real destination at that point and is unaffected.

The core is **not** blocked in Mode C: it keeps egress so it can recover a node or be reconfigured from the GUI over loopback. The cost is that traffic mihomo itself routes `DIRECT` (domestic names, or a profile DIRECT fallback) still leaves.

### 4.2 Bootstrap, static repair and guest paths

`network-firewall-bootstrap.service` installs `inet lf_bootstrap` before physical networking: input and forwarding default to drop, with loopback, established input, DHCP and essential ICMPv6 allowed. NetworkManager and dhcpcd require this unit to succeed. The complete static+mode transaction removes the bootstrap table only after installing the full policy; a first-load failure therefore retains ingress protection without changing the normal cold-boot Mode A contract. `my.proxy.bootMode = "proxy"` optionally records proxy intent and installs Mode C in the same early transaction (still before physical networking); the default `direct` keeps the A contract.

The supervisor compares the static baseline separately from the current mode fragment. Static drift invokes an internal complete loader under the existing lock, installing A for a direct session or C for a proxy/recovery session. It then re-evaluates health before entering B. Failed full repairs are limited to one attempt per 30 s and publish `unenforced`; an unsuccessful postcondition cannot remain labelled `proxy`. This status reports that enforcement could not be confirmed and does not itself replace missing kernel protection.

`my.proxy.guestInterfaces` defines all routed guest ingress interfaces (`virbr0` and `waydroid0` by default; the dedicated AP interface `my.machine.hotspot.interface` is added only when the hotspot is enabled). A Wi-Fi interface acting as a client uplink is never treated as guest ingress, so global IPv4/IPv6 replies to the host are not dropped by the pre-routing guard. In B/C, `lf_guest_guard` checks the original public destination at prerouting priority `-201`, ahead of transparent redirection; `proxymode_forward` also rejects both directions before conntrack accepts, independently of the outgoing interface. LAN/CGNAT destinations remain reachable. The libvirt/Waydroid DHCP and DNS services are accepted on input in every mode, and Mode C's reply exception is identified by bridge, gateway source and service port rather than by uid (a daemon that drops privileges keeps its listener's creator uid but its accepted TCP sockets carry the dropped uid). `transparentGuestInterfaces` is empty by default: explicitly listed interfaces receive IPv4 TPROXY only in healthy B, with dynamic rules cleared in A/C and pre-NAT input-path protection in C. Guest IPv6 public traffic remains refused. Host-socket transports such as passt/rootless containers follow the host policy instead of this bridge-forwarding path.

## 5. Coordination and lifecycle

Supervisors are event-driven, with an adaptive backstop (2 s while transitioning, capped at 5 s in an enforced mode / 60 s in direct).

* `proxy-net-watch.path` watches the mihomo control socket and its directory (`/run/user/<desktopUser>/clash-verge-rev`). A core start/stop fires `proxy-net-wake.service`.
* `proxy-net-wake.service` sends `SIGWINCH` to `proxy-mode`, `dns-upstream` and `gost-relay`; each loop's `wait` is interrupted and it re-decides immediately.
* A NetworkManager dispatcher script and a dhcpcd exit hook (`system/config/network.nix`) start the coordinator on a link or lease change.
* `nftables.service` runs the same scope loader for both `ExecStart` and `ExecReload`: under a shared lock it commits the static ruleset plus the current safety fragment in one `nft -f` transaction, so a reload installs Mode C atomically and the loop then works back to B. Stopping the unit does **not** remove the rules, and a failed load leaves the previous rules in place.
* `proxy-tun-watch.service` runs `ip -o monitor link` and wakes the coordinator on any link event naming the TUN device: turning the TUN off/on in the GUI is a second-level event, not poll-bound. (sysfs emits no inotify, so a `systemd.path` cannot see it; rtnetlink is the reliable source.)
* On a policy commit, `proxy-mode` signals `gost-relay` and `dns-upstream` directly (`request_wake`) and queues an immutable verification request (`request_verify`). Each request has its own file under `/run/netsec/requests/`; a `.path` unit drains remaining requests after a busy verifier finishes, rather than relying on repeated oneshot starts being queued.
* `proxy-rules-watch.service` consumes `nft monitor ruleset`, coalesces owned/mihomo table changes and wakes supervision at most once per second. Element updates of the three DNS lease sets are excluded to prevent renewal feedback; their definitions and referencing rules still wake supervision, and periodic comparison checks lease bounds.
* `proxy-mode` is a systemd notify service with a 90 s watchdog. READY and watchdog notifications follow successful policy postconditions; `/run/proxy-mode/heartbeat.json` records that verified round.
* `dns-upstream` is also a notify service with a 90 s watchdog; each pass renews the DNS lease through `dns-policy`, and `dns-policy-sleep` is a required precondition of sleep.target. It revokes every lease before sleep; ExecStopPost queues an independent, retrying dns-policy-resume service after wake or a failed hook. That service waits for an inactive sleep.target, revokes again, removes the sleeping marker and wakes supervision. The broker reports suspended while authorization is disabled.
* Every writer (`proxy-mode`'s loop, `--reload`, and an on/off request) shares `/run/proxy-mode/lock`; the long network probes run outside the lock, and the commit re-checks the intent, the committed version and the core cgroup snapshot. A result superseded by reload/on/off is discarded and re-decided. Each lock window starts with a fresh nft snapshot, including after a probe reacquires the lock. A separate operator lock serializes the entire on/off operation, including helper start/stop.

State files (all under `/run`):

| Path | Content |
|---|---|
| `/run/proxy-mode/status` | `proxy` \| `blocked` \| `direct` \| `unenforced` |
| `/run/proxy-mode/reason` | block reason: `no-tun` \| `direct-mode` \| `core-gone` \| `reload` \| `request`, or the health layer that failed: `config` (managed API drift) \| `obs` (route unobservable) \| `route` (DIRECT/REJECT) \| `node` (selected node) \| `tun` (TUN); with the `obs` hysteresis it reads `obs:1/2` (one failure, still proxy) or `obs:2/2` \| empty |
| `/run/proxy-mode/state.json` | atomic `{mode, reason, version, policy_version, revision, updated_at}`; `version` aliases `policy_version`, while reason-only updates change `revision` |
| `/run/proxy-mode/health.json`, `heartbeat.json` | latest probe result and layer durations; latest successful policy round |
| `/run/netsec/state.json` | atomic `{result, version, checked_at, detail}` for the verified generation |
| `/run/dns-upstream/state.json` | DNS mode/reason, applied `policy_version`, session identity, plaintext expiry/budget and override |
| `/run/dns-policy/state.json` | lease status (`applied`/`suspended`/`revoked`), policy generation when applied, and observation time |
| `/run/netsec/alerts/*.json` | per-owner alert records; `/run/netsec/failed` aggregates their owner names |
| `/run/proxy-mode/version` | commit counter; a liveness result is ignored if this changed underneath it |
| `/run/proxy-mode/intent` | root-written `proxy` \| `direct`; absent = this boot decides |
| `/run/proxy-mode/core-seen` | boot-scoped marker (crash detection) |
| `/run/netsec/result`, `/run/netsec/verified-version` | last verification verdict, and the mode version it verified |
| `/run/gost-relay/status`, `/run/dns-upstream/status`, `/run/dns-upstream/reason` | relay mode, resolver mode, why |

`gost-relay` maps the proxy-mode state directly: `proxy` → forward to `:7897`, `blocked` → no listener (refuse), `direct` → passthrough; every other value, including `unenforced` and an unreadable or absent file, → no listener. In `proxy` it trusts the node/TUN gate `proxy-mode` already passed and only re-checks the mixed listener's cgroup identity, so recovery is not blocked by a second warm-up site. It reaps the previous child before each start, runs without `SO_REUSEPORT`, requires exactly one listener per port, and is bounded by `LimitNOFILE`/`MemoryMax`/`TasksMax`. `nushell`'s `proxy-status` reports the mode, DNS, relay, verification result and alarm state, plus a live probe. Consumers read atomic policy/verification JSON; individual status/version files remain compatibility mirrors. `proxy-control status`/`wait` share readiness checks (`netsec/state.py`): heartbeat, DNS, relay and DNS broker observations must be fresh (within `my.proxy.stateMaxAgeSeconds`) and belong to the current policy version. The broker must report `applied`, verification must cover the same version, and no alarm may be active. Mode B requires mihomo DNS; Mode A requires DoT or a valid plaintext window. `suspended`/`revoked` broker states and `degraded-dot-only` DNS do not count as ready.

## 6. Verification, alerting, repair

At build time each complete mode (static ruleset plus its fragment) is parsed by the same nft implementation inside an isolated LKL userspace kernel, producing an expected nft JSON per mode. `nftables-verify` takes a locked snapshot of the live `nft -j list ruleset` and compares it against the expected JSON for the recorded mode, covering the owned tables, every base chain's hook/priority/policy and the full expression, **rule order included**; only handles, counters and set-element order are normalised.

* an empty chain is valid data; a read or parse error is a distinct failure, never an empty success;
* a liveness result spanning a newer commit is not published; captured requests remain queued, and only requests captured at the beginning of a completed round are acknowledged;
* a legitimately installed Mode C passes even with no core running (core-gone is a valid protected state);
* a failing check publishes a result and an alert (`netsec-alert@`, `/run/netsec/alerts/<owner>.json`, `wall`) and **still exits 0**, so it can never block a corrective rebuild; a premature exit likewise publishes a failure, and a success clears only its own owner's alert. `/run/netsec/failed` remains the aggregate list for compatibility and for `proxy-status`.

The verdict is atomic in `/run/netsec/state.json`; `/run/netsec/result` (`ok`/`degraded`/`failed`) and `/run/netsec/verified-version` mirror it for compatibility. `nftables-verify-repair.service` rebuilds the owned static policy through the same atomic loader and rechecks; `nftables-recover` retries a failed `nftables.service` once a minute.

### 6.1 Configuration checks

```sh
nix build .#checks.x86_64-linux.proxy-regressions
nix build .#checks.x86_64-linux.proxy-rules
nix build .#checks.x86_64-linux.proxy-profile
nix build .#checks.x86_64-linux.proxy-network-vm
nix build .#checks.x86_64-linux.proxy-core
nix build .#nixosConfigurations.lfour.config.system.build.toplevel
```

These checks validate generated rules, profile variants, core policy and network behavior in isolated environments without activating the host configuration. Complete-system builds require the [local application assets](overlays/local-apps/README.md).

## 7. Configuration surface

* `my.proxy.tunMode` (`system/config/proxy-options.nix`, default true) — whether a TUN is required. The Merge template writes `enhanced-mode: fake-ip` and `tun.enable` from it; `proxy-mode` and `nftables-verify` gate the TUN checks on it. `false` selects the plain HTTP-proxy model (no Mode C for a missing TUN, redir-host DNS).
* `my.proxy.tunDev` (default `my.machine.tunDevice` = `Mihomo`) — read by the firewall, the TUN fragment and the verifier.
* `my.proxy.routeTable` (default 2022) — the TUN `iproute2-table-index`, shared by the Merge template, `proxy-mode` and the verifier.
* `my.proxy.fakeIpRange` (default `198.18.0.1/16`) — written into `dns.fake-ip-range`; the guard's `fakeIpNet` is derived from it (read-only), and the verifier checks the client answer lies inside the full CIDR.
* `my.proxy.nodeProbeUrls` (default gstatic plus Microsoft connecttest) — each target can supply a complete HTTP/log/selected-leaf proof; domain targets are included in the Merge script's forced-proxy rules. `tunProbeName` names the fake-IP TUN self-test.
* `my.proxy.healthBudgetSeconds` (default 12, range 8–30) — shared probe deadline across all targets.
* `my.proxy.tunStack` (default `mixed`), `tunMtu` (1500) and `tunCongestionControl` (`cubic`, only for `mips`) — opt-in stack experiments; the core API must match the managed values. The mips TCP controller is separate from host BBR.
* `my.proxy.tunIpv6Address` (default `fdfe:dcba:9876::1/126`) — explicit TUN IPv6 address. With dual-stack TUN, the helper passes `SKIP_SYSTEM_IPV6_CHECK=1` to its core child, keeping the expected TUN structure independent of startup uplink IPv6 availability; IPv6 FIB/route checks remain strict.
* `my.proxy.guestInterfaces` and `transparentGuestInterfaces` — routed guest ingress list (VM/container bridges, plus the dedicated AP interface only when the hotspot is enabled) and its opt-in IPv4 TPROXY subset (default empty).
* `my.hardening.guestTcpPorts`/`guestUdpPorts` — the explicit desktop services libvirt guests may reach when `guestHostServices` is on; DHCP/DNS infrastructure is independent of these switches.
* `my.proxy.bootMode` (`direct` default, or `proxy`) — optional fail-closed boot intent installed with the pre-network baseline.
* `my.proxy.stateMaxAgeSeconds` (default 120) — freshness bound used by `proxy-control status`/`wait` and `proxy-status`.
* `my.proxy.splitDnsProfileUuids` (default empty) and `plaintextBootBudgetSeconds` (default null) — split-DNS profile exceptions and an additional boot-wide plaintext deadline. Approved profiles become dnsmasq `server=/domain/server@interface` entries and connection-bound interface/server leases (public upstreams unsupported); resolved itself stays on the managed resolver.
* `proxy-inspect` — read-only JSON of link/queue/offload/qdisc statistics, softirq counters, service CPU/RSS and current state; it sends no test traffic and changes no tuning.
* `my.hardening.allowPrivateEgress` (default true) — keep `privateV4`/ULA/link-local directly reachable in B; `false` subjects host private-address flows to the proxy policy too (affecting the tailnet and portals). `my.hardening.guestHostServices` (default true) keeps the VM→host desktop-service port list. Resolved has no wildcard DNS listener; guest gateway DNS remains separate.
* `my.browser.zen.dohOnly` (default false) — strict browser DoH-only; `false` keeps DoH preferred with the managed resolver as fallback.
* `my.machine` — interface names, ports (`mihomoDns`/`dnsmasq`/`unbound`/`gostHttp`/`gostRedirect`/`mihomoMixed`/`mihomoTproxy`/`dot`/`ntp`), uids, `mihomoMark`, and the local address ranges (`privateV4`, `multicastV4`, `ulaV6`, `linkLocalV6`, `multicastV6`, `limitedBroadcastV4`).
* The Clash core and its Merge template are configured in `home/config/cvr-merge.nix` (TUN stack, `dns-hijack`, `route-exclude-address`, `routing-mark`, fake-ip DNS + filter, sniffer, `respect-rules`).

## 8. File map

| File | Responsibility |
|---|---|
| `system/config/machine.nix` | interface/uid/port/mark/TUN/tunable single sources |
| `system/config/proxy-options.nix` | `my.proxy.tunMode`, `tunDev`, `fakeIpRange`, `fakeIpNet`, `routeTable`, `nodeProbeUrls` |
| `system/config/network.nix` | nftables ruleset, `proxyModeRules`, `blockedModeRules`, `redirect_guard`, `:53` redirect, env-proxy vars, `dnsmasq`/`unbound`/`resolved`, NM dispatcher, dhcpcd hook |
| `system/programs/systemd/proxy-mode.nix` | mode state machine, predicates, scope loader, `proxy-control`, `request_wake`/`request_verify`, `proxy-net-watch`/`proxy-net-wake` |
| `system/programs/systemd/dns-upstream.nix` | `servers-file` reconciliation and hot reload, bounded plaintext windows |
| `system/programs/systemd/gost-relay.nix` | gost listener lifecycle (proxy / closed / passthrough) |
| `system/programs/systemd/nftables-verify.nix` | expected-JSON verification, result files, timer, opt-in repair |
| `system/programs/systemd/netsec/rules.py` | structured nft JSON comparison (order-sensitive) and CIDR containment; only bounded DNS lease elements are normalised |
| `system/programs/systemd/netsec/link.py` | physical uplink, raw DHCP resolvers and session identity |
| `system/programs/systemd/netsec/dns_policy.py` | kernel DNS lease broker and split-DNS discovery from an approved profile |
| `system/programs/systemd/netsec/resolved_policy.py` | removes the managed TUN from resolved's default resolver selection |
| `system/programs/systemd/netsec/state.py` | shared readiness/freshness checks for `proxy-control` and `proxy-status` |
| `system/programs/systemd/netsec/alerts.py` | per-owner alert records plus the aggregate flag |
| `system/programs/systemd/netsec/managed_profile*.go`, `managed_route.go`, `patch_mihomo.py` | final-parser and controller-PATCH enforcement of the managed core profile (listeners, tunnels, DNS, TUN routing fields); patched into the pinned mihomo build |
| `system/programs/systemd/netsec/patch_sing_tun.py`, `patch_socks.py`, `socks_addr_test.go` | nftables interval endpoints, SOCKS address parsing and boundary tests |
| `system/programs/systemd/netsec/listeners.py` | shared passive TCP/UDP address, UID, cgroup and count checks |
| `system/programs/systemd/netsec/health.py` | bounded selected-node/TUN proof and API invariants |
| `system/programs/systemd/netsec/nm_dns.py` | managed Wi-Fi profile DNS reconciliation |
| `system/programs/systemd/netsec/events.py` | coalesced nft ruleset events |
| `system/programs/systemd/netsec/inspect.py` | read-only network and service observations |
| `system/programs/systemd/network-bootstrap.nix` | pre-network ingress baseline and dependencies |
| `system/programs/systemd/network-observe.nix` | `proxy-inspect` and resource accounting |
| `tests/netsec/` | isolated regressions and real-kernel VM policy tests |
| `system/programs/systemd/netsec-alert.nix` | `netsec-alert@` alert channel |
| `system/programs/systemd/nftables-recover.nix` | retry a failed `nftables.service` |
| `system/config/proxy-profile.nix` | managed core policy (listener/tunnel/DNS/TUN allow-list), fake-IP/route/mark assertions |
| `system/programs/clash-verge.nix` | service-mode core, sing-tun `route-exclude` patch and the managed-policy parser patch |
| `home/config/cvr-merge.nix` | Clash Merge template (TUN, fake-ip DNS, sniffer, `routing-mark`) |
| `home/config/dconf.nix` | GSettings system proxy → gost |
| `system/programs/flatpak.nix` | Flatpak proxy environment overrides |
| `home/config/nushell.nix` | `proxy-status`, `clash-on`/`clash-off`, `egress-audit` |

## 9. Limitations and trade-offs

* The `:33333` redirect is the only path for TCP that the TUN does not carry (source-/device-bound sockets, and anything after a manual flush); `gost` is a single point of failure for those, and flows reaching it are re-sourced to `127.0.0.1`, so per-source profile rules do not apply. An explicit `connect()` to `:33333` is refused with a TCP RST by the static `redirect_guard` base chain (priority `-201`, before NAT), so it can no longer drive gost's self-loop; the supervisor's self-connected-socket detector remains as a backstop. A transparently redirected flow is untouched because it still carries its real destination when the guard runs.
* Mode B's dedicated DNS/NTP exceptions allow `unbound` (DoT, tcp/853) and `systemd-timesyncd` (udp/123); the core mark and root-UDP exception also apply. Ordinary application plaintext resolver queries are redirected into `dnsmasq`. Mode A adds DHCP-provided resolvers only within the bounded plaintext window (section 3.2).
* The kill switch exempts the core by mark rather than a blanket uid-0 allow. DHCP exceptions are pinned to uid 0/`dhcpcd`. Mode B permits **all root UDP** on uplinks to preserve Tailscale arbitrary-port peer/STUN hole punching; this also allows other root UDP. This is an accepted Mode B compatibility exception, not a root boundary: root can already set the core mark. Mode C cuts unmarked tailnet data.
* Mode C keeps the core alive, so mihomo's own `DIRECT` traffic still leaves; loopback stays open for local IPC, but the app-facing proxy ports (`7897`/`33332`/`33333`) are refused for non-root in both TCP and UDP, and a hop-limit-255 ICMPv6 NDP carve-out keeps link maintenance alive without opening a data path. Guest DHCP/DNS replies are allowed by bridge, gateway source and port (`67`/`53`), not by uid: a privileged-port daemon may send them after dropping privileges, and the accepted TCP sockets carry the dropped uid. The guard is a base chain evaluated before mihomo's redirect, so this holds for locally generated TCP too.
* Health is decided by `health_ok` through `netsec/health.py`: each target supplies its own HTTP/log/selected-leaf proof, and policy names are resolved exactly and URL-encoded. DIRECT/REJECT is a routing failure, not a reason to try an unrelated healthy node. Targets share a 12 s deadline by default; the probe runs in a cancellable process group while supervision re-checks core/TUN/configuration. `config`/`route`/`node`/`tun` cut immediately; `obs` needs two consecutive failures, and an event-cancelled probe does not consume an observation. The 5 s value caps sleep, not end-to-end protection/recovery delay. Layer durations are in `health.json`; actual delay depends on the fault and event source. The transparent TCP probe sends a real HTTP request to a fake-IP and requires its exact-source route log; it does not independently prove every UDP/IPv6 TUN device path. Because sing-tun only puts the TUN's `/30` into table 2022, a fake IP outside that subnet still has the uplink as its route when the Mode C filter runs; the guard therefore allows root to the fake-IP network only on 53/80/443 without requiring `oifname`, and the later auto-redirect (priority -150) delivers it to the core. The core's reply leaves its loopback redirect listener before reverse NAT, so the guard also allows `saddr 127.0.0.1` → fake-IP for root. B mode returns every fake-IP destination before the gost redirect for the same reason. If that redirect or reply path is missing, the probe times out and the host stays in C.
* TCP/UDP DNS and mixed listeners must match the expected address, UID and service cgroup; the health gate, coordinator, verifier and relay share passive identity checks. Gost requires exactly one owner per endpoint. Identity failure is a configuration-layer failure; a successful DNS answer alone is insufficient. The "Clash is on" decision requires the core, not just the GUI.
* The mihomo controller socket is mode 0777 without a secret, but its `/run/user/<uid>` ancestor is mode 0700: desktop same-uid processes can reconfigure the root core, rather than every local uid. Managed mode/mark/port/TUN invariants are checked and fingerprinted; rules, geodata and every DIRECT decision are outside that fingerprint. A profile reload can preserve all sampled gates while sending other traffic from the real address. The root core reads user-owned configuration and databases. The helper socket is restricted to `clash-verge-control`; same-uid GUI trust remains part of this desktop architecture. Supervisor/loader capabilities are narrowed, but the design does not contain a compromised root core.
* The managed-profile allow-list is checked at final profile parsing and the controller PATCH entry point. PATCH rejects duplicate keys and noncanonical schema field names; validation and application use the same decoded upstream schema. This is a small patch against the pinned mihomo source (`managed_profile.go`, `managed_route.go`, `patch_mihomo.py`). Anchor drift fails the package build instead of silently dropping the check; revalidate it on every mihomo bump. Likewise, `unbound` is a source override of nixpkgs' `unbound-with-systemd`: it must retain `--enable-systemd`, because the NixOS module uses `Type=notify` and a plain unbound build would hang its start job until the timeout.
* `systemctl stop nftables.service` does not remove the firewall: the unit has no teardown, so the rules stay loaded. To reinstall or reconcile them, use `sudo systemctl restart nftables.service` (or `reload`), which re-runs the scope loader — never `nft flush ruleset`, which also destroys mihomo's own tables and the TUN's routing, and only a TUN restart recreates them.
* Mode A needs the core stopped, not the window closed: in service mode the always-on `clash-verge.service` helper owns the core, so use `clash-off`/`clash-on`. Only these express intent — starting or stopping the core from the GUI does not, so after a `clash-off` (direct intent) a GUI-started core stays in Mode C until you `clash-on`, and after a `clash-on` a GUI-stopped core (or a crash) stays in Mode C until you `clash-off`. That is the fail-closed behaviour, not a bug.
* Forwarded guest/VM traffic carries no host uid and follows the pre-NAT/forward contract in section 4.2. It is not proxied by default; IPv4 TPROXY is an explicit subset option. Existing connections must pass the early guards after switching to C. Socket-based rootless transports need separate host-path validation.
* Documented trade-offs: the Mode A plaintext budget in section 3.2 is a maximum session deadline, not accumulated usage. DHCP parameter changes cannot refresh it; real reconnection starts a new session unless an optional boot-wide deadline has expired. `force-plaintext` is a continuous override while present. In Mode B private/CGNAT access (including `100.64.0.0/10`) is intentional with `allowPrivateEgress=true`; a private address is not a trust guarantee. Cold boot begins in A when no proxy intent/core has been seen; supervision cannot retroactively protect traffic sent before a proxy request.
* `allowPrivateEgress` and `guestHostServices` control private egress and guest desktop-service access. The former wildcard `externalDns` option is removed: LAN/tailnet DNS requires a separately designed listener, client ACL and answer route. The module rejects the legacy option rather than silently exposing a stub.
* `gost` runs from its store path only (`gost-relay` uses absolute paths); it is not on the system PATH and is not copied into the initrd.


---

## 1. 数据路径

出站只有一条链路。三类应用流量最终汇聚到 Clash 内核（mihomo，service mode，以 root 运行）；由 nftables 决定哪一类可以离开，本机产生的公网流量要么被代理、要么被重定向、要么被丢弃，并保留下文明确列出的基础设施与私网例外。防火墙唯一无法判断的是内核自身的路由：profile 里命中 `DIRECT` 的流量会按设计以真实地址离开，健康门只能对它抽样（见第 9 节）。

```
   认环境变量的应用          忽略代理的 TCP           被 TUN 捕获 / fake-ip
   （nix-daemon、curl、      （任何不认环境变量         （被路由进 Mihomo 设备
     Flatpak、hermes）         的应用）                   的流量）
        |                         |                         |
        v                         v  nft REDIRECT           v
   127.0.0.1:33332  ------->  127.0.0.1:33333            TUN "Mihomo"
        |  gost-relay             |  gost-relay             |
        | （proxy 模式）          |  （proxy 模式）         |
        +----------->  127.0.0.1:7897  <--------------------+
                       mihomo mixed 端口
                            |
                     profile 规则匹配
                       /            \
                  DIRECT            代理组 / 节点
                     |                    |
                     +--------> 上行口 <--+   （mihomo 给自己的 socket
                                                打 mark；断网保护恰好豁免
                                                这个 mark）
```

DNS 是并行的另一条链路（见 3.2）：`systemd-resolved` → `dnsmasq :1054` → `mihomo :1053`（模式 B，fake-ip）或 `unbound :1055`（模式 A，DoT）。任何经上行口发出的明文 `:53` 查询都会被重定向进 `dnsmasq`。

## 2. 组件

| 组件 | 监听 / 设备 | uid | 作用 |
|---|---|---|---|
| Clash Verge (mihomo) | `127.0.0.1:7897` mixed、`:1053` DNS、TUN `Mihomo` | root（按 packet mark 豁免） | 代理内核、规则引擎、DNS 解析器 |
| `gost-relay` | `127.0.0.1:33332` HTTP、`:33333` redirect | 987 | 内核前置中转；B 转发、A 直通，C 或未知状态关闭监听 |
| `dnsmasq` | `127.0.0.1:1054` | 985 | 面向客户端的唯一解析器；上游随模式切换 |
| `unbound` | `127.0.0.1:1055` | 983 | mihomo 不作为可信上游时使用的加密 DoT 解析器 |
| `dhcpcd` | 有线上行 DHCP | 986 | 有线租约；后备 DHCP 解析器的来源 |
| `proxy-mode` | — | root | 决定模式、提交 ruleset、发布模式版本 |
| `dns-upstream` | — | root | 协调 `dnsmasq` 上游、发布 DNS 状态并调用 `dns-policy` |
| `dns-policy` | — | root（仅 `CAP_NET_ADMIN`） | 在策略锁下向内核写入短时 DNS 目的租约 |
| `dns-resolved-policy` | — | root（capability 为空） | 让受管 TUN 不参与 resolved 的默认解析器选择 |
| `nftables-verify` | — | root | 把实时内核状态与预期规则比对并给出结果 |

端口、uid、packet mark（`6666` → `0x00001a0a`）、TUN 设备名与 TUN 路由表都在 `system/config/machine.nix` 与 `system/config/proxy-options.nix` 单点定义；ruleset、各服务与 Clash 模板都从这里读取。

## 3. 流量如何工作

### 3.1 主机流量（模式 B）

1. **认环境变量的消费者**连接 `127.0.0.1:33332`，`gost-relay` 转发到 `127.0.0.1:7897`。会话变量 `http_proxy`/`https_proxy`/`all_proxy`（及其大写版本）、GSettings（`home/config/dconf.nix`）与 Flatpak 覆盖（`system/programs/flatpak.nix`）都指向这里。
2. **忽略代理的 TCP** 保留公网目的地址，由 `lf_proxymode_nat` 的 output 链 REDIRECT 到 `127.0.0.1:33333`，`gost-relay` 同样转发到内核。
3. **内核经 TUN 路由的一切**（包括 fake-ip 地址）从 `Mihomo` 设备读出。
4. 内核套用 profile。`DIRECT` 与代理节点 socket 都经上行口离开；放行判据是 `meta mark 0x00001a0a accept`，因此核心既可连接节点，也可访问规则明确选择 DIRECT 的目标。
5. `proxymode_drops` 在明确的 DNS/NTP、DHCP 和 root UDP 例外之后拒绝其它公网出站。root UDP 例外保留 Tailscale 打洞，实际匹配全部 root UDP，并不识别 tailscaled 进程；普通 root TCP 仍须携带核心 mark 或满足其它明确例外。

### 3.2 DNS

* `systemd-resolved` 是 NetworkManager 锁定的解析器，转发给 `dnsmasq :1054`。
* `dns-upstream` 重写 `/run/dns-upstream/servers.conf`（即 dnsmasq 的 `servers-file`）并逐轮协调；变更用 SIGHUP `reload` 热更新而非重启，应用失败则保持 pending 并持续重试。
* 模式 B 上游：`mihomo :1053`（fake-ip）。模式 A / blocked 上游：`unbound :1055`（DoT 到 AliDNS）。
* Mihomo DNS 设置由 `my.proxy.managedDns` 提供：禁用额外的 `direct-nameserver` 与 `proxy-server-nameserver-policy` 解析器，固定 fake-IP filter mode 和 IPv6 fake-IP range。启用 TUN 时，模板、最终解析器/PATCH 策略和健康门共同固定 `tun.dns-mode: hijack`。
* 静态 `lf_nat` 链把所有经上行口发出的 `:53`（v4 与 v6）重定向进 `dnsmasq`，因此门户/局域网/CGNAT 范围内硬编码的解析器不会明文出网。
* 明文后备是**内核租约**而非协调器状态：`dns-policy` 把 DHCP 解析器写入 `inet lf_filter dns_plaintext4`，超时不超过剩余窗口；静态 `output` base chain（`lf_dns_guard`，优先级 `-199` → `dns_egress`）丢弃 `dnsmasq` 的其它上游查询。因此停止、杀死或卡住 `dns-upstream` 都不能让明文许可活过租约。split DNS 租约按接口与服务器匹配，截止不超过原始观测后 120 秒，并绑定批准的 UUID、ActiveConnection、ifindex、carrier-down 计数和撤销代次；dispatcher 在 pre-up/pre-down/down 撤销旧授权，broker 授权前重验连接。

有线 `ens1` 继续由 dhcpcd 管理；NetworkManager 按名称与 MAC 排除它，并管理 Wi-Fi `wlo1`。`netsec/nm_dns.py` 在受管 Wi-Fi 的 pre-up/up/DHCP 事件协调当前连接 profile：IPv4/IPv6 都忽略自动下发的 DNS，清除不需要的显式服务器与搜索域，并给 resolved 指定受管解析器及 `~.` 路由域。`my.proxy.splitDnsProfileUuids` 可为企业 split DNS 保留指定 profile；该例外不改变 nftables 出站策略。明文后备读取 dhcpcd 原始租约或 NetworkManager `DHCP4.OPTION`，不读取已经生效的 `IP4.DNS` 列表。

split DNS 仅支持已由 `allowPrivateEgress` 排除在 TUN 外的私网/ULA 上游，dnsmasq 将条件转发绑定到受管 Wi-Fi。批准 profile 无法读取或上游不受支持时进入 `split-error`，暂停转发并告警；旧连接快照只撤销、不续租。白名单默认为空。

内部上游端口 1053/1055 仅供 root 诊断和 dnsmasq UID 查询；1054 保留客户端访问及上行 DNS 重定向。静态 output guard 拒绝其它 UID 从这些保留端口发出的应答。协调器与健康门共用 `netsec/listeners.py` 验证 TCP/UDP 监听身份；本地解析器身份失败时以 `resolver-error` 暂停默认转发，不因此开放明文后备；已批准的条件转发仍受 split 租约约束。

模式 A 可为门户或 bootstrap 恢复临时使用 DHCP 解析器。每次窗口最多 120 秒，且不能超过连接会话首次需要明文时起算的 300 秒截止。会话键由 boot ID、接口 ifindex、carrier-down 计数及 Wi-Fi 的 NetworkManager ActiveConnection 对象路径组成；DHCP 地址、网关、DNS 更新只改变参数指纹，不重发预算，服务重启也保留预算。`plaintextBootBudgetSeconds` 可再增加本次开机的总截止。`/run/dns-upstream/force-plaintext` 是文件存在期间持续生效的 override，删除或重启机器才取消。会话身份、截止、剩余预算与 override 状态发布在 `/run/dns-upstream/state.json`。

## 4. 模式

`proxy-mode` 就是状态机。它依据实时状态决策（核心 cgroup、TUN、控制口、mihomo 自身日志）；记录下来的 status 只在变更/迟滞判定与展示时被读回：

| 判据 | 来源 | 含义 |
|---|---|---|
| `isClashOn` | `clash-verge.service` cgroup 内的 `verge-mihomo` | 内核在运行 |
| `intent` | `/run/proxy-mode/intent`（`proxy` \| `direct`），由 root 写入 | 最近一次显式模式请求；缺失表示本次开机自动判定 |
| `tun_ready` | `Mihomo` 设备 UP + FIB `my.proxy.routeTable` + 一条经它的路由（IPv4，及启用时的 IPv6） | TUN 确实在抓流量 |
| `mode_direct` | 经控制口 `GET /configs` | mihomo 处于 `direct` 模式 |
| `health_ok` | `netsec/health.py` 核对受管 API 不变量（模式、`managed-policy` 摘要、mark、端口、LAN 暴露、TUN 参数），再按 `nodeProbeUrls` 逐个执行完整的 HTTP 200/204、精确源端口/主机日志与所选叶子节点 delay 证明，之后经 TUN/auto-redirect 向 fake-IP 发真实 HTTP 请求并校验精确源端口的路由日志，最后复核配置指纹 | 受管核心配置、**实际所选节点**与透明 TCP 数据路径在共享总截止内全部通过 |

| | **模式 A** | **模式 B** | **模式 C** |
|---|---|---|---|
| 进入条件 | `clash-off`（显式直连意图），或本次开机从未见过内核且没有代理意图 | 内核在、TUN 就绪、节点健康 | 内核在，且满足任一：TUN 未就绪；`direct` 模式；代理意图下整个 helper/core cgroup 消失；一次明确的 `config`/`route`/`node`/`tun` 健康失败，或连续两次 `obs` 失败（单次瞬时 `obs` 不再切断） |
| nftables | 片段为空 | 装载 `proxyModeRules` | 装载 `blockedModeRules` |
| 出站 | 直连，无断网保护 | 主机公网流量代理或丢弃，保留明确例外；访客公网默认拒绝，可显式启用 IPv4 TPROXY | 仅 loopback、核心 mark、root 对面向 Clash 的端口（`7897` mixed、`1053` DNS、`33332`/`33333` gost）与到 fake-ip 网段 53/80/443 的透明自测访问（及其 loopback 源应答）、上行口 DHCP，以及发往访客网桥的 DHCP/DNS 应答（网桥 + 网关源地址 + 67/53，刻意**不**按 uid 匹配）存活；非 root 对这些端口的流量（TCP 与 UDP）以及所有其它非 loopback/非核心流量都被丢弃。guard 是一条在 mihomo 重定向之前求值的 base chain，因此非 root 的 TCP 流在仍携带真实目的地址时就被切断 |
| DNS | `unbound` DoT | `mihomo` fake-ip | `unbound`（其 DoT 出站已被切断，DNS 随之停止） |

模式由单次开机内的操作员意图驱动：`clash-on` 在 `/run/proxy-mode/intent` 记下代理意图，`clash-off` 记下直连意图；`proxy-mode` 首次见到内核时写 `/run/proxy-mode/core-seen`。只要意图为 `proxy`，或意图从未被显式清除且本次开机见过内核，内核缺失就保持模式 C——因此崩溃的内核、乃至**整个 helper/core cgroup 消失**都按故障处理，绝不会静默退回模式 A。只有显式 `clash-off`（直连意图）与无意图的全新开机才回到模式 A。`core-gone` 路径保留一轮宽限，避免在 `clash-off` 拆除过程中被误触。

### 4.1 两个片段

两者都由 `system/config/network.nix` 生成。scope loader（`proxy-mode --reload`）把静态 ruleset 与该模式的片段拼接、在**同一个 `nft -f` 事务**里提交，因此启动或 reload 都不会让片段链短暂为空；`rules_off` 则全清。

* `proxyModeRules` — `proxymode_drops`（核心 mark accept、固定到 uid 0/`dhcpcd` 的 DHCP 客户端、为 Tailscale 打洞保留的全部 root UDP，然后 drop）、`proxymode_tail`（对未固定接口的反向 default-deny）、`proxymode_forward`（访客拒绝），以及 `:33333` 的 REDIRECT 表。核心保留 mark，节点可达。
* `blockedModeRules` — 填充 `lf_blocked_guard`，它是 **`output` 钩子上、优先级 `-200` 的 base chain**，即在 mihomo 自身的自动重定向（`nat/output` `-150`）与所有静态 accept 之前求值。它放行 loopback、核心 mark、root 到 fake-ip 网段 53/80/443 的访问及 loopback 源应答（之后由 auto-redirect 捕获，并由路由日志验证）、固定到 uid 0/`dhcpcd` 的 DHCP 客户端（服务器应答仅限显式开启的 AP/子网；L2 本地），以及一项 hoplimit 255 的 ICMPv6 邻居发现例外（用于链路维护），最后一条终态 `drop`。**先于重定向**求值正是切断能生效的原因：一旦重定向跑过，本机非 root 的 TCP 流目的地址已被改写成 `127.0.0.1:<inbound>`，guard 的端口列表就匹配不到它了。guard 是无条件的，因此普通片段里的 LAN/DHCP/DNS/NTP accept 都活不过它；普通片段与重定向表被拆除，`proxymode_forward` 重新填入"拒绝公网目的地址"的规则给访客。

另有一条静态链 `redirect_guard`（`output` 钩子、优先级 `-201`，在 NAT 之前）：对显式连接 loopback 上 gost redirect 端口的流量回以 TCP RST；而真正被透明重定向的流在此刻仍携带真实目的地址，不受影响。

模式 C 中核心**不被**阻断：它保留出站，以便恢复节点或从 GUI 经 loopback 重配。代价是 mihomo 自己判为 `DIRECT` 的流量（国内域名，或 profile 的 DIRECT 兜底）仍会离开。

### 4.2 启动基线、静态恢复与访客路径

`network-firewall-bootstrap.service` 在物理网络启动前装入 `inet lf_bootstrap`：入站与转发默认拒绝，放行 loopback、已建立的入站连接、DHCP 与必要 ICMPv6。NetworkManager 和 dhcpcd 都要求该单元成功。完整静态规则与模式片段提交成功时，才在同一事务中删除 bootstrap 表；因此首次加载失败仍保留入站保护，同时不改变正常冷启动进入模式 A 的合同。`my.proxy.bootMode = "proxy"` 可在同一早期事务里（仍早于物理网络）写入代理意图并直接装入模式 C；默认 `direct` 保持 A 的合同。

监督器分别比较静态基线与当前模式片段。静态漂移会在已有共享锁内调用完整 loader：直连会话装入 A，代理或恢复会话装入 C，再重新通过健康门进入 B。完整修复失败按 30 秒限频并发布 `unenforced`；后置校验失败不能继续标记为 `proxy`。这一状态表示无法确认保护已安装，状态发布本身不能代替缺失的内核规则。

`my.proxy.guestInterfaces` 定义全部转发访客入口，默认 `virbr0`、`waydroid0`；仅在显式开启热点时加入独立的 AP 接口 `my.machine.hotspot.interface`。作为客户端上行的 Wi-Fi 接口**不**再算作访客入口，因此发往宿主公网 IPv4/IPv6 地址的回复不会被前置 guard 误杀。B/C 的 `lf_guest_guard` 在 prerouting priority `-201`、透明重定向之前检查原始公网目标；`proxymode_forward` 还在 conntrack accept 前双向拒绝，不依赖出口接口。LAN/CGNAT 目标保留可达。libvirt/Waydroid 的 DHCP 与 DNS 服务在所有模式都由 input 放行，模式 C 的应答例外按网桥、网关源地址与服务端口识别，而不是按 uid（会降权的守护进程保留监听 socket 创建者的 uid，但 accept 出来的 TCP socket 携带降权后的 uid）。`transparentGuestInterfaces` 默认空：显式列出的接口仅在健康 B 安装 IPv4 TPROXY 动态规则，A/C 清空，C 的提前拦截覆盖 input 路径；访客 IPv6 公网仍被拒绝。passt/rootless 容器等转成宿主 socket 的流量遵循宿主策略，与桥接转发路径不同。

## 5. 协调与生命周期

各守护脚本事件驱动，并带自适应兜底（切换中 2 秒，enforced 模式封顶 5 秒 / direct 封顶 60 秒）。

* `proxy-net-watch.path` 监听 mihomo 控制 socket 及其目录（`/run/user/<desktopUser>/clash-verge-rev`）。内核起停触发 `proxy-net-wake.service`。
* `proxy-net-wake.service` 向 `proxy-mode`、`dns-upstream`、`gost-relay` 发 `SIGWINCH`；各循环的 `wait` 被打断并立即重新决策。
* NetworkManager dispatcher 脚本与 dhcpcd exit hook（`system/config/network.nix`）在链路变化或租约变化时启动协调器。
* `nftables.service` 的 `ExecStart` 与 `ExecReload` 走同一个 scope loader：在共享锁下把静态 ruleset 与当前安全片段拼成一个 `nft -f` 事务提交，因此 reload 会原子地装入模式 C，之后由常驻循环恢复 B。停止单元**不会**删除规则；装载失败时旧规则保持不变。
* 策略提交时，`proxy-mode` 直接唤醒 `gost-relay` 与 `dns-upstream`（`request_wake`），并生成不可变的校验请求（`request_verify`）。每个请求对应 `/run/netsec/requests/` 下的独立文件；`.path` 在繁忙的 verifier 完成后继续处理剩余待办，不依赖多次 start oneshot 恰好排成队。
* `proxy-rules-watch.service` 消费 `nft monitor ruleset`，合并自有/mihomo 表的变更，每秒最多唤醒一次监督器。三个 DNS 租约集合的元素增删不触发唤醒，避免续租反馈；集合定义和引用规则仍触发监督，周期比较继续检查租约上限。
* `proxy-mode` 是带 90 秒 watchdog 的 systemd notify 服务，策略后置检查成功才发 READY 与 watchdog 通知；`/run/proxy-mode/heartbeat.json` 记录该次成功轮次。
* `dns-upstream` 同样是带 90 秒 watchdog 的 notify 服务；每轮通过 `dns-policy` 续租，`dns-policy-sleep` 是 sleep.target 的强依赖，撤销全部租约成功才允许休眠；ExecStopPost 在恢复或钩子失败后启动独立的 dns-policy-resume 重试服务。后者确认 sleep.target 已退出，再撤销、清除 sleeping 标记并唤醒监督器。禁止授权期间 broker 发布 suspended 状态。
* 所有写入者（`proxy-mode` 主循环、`--reload`、on/off 请求）共用 `/run/proxy-mode/lock`；耗时的网络探测在锁外执行，提交前重新核对意图、已提交版本与核心 cgroup 快照。被 reload/on/off 取代的探测结果会被丢弃并重判。每个锁定窗口都重新取得 nft 快照，探针后重新加锁也一样；另有 operator lock 串行化包括 helper 起停在内的完整 on/off 操作。

状态文件（均在 `/run` 下）：

| 路径 | 内容 |
|---|---|
| `/run/proxy-mode/status` | `proxy` \| `blocked` \| `direct` \| `unenforced` |
| `/run/proxy-mode/reason` | 阻断原因：`no-tun` \| `direct-mode` \| `core-gone` \| `reload` \| `request`，或健康门失败层：`config`（受管 API 漂移）\| `obs`（路由不可观测）\| `route`（DIRECT/REJECT）\| `node`（所选节点）\| `tun`（TUN）；带 `obs` 迟滞时读作 `obs:1/2`（失败一次，仍在 proxy）或 `obs:2/2` \| 空 |
| `/run/proxy-mode/state.json` | 原子 `{mode, reason, version, policy_version, revision, updated_at}`；`version` 与 `policy_version` 同值，仅原因变化更新 `revision` |
| `/run/proxy-mode/health.json`、`heartbeat.json` | 最近探针的结果与分层耗时；最近策略成功轮次 |
| `/run/netsec/state.json` | 原子 `{result, version, checked_at, detail}`，绑定被验证的策略代次 |
| `/run/dns-upstream/state.json` | DNS 模式/原因、已应用的 `policy_version`、会话身份、明文截止/预算与 override |
| `/run/dns-policy/state.json` | 租约状态（`applied`/`suspended`/`revoked`）、应用时的策略代次及观测时间 |
| `/run/netsec/alerts/*.json` | 按 owner 分文件的告警；`/run/netsec/failed` 聚合其 owner 名称 |
| `/run/proxy-mode/version` | 提交计数器；若期间它变了，运行检查结果会被忽略 |
| `/run/proxy-mode/intent` | 由 root 写入的 `proxy` \| `direct`；缺失表示本次开机自动判定 |
| `/run/proxy-mode/core-seen` | 单次开机内的崩溃检测标记 |
| `/run/netsec/result`、`/run/netsec/verified-version` | 最近一次验证结论，以及它所验证的模式版本 |
| `/run/gost-relay/status`、`/run/dns-upstream/status`、`/run/dns-upstream/reason` | 中继模式、解析器模式、原因 |

`gost-relay` 直接映射 proxy-mode 状态：`proxy` → 转发到 `:7897`，`blocked` → 无监听（拒绝），`direct` → 直通；其余一切取值（含 `unenforced`、以及不可读或缺失的状态文件）→ 无监听。在 `proxy` 下它信任 `proxy-mode` 已通过的节点/TUN 健康门，只复核 mixed 监听者的 cgroup 身份，因此恢复不会被"第二站点预热"拖住。它在每次启动前先回收上一个子进程，不再使用 `SO_REUSEPORT`，要求每个端口恰好一个监听者，并由 `LimitNOFILE`/`MemoryMax`/`TasksMax` 加以约束。`nushell` 的 `proxy-status` 会打印模式、DNS、中继、验证结论与告警状态，外加一次实时探测。消费端读取原子的策略/校验 JSON，独立 status/version 文件作为兼容镜像保留。`proxy-control status`/`wait` 共用同一套就绪检查（`netsec/state.py`）：心跳、DNS、中继及 DNS broker 的观测必须在 `my.proxy.stateMaxAgeSeconds` 内，且属于当前策略代次；broker 状态须为 `applied`，校验须覆盖同一代次，并且没有活动告警。模式 B 要求使用 mihomo DNS，模式 A 要求 DoT 或有效的明文窗口。broker 的 `suspended`/`revoked` 状态及 DNS 的 `degraded-dot-only` 均不视为就绪。

## 6. 校验、告警、修复

构建时，每种完整模式（静态 ruleset 加其片段）都由同一 nft 实现在一个隔离的 LKL 用户态内核里解析，生成该模式的预期 nft JSON。`nftables-verify` 在共享锁下对实时 `nft -j list ruleset` 取快照，与记录模式对应的预期 JSON 比对，覆盖自有表、每条 base chain 的 hook/priority/policy 以及完整表达式，**包含规则次序**；只规范化 handle、计数与集合元素顺序。

* 空链是合法数据；读取或解析错误是独立的失败，绝不等价于"空即成功"；
* 跨越新提交的运行检查结果不发布，捕获的请求保留重跑；完成一轮后只确认该轮开始时捕获的请求，不能删除后来到达的请求；
* 合法安装的模式 C 即使没有内核运行也通过（core-gone 是合法保护态）；
* 失败会发布结果与告警（`netsec-alert@`、`/run/netsec/alerts/<owner>.json`、`wall`）并**仍然退出 0**，因此不会挡住修复性 rebuild；意外提前退出同样发布失败，成功只清除自己 owner 的告警。`/run/netsec/failed` 仍作为聚合列表保留，兼容 `proxy-status`。

结论原子发布在 `/run/netsec/state.json`，并以 `/run/netsec/result`（`ok`/`degraded`/`failed`）与 `/run/netsec/verified-version` 保留兼容镜像。`nftables-verify-repair.service` 走同一个原子 loader 重建自有静态策略并复查；`nftables-recover` 每分钟重试一次失败的 `nftables.service`。

### 6.1 配置检查

```sh
nix build .#checks.x86_64-linux.proxy-regressions
nix build .#checks.x86_64-linux.proxy-rules
nix build .#checks.x86_64-linux.proxy-profile
nix build .#checks.x86_64-linux.proxy-network-vm
nix build .#checks.x86_64-linux.proxy-core
nix build .#nixosConfigurations.lfour.config.system.build.toplevel
```

这些检查在隔离环境中验证生成的规则、配置分支、核心策略与网络行为，不激活宿主配置。完整系统构建需要准备[本地应用资源](overlays/local-apps/README.md)。

## 7. 配置面

* `my.proxy.tunMode`（`system/config/proxy-options.nix`，默认 true）——是否要求 TUN。Merge 模板据此写 `enhanced-mode: fake-ip` 与 `tun.enable`；`proxy-mode` 与 `nftables-verify` 据此决定是否校验 TUN。`false` 选择纯 HTTP 代理模型（缺 TUN 不进入模式 C，DNS 用 redir-host）。
* `my.proxy.tunDev`（默认 `my.machine.tunDevice` = `Mihomo`）——防火墙、TUN 片段与校验器都读取。
* `my.proxy.routeTable`（默认 2022）——TUN 的 `iproute2-table-index`，Merge 模板、`proxy-mode` 与校验器共用。
* `my.proxy.fakeIpRange`（默认 `198.18.0.1/16`）——写入 `dns.fake-ip-range`；guard 的 `fakeIpNet` 由它派生（只读），校验器检查客户端答案落在完整 CIDR 内。
* `my.proxy.nodeProbeUrls`（默认 gstatic 与 Microsoft connecttest）——每个目标都可执行完整 HTTP/日志/所选叶子证明；域名目标加入 Merge script 的强制代理规则。`tunProbeName` 是 fake-IP TUN 自测域名。
* `my.proxy.healthBudgetSeconds`（默认 12，范围 8–30）——全部目标共享的一轮探针总截止。
* `my.proxy.tunStack`（默认 `mixed`）、`tunMtu`（1500）与 `tunCongestionControl`（`cubic`，仅用于 `mips`）——显式栈实验入口；核心 API 必须与受管值一致，mips TCP 控制器与宿主 BBR 独立。
* `my.proxy.tunIpv6Address`（默认 `fdfe:dcba:9876::1/126`）——显式 TUN IPv6 地址。双栈 TUN 下 helper 向核心子进程传递 `SKIP_SYSTEM_IPV6_CHECK=1`，让预期结构不依赖启动瞬间的上行 IPv6 状态，IPv6 FIB/路由校验仍然严格。
* `my.proxy.guestInterfaces` 与 `transparentGuestInterfaces`——转发访客入口（VM/容器网桥，仅在开启热点时加入独立 AP 接口）及其显式启用的 IPv4 TPROXY 子集，后者默认空。
* `my.hardening.guestTcpPorts`/`guestUdpPorts`——`guestHostServices` 开启时 libvirt 访客可访问的显式桌面服务；DHCP/DNS 基础设施不受该开关影响。
* `my.proxy.bootMode`（默认 `direct`，可选 `proxy`）——可选的 fail-closed 开机意图，随物理网络前的基线一起安装。
* `my.proxy.stateMaxAgeSeconds`（默认 120）——`proxy-control status`/`wait` 与 `proxy-status` 使用的观测新鲜度上限。
* `my.proxy.splitDnsProfileUuids`（默认空）与 `plaintextBootBudgetSeconds`（默认 null）——split DNS profile 例外与额外的本次开机明文总截止。被批准的 profile 会生成 dnsmasq `server=/domain/server@interface` 与绑定连接的接口/服务器租约（不支持公网上游）；resolved 自身仍走受管解析器。
* `proxy-inspect`——只读输出接口/队列/offload/qdisc、softirq、服务 CPU/RSS 与当前状态 JSON，不发测试流量，不修改调优参数。
* `my.hardening.allowPrivateEgress`（默认 true）——在 B 中保留 `privateV4`/ULA/link-local 直达；`false` 会把宿主私网流量也置于代理策略约束下（影响 tailnet 与门户）。`my.hardening.guestHostServices`（默认 true）保留 VM→宿主桌面服务端口列表。resolved 不提供通配 DNS 监听，访客网关 DNS 独立保留。
* `my.browser.zen.dohOnly`（默认 false）——浏览器严格 DoH-only；`false` 保持 DoH 优先、以受管解析器为后备。
* `my.machine`——接口名、端口（`mihomoDns`/`dnsmasq`/`unbound`/`gostHttp`/`gostRedirect`/`mihomoMixed`/`mihomoTproxy`/`dot`/`ntp`）、uid、`mihomoMark`，以及本地地址段（`privateV4`、`multicastV4`、`ulaV6`、`linkLocalV6`、`multicastV6`、`limitedBroadcastV4`）。
* Clash 内核与其 Merge 模板配置在 `home/config/cvr-merge.nix`（TUN 栈、`dns-hijack`、`route-exclude-address`、`routing-mark`、fake-ip DNS 与过滤器、sniffer、`respect-rules`）。

## 8. 文件映射

| 文件 | 职责 |
|---|---|
| `system/config/machine.nix` | 接口/uid/端口/mark/TUN/可调项的单点定义 |
| `system/config/proxy-options.nix` | `my.proxy.tunMode`、`tunDev`、`fakeIpRange`、`fakeIpNet`、`routeTable`、`nodeProbeUrls` |
| `system/config/network.nix` | nftables ruleset、`proxyModeRules`、`blockedModeRules`、`redirect_guard`、`:53` 重定向、env 代理变量、`dnsmasq`/`unbound`/`resolved`、NM dispatcher、dhcpcd hook |
| `system/programs/systemd/proxy-mode.nix` | 模式状态机、判据、scope loader、`proxy-control`、`request_wake`/`request_verify`、`proxy-net-watch`/`proxy-net-wake` |
| `system/programs/systemd/dns-upstream.nix` | `servers-file` 协调与热更新、有界明文窗口、DNS 状态与 watchdog |
| `system/programs/systemd/netsec/dns_policy.py` | 内核 DNS 租约 broker，及从批准 profile 生成 split DNS |
| `system/programs/systemd/netsec/resolved_policy.py` | 让受管 TUN 退出 resolved 默认解析器选择 |
| `system/programs/systemd/netsec/state.py` | `proxy-control` 与 `proxy-status` 共用的就绪/新鲜度检查 |
| `system/programs/systemd/netsec/alerts.py` | 按 owner 的告警文件与聚合标记 |
| `system/programs/systemd/netsec/managed_profile*.go`、`managed_route.go`、`patch_mihomo.py` | 在最终解析器与控制口 PATCH 处强制受管核心配置（监听、隧道、DNS、TUN 路由字段），随固定版本 mihomo 构建打入 |
| `system/programs/systemd/netsec/patch_sing_tun.py`、`patch_socks.py`、`socks_addr_test.go` | nftables interval 端点、SOCKS 地址解析与边界测试 |
| `system/programs/systemd/gost-relay.nix` | gost 监听生命周期（proxy / closed / 直通） |
| `system/programs/systemd/nftables-verify.nix` | 预期 JSON 校验、结果文件、定时器、可选修复 |
| `system/programs/systemd/netsec/rules.py` | 结构化 nft JSON 比对（含规则次序）与 CIDR 包含判断 |
| `system/programs/systemd/netsec/link.py` | 物理上行、原始 DHCP 解析器与会话身份 |
| `system/programs/systemd/netsec/listeners.py` | 共用 TCP/UDP 地址、UID、cgroup 与监听数量检查 |
| `system/programs/systemd/netsec/health.py` | 有界的所选节点/TUN 证明与 API 不变量 |
| `system/programs/systemd/netsec/nm_dns.py` | 受管 Wi-Fi profile 的 DNS 协调 |
| `system/programs/systemd/netsec/events.py` | 合并 nft ruleset 事件 |
| `system/programs/systemd/netsec/inspect.py` | 只读网络与服务观测 |
| `system/programs/systemd/network-bootstrap.nix` | 网络启动前的入站基线与依赖 |
| `system/programs/systemd/network-observe.nix` | `proxy-inspect` 与资源统计 |
| `tests/netsec/` | 隔离回归与真实内核 VM 策略测试 |
| `system/programs/systemd/netsec-alert.nix` | `netsec-alert@` 告警通道 |
| `system/programs/systemd/nftables-recover.nix` | 重试失败的 `nftables.service` |
| `system/programs/clash-verge.nix` | service mode 内核、sing-tun `route-exclude` 补丁与受管配置解析器补丁 |
| `system/config/proxy-profile.nix` | 受管核心配置（监听/隧道/DNS/TUN 白名单）与 fake-IP/路由/mark 断言 |
| `home/config/cvr-merge.nix` | Clash Merge 模板（TUN、fake-ip DNS、sniffer、`routing-mark`） |
| `home/config/dconf.nix` | GSettings 系统代理 → gost |
| `system/programs/flatpak.nix` | Flatpak 代理环境覆盖 |
| `home/config/nushell.nix` | `proxy-status`、`clash-on`/`clash-off`、`egress-audit` |

## 9. 限制与取舍

* `:33333` 重定向是 TUN 未承载的 TCP（绑定源/设备的套接字、以及手工 flush 之后的流量）的唯一路径；`gost` 是这些流的单点故障，且到达它的流会被改源为 `127.0.0.1`，因此按来源匹配的 profile 规则不适用。显式 `connect()` 到 `:33333` 会被静态的 `redirect_guard` base chain（优先级 `-201`，在 NAT 之前）回以 TCP RST，因此不再能诱发 gost 自循环；supervisor 的自连套接字检测保留为后备。真正被透明重定向的流在 guard 求值时仍携带真实目的地址，因而不受影响。
* 模式 B 的专门 DNS/NTP 例外放行 `unbound`（DoT，tcp/853）与 `systemd-timesyncd`（udp/123），核心 mark 和 root UDP 例外也适用；普通应用的明文解析器查询被重定向进 `dnsmasq`。模式 A 仅在有预算的明文窗口内加入 DHCP 解析器（见 3.2）。
* 断网保护按 mark 豁免核心，不对 uid 0 整体放行；DHCP 例外固定到 uid 0/`dhcpcd`。模式 B 在上行口放行**全部 root UDP**，保留 Tailscale 任意对端/STUN 端口的打洞，其它 root UDP 也会放行。这是模式 B 的兼容取舍，不构成 root 边界，root 本就可以设置核心 mark。模式 C 切断未打标的 tailnet 数据。
* 模式 C 保留核心存活，因此 mihomo 自身判为 `DIRECT` 的流量仍会离开；loopback 保留供本机 IPC 使用，但面向应用的代理端口（`7897`/`33332`/`33333`）对非 root 的 TCP 与 UDP 都被拒绝，另有一项 hoplimit 255 的 ICMPv6 邻居发现例外维持链路维护而不开放数据通道。访客 DHCP/DNS 应答按网桥、网关源地址与端口（`67`/`53`）放行，不按 uid：特权端口守护进程会降权，accept 出来的 TCP socket 携带降权后的 uid。guard 是在 mihomo 重定向之前求值的 base chain，因此对本机产生的 TCP 同样成立。
* 健康检查由 `health_ok` 调用 `netsec/health.py`：每个目标提供独立的 HTTP/日志/所选叶子证明，策略名精确解析并经 URL 编码。DIRECT/REJECT 属于路由失败，不能换无关健康节点充数。全部目标默认共享 12 秒截止；探针运行在可取消的进程组中，监督器期间复查核心/TUN/配置。`config`/`route`/`node`/`tun` 失败立即切断，`obs` 需连续两次，事件取消不消耗观测次数。5 秒是睡眠上限，不是端到端保护/恢复时延；分层耗时在 `health.json`，实际时延取决于故障及事件来源。透明 TCP 探针会向 fake-IP 发真实 HTTP 请求并校验精确源端口的路由日志；它不单独证明 TUN 设备的全部 UDP/IPv6 数据路径。由于 sing-tun 只把 TUN 的 `/30` 放进 table 2022，/30 之外的 fake-IP 在 Mode C filter 求值时路由仍指向上行口；因此 guard 对 root 到 fake-IP 只放行 53/80/443，且不要求 `oifname`，由之后 `-150` 的 auto-redirect 交给核心。核心的应答从 loopback redirect 监听发出、在反向 NAT 之前经过此 chain，所以 guard 还放行 root 的 `saddr 127.0.0.1` → fake-IP。B 模式同样在 gost 重定向前放行所有 fake-IP 目的地址。若重定向或回包路径缺失，探针超时并保持 Mode C。
* TCP/UDP DNS 与 mixed 监听须符合预期地址、UID 和服务 cgroup；健康门、协调器、校验器和中继共用被动身份检查。gost 各端口也须恰好一个合法监听者。身份失败属于 config 层失败，不能只凭 DNS 能应答放行。“Clash 在运行”要求核心，而非仅 GUI。
* mihomo 控制 socket 为 mode 0777、无 secret，但祖先 `/run/user/<uid>` 是 0700：桌面同 UID 进程可重配 root 核心，并非所有本机 UID 都能访问。模式/mark/端口/TUN 不变量被检查并记录指纹，规则、geodata 与全部 DIRECT 决策不在指纹范围内；profile 重载仍可能保留全部抽样健康门，同时使其它流量以真实地址离开。root 核心读取用户所有的配置与数据库，helper socket 限定到 `clash-verge-control` 组；GUI 同 UID 信任仍属桌面架构取舍。监督器/loader 已收窄 capability，设计不隔离已失陷的 root 核心。
* 受管配置白名单在最终 profile 解析和控制口 PATCH 入口检查。PATCH 拒绝重复键和 schema 字段的非规范名称，校验与应用使用同一次解码的上游 schema。这是对固定版本 mihomo 源码的小补丁（`managed_profile.go`、`managed_route.go`、`patch_mihomo.py`）。上游锚点变化会让包构建失败，而不是悄悄丢掉检查；每次升级 mihomo 都要复核。`unbound` 则是对 nixpkgs `unbound-with-systemd` 的源码覆盖，必须保留 `--enable-systemd`，因为 NixOS 模块使用 `Type=notify`，换成普通 unbound 构建会让启动 job 挂到超时。
* `systemctl stop nftables.service` 不会移除防火墙：该 unit 没有拆除动作，规则保持装载。要重新装载或校正，用 `sudo systemctl restart nftables.service`（或 `reload`），它会重跑 scope loader —— 绝不要 `nft flush ruleset`，它还会毁掉 mihomo 自己的表与 TUN 路由，只有重启 TUN 才能重建。
* 模式 A 需要停掉核心，而非只关窗口：service mode 下常驻的 `clash-verge.service` helper 托管核心，因此用 `clash-off`/`clash-on`。只有这两条会表达意图——从 GUI 起停核心不会：所以 `clash-off`（直连意图）之后用 GUI 起核心仍停在模式 C，直到再 `clash-on`；`clash-on` 之后用 GUI 停核心（或崩溃）也停在模式 C，直到 `clash-off`。这是 fail-closed 行为，不是 bug。
* 转发访客/虚拟机流量不带宿主 uid，遵循 4.2 的 pre-NAT/forward 合同。默认不代理，IPv4 TPROXY 须显式指定子集；切入 C 后已有连接也须通过提前拦截。转成宿主 socket 的 rootless 传输须另按宿主路径验证。
* 已记录的取舍：3.2 中模式 A 的明文预算是会话最长截止，不是累计时长；DHCP 参数变化不刷新，实际重连建立新会话，但可选的开机总截止仍然有效。`force-plaintext` 存在期间持续显式覆盖预算。模式 B 在 `allowPrivateEgress=true` 时按设计直达私网/CGNAT（含 `100.64.0.0/10`），私网地址不代表可信。无代理意图且从未见核心的冷启动以 A 开始，监督器不能追溯保护提出代理请求之前已发出的流量。
* `allowPrivateEgress` 与 `guestHostServices` 分别控制私网出站和访客桌面服务权限。旧的通配 `externalDns` 选项已移除；LAN/tailnet DNS 需要独立设计监听、客户端 ACL 与答案路由，模块对旧选项明确报错，不再静默暴露 stub。
* `gost` 只从 store 路径运行（`gost-relay` 用绝对路径）；不在系统 PATH 上，也不复制进 initrd。
