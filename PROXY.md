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

The configuration imports no `system/programs/ssh.nix`, so it deploys no sshd service or port-22 listener. Enabling sshd also requires an explicit firewall rule in `system/config/network.nix`. Nix daemon trust is restricted to root; normal users retain ordinary builds and declared signed caches, while administrative changes use sudo.

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
* Mode B upstream: `mihomo :1053` (fake-ip). Mode A / blocked upstream: `unbound :1055` (DoT to 360).
* Unbound filters upstream answers containing LAN/CGNAT, guest, loopback, unspecified, link-local or ULA addresses, including the corresponding IPv4-mapped IPv6 ranges. Explicit Unbound `local-data` and narrowly configured `private-domain` entries remain available. This covers the Mode-A DoT path; approved split DNS and the leased plaintext portal fallback use dnsmasq directly. Mihomo's domain destinations have a separate guard described below; independent browser DoH answers are outside Unbound's scope.
* Mihomo DNS settings come from `my.proxy.managedDns`: additional `direct-nameserver` and `proxy-server-nameserver-policy` resolvers are disabled, and the fake-IP filter mode and IPv6 fake-IP range are fixed. With TUN enabled, `tun.dns-mode` is explicitly pinned to `hijack` in the profile, parser/PATCH policy and health gate.
* The static `lf_nat` chains redirect every `:53` leaving an uplink (v4 and v6) into `dnsmasq`, so a hardcoded resolver in a portal/LAN/CGNAT range cannot leave in the clear.
* Plaintext fallback is a kernel lease, not a coordinator state: `dns-policy` adds the DHCP resolvers to `inet lf_filter dns_plaintext4` with a timeout bounded by the remaining window, and a static `output` base chain (`lf_dns_guard`, priority `-199` → `dns_egress`) drops every other `dnsmasq` upstream query. Stopping, killing or stalling `dns-upstream` therefore cannot keep plaintext DNS permitted past the lease. Split-DNS leases are keyed by interface/server, expire at the original observation plus 120 s and bind the approved UUID, ActiveConnection, ifindex, carrier-down counter and revocation epoch. The dispatcher revokes them at pre-up/pre-down/down; the broker rechecks the connection before granting.

Wired `ens1` remains managed by dhcpcd; NetworkManager excludes it by name and MAC and manages Wi-Fi `wlo1`. `netsec/nm_dns.py` reconciles the active managed Wi-Fi profile at pre-up/up/DHCP events: both IP families ignore automatically supplied DNS, unwanted explicit servers/search domains are cleared, and resolved uses the managed resolver with the `~.` routing domain. `my.proxy.splitDnsProfileUuids` preserves selected profiles for enterprise split DNS; this exemption does not change nftables egress policy. Plaintext fallback reads the original dhcpcd lease or NetworkManager `DHCP4.OPTION`, rather than the effective `IP4.DNS` list.

Wi-Fi scans use randomized MAC addresses. Connections default to `stable-ssid`: the SSID and this machine's persistent identity determine the address, so reconnecting to the same SSID keeps it while different SSIDs use different identities. `/var/lib/NetworkManager` and `/etc/machine-id` are persisted across root rollback. Networks sharing an SSID also share this identity; MAC randomization does not hide IP addresses or authenticated accounts.

`my.hardening.wifi.clonedMacAddress` controls this default. An explicit `802-11-wireless.cloned-mac-address` in a connection profile takes precedence. For a network requiring its hardware MAC, use `nmcli connection modify uuid <UUID> 802-11-wireless.cloned-mac-address permanent`; a literal MAC is also supported. Clear that property with `nmcli connection modify uuid <UUID> 802-11-wireless.cloned-mac-address ""` to inherit the default again. The setting applies on the next activation; `preserve` keeps the current address and does not restore the hardware MAC. Wired identity remains fixed.

Split DNS supports only private/ULA upstreams already excluded from the TUN with `allowPrivateEgress`; dnsmasq binds conditional forwarding to the managed Wi-Fi interface. Unreadable or unsupported approved profiles enter `split-error`, disabling forwarding and raising an alarm. Stale connection snapshots are revoked rather than renewed. The whitelist is empty by default.

Only root diagnostics and the dnsmasq UID may query internal upstream ports 1053/1055. Port 1054 remains client-facing, including redirected uplink DNS. A static output guard rejects replies from other UIDs on these reserved DNS ports. The coordinator and health gate authenticate TCP/UDP listeners through `netsec/listeners.py`; local resolver identity failure disables default forwarding as `resolver-error` and cannot open plaintext fallback. Approved conditional forwarding remains subject to its split lease.

The dnsmasq unit keeps its root-start/self-drop and D-Bus reload lifecycle, with a bounded capability set and a read-only system view except for its state directory. Unbound's loopback high-port forwarder has no ambient or bounding capabilities; transparent listening and privileged binds are not part of this service's contract. Guest DHCP/DNS daemons remain separate.

Mode A may temporarily use plaintext resolvers for portal/bootstrap recovery, and to keep DNS answering while the encrypted path is unhealthy. Each opening lasts at most 120 s, is renewed while the session budget lasts, and must end before the connection session's 1800 s (30 min) deadline, measured from its first need for plaintext. Reaching a validated encrypted answer resets that budget, so a later outage is not punished for an earlier one. The session key includes the boot ID, interface index, carrier-down counter and, for Wi-Fi, the NetworkManager ActiveConnection object path. A DHCP address/gateway/DNS update changes a parameter fingerprint, not the session budget; a service restart also retains the budget. `plaintextBootBudgetSeconds` can add a boot-wide deadline. `/run/dns-upstream/force-plaintext` is a persistent override while the file exists, until deletion or reboot; it is the operator's explicit decision to keep plaintext DNS even when the validator reports a failure, and it is still bounded by the 120 s renewal. Session identity, expiry, remaining budget, override state and the latest probe verdict are published in `/run/dns-upstream/state.json` and appended to `/var/lib/dns-upstream/history.log`.


Mode A uses 360's DoT endpoints (`101.226.4.6`, `218.30.118.6`, `123.125.81.6`) with TLS hostname `dot.360.cn`. Measured from this uplink, every foreign DoT endpoint (Cloudflare `1.1.1.1`, Quad9 `9.9.9.9`/`149.112.112.112`, Google `8.8.8.8`) times out at the transport level, so none can serve as the validated upstream. Domestic endpoints that answer are not interchangeable: AliDNS (`223.5.5.5`/`223.6.6.6`) and `dot.pub` answer but strip RRSIGs, which `harden-dnssec-stripped` turns into SERVFAIL for signed zones; 360 answers and returns RRSIG material for both the root and a signed `A` query, which is what a local validator needs. Mihomo’s domestic/bootstrap DNS stays independent. Unbound validates DNSSEC locally in A with `validator iterator`, ignores client CD requests and maintains its RFC 5011 root anchor in `/var/lib/unbound`. The initial anchor comes from the locked `dns-root-data` package, so startup does not need an unauthenticated anchor download. The health probe keeps two stages apart: an 8 s query to Unbound for a fresh random name under the root produces the validator verdict — its validated NSEC denial proves the chain live and can never be served from cache, unlike a repeated DNSKEY query — with AD required and EDE 5–12 as the only signature error, and a direct TLS query to the encrypted upstreams (5 s per attempt, stopping at the first that answers, so a slow upstream is never mistaken for a dead one) attributes an inconclusive verdict to the network rather than to the signature. That second stage also reports whether the upstream returned RRSIG material at all, because an upstream that strips signatures cannot feed a local validator. The first verdicts of a cold resolver are deferred, and only while that transport stage answered, so a cold trust-anchor priming cannot be read as a failure. A verdict that is unavailable or negative no longer blackouts DNS: dnsmasq moves to the bounded plaintext window (120 s leases inside a 1800 s per-session budget, renewed while the budget lasts and reset once the encrypted path validates again) using the DHCP resolvers, or `1.1.1.1`/`1.0.0.1` when the link supplies none. The fallback is deliberately not an Unbound upstream, and stays a different operator from the validated endpoints. The verdict, rcode, EDE codes and failure category are published in `/run/dns-upstream/state.json` and appended to `/var/lib/dns-upstream/history.log`, so a boot that had to be rolled back still carries the evidence. Once the budget is spent only the encrypted resolver remains, and `force-plaintext` stays the explicit operator override. A validating probe demonstrates resolver health, not that every queried zone is signed. Split DNS, leased plaintext fallback and B’s Mihomo DNS do not acquire local DNSSEC validation from this setting.

Full-config parsing upgrades only exact recognized pre-MagicDNS managed DNS values to the current root policy, and always takes `lf-private-targets` from that policy: a cached profile from an older generation must not stop the core from starting, and a profile value can never weaken the effective guard. Other profile drift and runtime PATCH attempts to weaken the policy are still rejected.

The managed Mihomo core checks domain targets after routing/sniffing and before TCP/UDP egress, including each datagram in a reused UDP association. It rejects private, loopback, link-local, unspecified and multicast answers, including mixed public/private answers and IPv4-mapped IPv6. Carrier-grade NAT stays in that list: Tailscale peers are reached by literal addresses or the authenticated `*.ts.net` suffix, both exempt, so no dial legitimately resolves a domain into `100.64.0.0/10`. A lookup that still fails after one retry is not a target verdict: the dial proceeds and resolves on its own path, so a resolver hiccup cannot be reported as a dead node. It pins the checked address for both DIRECT and proxy dials, preventing a second remote DNS lookup. Literal-IP requests, internal core infrastructure and the explicit suffixes in `my.proxy.privateTargetDomains` (`localhost`, `lan`, `local`, `home.arpa`) are exempt. The authenticated current MagicDNS suffix is a dynamic exception. Public domain targets therefore resolve locally before proxy dialing; this affects remote-DNS behavior and uses the core's IPv4 preference. This is domain-target protection, not a universal SSRF boundary or protection against a proxy server that disregards the requested destination.

Tailscale declares `accept-dns=false`, `accept-routes=false`, no advertised subnet routes or exit-node service, no exit node and Tailscale SSH disabled. `my.hardening.tailscale.magicDns` defaults true: the root helper reads the running daemon's current MagicDNS suffix, gives only that search/routing domain to `tailscale0`, and sets `DefaultRoute=no`. The read uses a 5 s deadline, and an unreadable or slow daemon is not a logout: the last authenticated route is kept, so the conditional resolver does not flap and the exception file is not deleted several times a minute. Logout, a tailnet change or disabled MagicDNS revokes the old suffix. No tailnet `~.` route takes over public DNS. Mihomo excludes `ts.net` from fake-IP and conditionally resolves these names directly via the local Tailscale resolver, preserving Tailscale IP routes even under a GLOBAL profile; the private-target exemption remains limited to the authenticated current suffix.

Time synchronization uses Chrony with NTS-only sources (`my.machine.ntsUpstreams`, Cloudflare and Netnod by default), `authselectmode require` and certificate time checks enabled. UDP/123 and TCP/4460 are scoped to the chrony UID in B's firewall exceptions; C retains its non-core egress refusal, including time synchronization. Failed authentication leaves the clock unsynchronized instead of selecting ordinary NTP. Persistent `/var/lib/chrony` state and `chronyd -s` supply an RTC/drift-file time floor after restart; the module's `rtcfile`/`rtcautotrim` keep that floor accurate, so a clock too far off for NTS certificates recovers from the RTC instead of deadlocking DNS. The local command port stays enabled, so `chronyc sources` and `chronyc authdata` can tell a failed NTS-KE from a failed source. If the clock is still too wrong for TLS, an administrator must correct it; no certificate-time bypass or plaintext time fallback is configured.

## 4. Modes

`proxy-mode` is the state machine. It decides from live state (the core's cgroup, the TUN, the control socket, mihomo's own log); the recorded status is read back only to carry the change/hysteresis decision and for display:

| Predicate | Source | Meaning |
|---|---|---|
| `isClashOn` | `verge-mihomo` in `clash-verge.service`'s cgroup | the core is running |
| `intent` | `/run/proxy-mode/intent` (`proxy` \| `direct`), written by root | the last explicit mode request; absent = this boot decides |
| `tun_ready` | `Mihomo` device up + FIB `my.proxy.routeTable` + a route via it, for IPv4 and (when enabled) IPv6; actual unmarked IPv6 route selection for root and the desktop UID | the required kernel routing observations pass |
| `mode_direct` | `GET /configs` over the control socket | mihomo is in `direct` mode |
| `health_ok` | `netsec/health.py` checks TCP/UDP listener identities and managed API invariants (mode, the `managed-policy` digest, mark, ports, LAN exposure and TUN parameters), then tries complete HTTP 200/204 + exact-source-port/hostname log + current selected-leaf validation for `nodeProbeUrls`, followed by a fake-IP HTTP request through TUN/auto-redirect with its own exact-source-port route log and a final configuration fingerprint check | the managed core configuration, the **effective selected node**, and the transparent TCP data path pass within one shared deadline |

| | **Mode A** | **Mode B** | **Mode C** |
|---|---|---|---|
| entry | `clash-off` (explicit direct intent), or a boot where the core was never seen and no proxy intent is set | core on, TUN ready, node healthy | core on and any of: the TUN is not ready (device/FIB/route); `direct` mode; the whole helper/core cgroup vanished while proxy intent is set; one definite `config`/`route`/`tun` health failure, or two consecutive `obs` failures (including HTTP timeouts) |
| nftables state | fragments empty | `proxyModeRules` loaded | `blockedModeRules` loaded |
| egress | direct, no kill switch | host public traffic proxied or dropped, with explicit exceptions; guest public traffic refused unless IPv4 TPROXY is enabled | only loopback, the core's mark, root's access to the Clash-facing ports (`7897` mixed, `1053` DNS, `33332`/`33333` gost) and to the fake-ip network on 53/80/443 for the transparent self-test (plus its loopback-source replies), DHCP on the uplinks, and DHCP/DNS replies to a guest bridge (bridge + gateway source + 67/53, deliberately **not** uid-scoped) survive; non-root traffic to those ports (TCP and UDP) and every other non-loopback/non-core flow are dropped. The guard is a base chain evaluated before mihomo's redirect, so a non-root TCP flow is cut while it still carries its real destination |
| DNS | `unbound` DoT, falling back to the bounded plaintext window while the encrypted path has no verdict | `mihomo` fake-ip | `unbound` (its own DoT egress is cut, so DNS stops) |

The mode is driven by boot-scoped operator intent: `clash-on` records proxy intent and `clash-off` records direct intent in `/run/proxy-mode/intent`, and `proxy-mode` writes `/run/proxy-mode/core-seen` when it first sees the core. A missing core stays Mode C whenever intent is `proxy`, or when it was never explicitly cleared and the core was seen this boot — so a crashed core, or the whole helper/core cgroup disappearing, is treated as a failure, never as a silent drop to Mode A. Only an explicit `clash-off` (direct intent) and a fresh boot with no intent return to Mode A. The `core-gone` path keeps a one-pass grace so the teardown in `clash-off` is not caught mid-flight.

### 4.1 The two fragments

Both are emitted by `system/config/network.nix`. The scope loader (`proxy-mode --reload`) concatenates the static ruleset with the mode's fragment and commits them in a **single `nft -f` transaction**, so a start or reload never leaves the fragment chains empty; `rules_off` flushes both.

* `proxyModeRules` — `proxymode_drops` (core-mark accept, DHCP clients pinned to uid 0/`dhcpcd`, all root UDP to preserve Tailscale hole punching, then drop), `proxymode_tail` (reverse default-deny for interfaces not pinned), `proxymode_forward` (guest refusal), and the `:33333` REDIRECT tables. The core keeps its mark and its nodes stay reachable.
* `blockedModeRules` — fills `lf_blocked_guard`, a **base chain on the `output` hook at priority `-197`**, i.e. evaluated after conntrack (`-200`) and before mihomo's own auto-redirect (`nat/output` `-150`) and the ordinary output accepts. It accepts loopback, the core's mark, root's access to the fake-ip network on 53/80/443 before auto-redirect, plus loopback-source replies and root TCP DNAT replies from the TUN address to fake-IP (conntrack must identify a reply whose original destination was fake-IP port 80/443; the route log checks that the core carried the self-test), DHCP clients pinned to uid 0/`dhcpcd` (server replies only for the explicitly enabled AP/subnet; L2-local), and a hop-limit-255 ICMPv6 NDP carve-out for link maintenance, then a terminal `drop`. Running *before* the redirect is what makes the cut real: once the redirect has run, a locally generated non-root TCP flow already carries `127.0.0.1:<inbound>` as its destination and the guard's port list would not match it. The guard is unconditional, so the normal fragment's LAN/DHCP/DNS/NTP accepts do not survive it; the normal fragment and the redirect tables are torn down, and `proxymode_forward` is refilled with the public-destination refusal for guests.

A separate static chain, `redirect_guard` (an `output` base chain at priority `-201`, before NAT), rejects an explicit loopback connection to gost's redirect port with a TCP RST; a flow that is transparently redirected still carries its real destination at that point and is unaffected.

The packaged GUI keeps the public `clash-verge` process identity used by `clash-on`/`clash-off`. Before launching the current package, its launcher waits for older GUI instances of the same user to exit, so an old singleton cannot retain a previous core path. An already running GUI from the current package is preserved.

The core is **not** blocked in Mode C: it keeps egress so it can recover a node or be reconfigured from the GUI over loopback. The cost is that traffic mihomo itself routes `DIRECT` (domestic names, or a profile DIRECT fallback) still leaves.

### 4.2 Bootstrap, static repair and guest paths

`network-firewall-bootstrap.service` installs `inet lf_bootstrap` before physical networking: input and forwarding default to drop, with loopback, established input, DHCP and essential ICMPv6 allowed. NetworkManager and dhcpcd require this unit to succeed. The complete static+mode transaction removes the bootstrap table only after installing the full policy; a first-load failure therefore retains ingress protection without changing the normal cold-boot Mode A contract. `my.proxy.bootMode = "proxy"` optionally records proxy intent and installs Mode C in the same early transaction (still before physical networking); the default `direct` keeps the A contract.

The supervisor compares the static baseline separately from the current mode fragment. Static drift invokes an internal complete loader under the existing lock, installing A for a direct session or C for a proxy/recovery session. It then re-evaluates health before entering B. Failed full repairs are limited to one attempt per 30 s and publish `unenforced`; an unsuccessful postcondition cannot remain labelled `proxy`. This status reports that enforcement could not be confirmed and does not itself replace missing kernel protection.

`my.proxy.guestInterfaces` defines all routed guest ingress interfaces (`virbr0` and `waydroid0` by default; the dedicated AP interface `my.machine.hotspot.interface` is added only when the hotspot is enabled). A Wi-Fi interface acting as a client uplink is never treated as guest ingress, so global IPv4/IPv6 replies to the host are not dropped by the pre-routing guard. In B/C, `lf_guest_guard` checks the original public destination at prerouting priority `-201`, ahead of transparent redirection; `proxymode_forward` also rejects both directions before conntrack accepts, independently of the outgoing interface. LAN/CGNAT destinations remain reachable. The libvirt/Waydroid DHCP and DNS services are accepted on input in every mode, and Mode C's reply exception is identified by bridge, gateway source and service port rather than by uid (a daemon that drops privileges keeps its listener's creator uid but its accepted TCP sockets carry the dropped uid). `transparentGuestInterfaces` is empty by default: explicitly listed interfaces receive IPv4 TPROXY only in healthy B, with dynamic rules cleared in A/C and pre-NAT input-path protection in C. Guest IPv6 public traffic remains refused. Host-socket transports such as passt/rootless containers follow the host policy instead of this bridge-forwarding path.

Libvirt/Waydroid guest input reaches `lf_guest_host` before the generic conntrack accept. The default permits only bridge-scoped DHCP/DNS (plus explicitly enabled TPROXY); service removal also cuts established guest-initiated host connections. Libvirt desktop services require `guestHostServices=true` and explicit `guestTcpPorts`/`guestUdpPorts`. Private forwarding remains enabled by default (`my.hardening.guestPrivateEgress=true`). Set it false to restrict these bridges before NAT and established-flow accepts; `guestPrivateExceptions` grants exact interface/CIDR/TCP-or-UDP/port tuples. Gateway infrastructure and replies to host-initiated connections remain available, while existing A/B/C constraints still apply. This option does not change a separately enabled hotspot's policy.

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
| `/run/proxy-mode/reason` | block reason: `no-tun` \| `direct-mode` \| `core-gone` \| `reload` \| `request`, or the health layer that failed: `config` (managed API drift) \| `obs` (HTTP/log/selection proof unavailable) \| `route` (DIRECT/REJECT) \| `tun` (TUN); with the `obs` hysteresis it reads `obs:1/2` (one failure; an existing proxy mode is retained) or `obs:2/2` \| empty |
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

The supervisor and verifier share `netsec/tun.py`: a bounded JSON observation of the device, both routing tables/rules, and actual unmarked IPv6 routes for root and the desktop UID. A route that chooses an uplink despite a surviving TUN table forces Mode C through the existing supervisor decision/commit path. Managed TUN inputs also pin rule priority 9000, internal marks 8227/8228, fallback priority 32768 and file-descriptor 0; profile parsing, PATCH, templates and the health view share those values. An upstream RawTun schema coverage test requires explicit classification of new fields.

Health resolves the connection's logged `group[leaf]` against known complete names and validates that recorded leaf against the current graph. A subsequent `now` cannot replace a recorded DIRECT result. LoadBalance uses its per-connection leaf without requiring `now`; the managed core also logs the actual chain in global mode. Ambiguous names or a changed selection invalidate the observation.

Verification results carry CLOCK_BOOTTIME and a boot ID. Readiness requires both freshness and the current policy generation. `my.proxy.verificationMaxAgeSeconds` defaults to 420 seconds, separately from the 120-second consumer heartbeat contract; this accommodates the 300-second verifier timer, 30-second timer accuracy and bounded 60-second execution. Missing, future or cross-boot verification is never ready.

## 7. Configuration surface

* `my.proxy.tunMode` (`system/config/proxy-options.nix`, default true) — whether a TUN is required. The Merge template writes `enhanced-mode: fake-ip` and `tun.enable` from it; `proxy-mode` and `nftables-verify` gate the TUN checks on it. `false` selects the plain HTTP-proxy model (no Mode C for a missing TUN, redir-host DNS).
* `my.proxy.tunDev` (default `my.machine.tunDevice` = `Mihomo`) — read by the firewall, the TUN fragment and the verifier.
* `my.proxy.routeTable` (default 2022) — the TUN `iproute2-table-index`, shared by the Merge template, `proxy-mode` and the verifier.
* `my.proxy.fakeIpRange` (default `198.18.0.1/16`) — written into `dns.fake-ip-range`; the guard's `fakeIpNet` is derived from it (read-only), and the verifier checks the client answer lies inside the full CIDR.
* `my.proxy.nodeProbeUrls` (default HTTPS gstatic plus Cloudflare generate_204) — each target can supply a complete HTTP/log/selected-leaf proof; domain targets are included in the Merge script's forced-proxy rules. `tunProbeName` names the fake-IP TUN self-test.
* `my.proxy.delayTestUrl` (default `https://www.gstatic.com/generate_204`) — the global profile script replaces missing/HTTP URLs in url-test/fallback/load-balance groups and enabled provider health checks; existing HTTPS URLs and disabled checks are preserved. At GUI startup it also fills an unset delay-test URL in an existing GUI configuration; custom URLs and manual/group selections retain their precedence. The first launch uses the GUI’s own initial settings.
* `my.proxy.healthBudgetSeconds` (default 12, range 8–30) — shared probe deadline across all targets.
* `my.proxy.healthRequestTimeoutSeconds` (default 5, range 1–10) — maximum for each real HTTP health request, including connection/TLS setup, capped by the shared deadline.
* `my.proxy.tunStack` (default `mixed`), `tunMtu` (1500) and `tunCongestionControl` (`cubic`, only for `mips`) — opt-in stack experiments; the core API must match the managed values. The mips TCP controller is separate from host BBR.
* `my.proxy.tunIpv6Address` (default `fdfe:dcba:9876::1/126`) — explicit TUN IPv6 address. With dual-stack TUN, the helper passes `SKIP_SYSTEM_IPV6_CHECK=1` to its core child, keeping the expected TUN structure independent of startup uplink IPv6 availability; IPv6 FIB/route checks remain strict.
* `my.proxy.guestInterfaces` and `transparentGuestInterfaces` — routed guest ingress list (VM/container bridges, plus the dedicated AP interface only when the hotspot is enabled) and its opt-in IPv4 TPROXY subset (default empty).
* `my.hardening.guestTcpPorts`/`guestUdpPorts` — the explicit desktop services libvirt guests may reach when `guestHostServices` is on; DHCP/DNS infrastructure is independent of these switches.
* `my.proxy.bootMode` (`direct` default, or `proxy`) — optional fail-closed boot intent installed with the pre-network baseline.
* `my.proxy.stateMaxAgeSeconds` (default 120) — freshness bound used by `proxy-control status`/`wait` and `proxy-status`.
* `my.proxy.splitDnsProfileUuids` (default empty) and `plaintextBootBudgetSeconds` (default null) — split-DNS profile exceptions and an additional boot-wide plaintext deadline. Approved profiles become dnsmasq `server=/domain/server@interface` entries and connection-bound interface/server leases (public upstreams unsupported); resolved itself stays on the managed resolver.
* `proxy-inspect` — read-only JSON of link/queue/offload/qdisc statistics, softirq counters, service CPU/RSS and current state; it sends no test traffic and changes no tuning.
* `my.hardening.allowPrivateEgress` (default true) — keep `privateV4`/ULA/link-local directly reachable in B; `false` subjects host private-address flows to the proxy policy too (affecting the tailnet and portals). `my.hardening.guestHostServices` (default false) enables explicitly configured VM→host service ports; both port lists default empty. Resolved has no wildcard DNS listener; guest gateway DNS remains separate.
* `my.browser.zen.dohOnly` (default false) — strict browser DoH-only; `false` keeps DoH preferred with the managed resolver as fallback.
* `my.machine` — interface names, ports (`mihomoDns`/`dnsmasq`/`unbound`/`gostHttp`/`gostRedirect`/`mihomoMixed`/`mihomoTproxy`/`dot`/`ntp`), uids, `mihomoMark`, and the local address ranges (`privateV4`, `multicastV4`, `ulaV6`, `linkLocalV6`, `multicastV6`, `limitedBroadcastV4`).
* The Clash core and its Merge template are configured in `home/config/cvr-merge.nix` (TUN stack, `dns-hijack`, `route-exclude-address`, `routing-mark`, fake-ip DNS + filter, sniffer, `respect-rules`).

## 8. File map

| File | Responsibility |
|---|---|
| `system/config/machine.nix` | interface/uid/port/mark/TUN/tunable single sources |
| `system/config/proxy-options.nix` | `my.proxy.tunMode`, `tunDev`, `fakeIpRange`, `fakeIpNet`, `routeTable`, `nodeProbeUrls` |
| `system/config/network.nix` | nftables ruleset, `proxyModeRules`, `blockedModeRules`, `redirect_guard`, `:53` redirect, env-proxy vars, `dnsmasq`/`unbound`/`resolved`, NM dispatcher, dhcpcd hook |
| `system/config/network-hardening.nix` | shared network sysctl baseline, interface redirect/source-route restrictions and disabled RDS/TIPC module loads |
| `system/programs/systemd/proxy-mode.nix` | mode state machine, predicates, scope loader, `proxy-control`, `request_wake`/`request_verify`, `proxy-net-watch`/`proxy-net-wake` |
| `system/programs/systemd/dns-upstream.nix` | `servers-file` reconciliation and hot reload, bounded plaintext windows, persisted verdict history |
| `system/programs/systemd/netsec/dnssec_probe.py` | two-stage resolver probe: fresh-name local verdict plus a direct TLS check of the encrypted upstreams |
| `system/programs/systemd/gost-relay.nix`, `netsec/patch_gost.py`, `netsec/gost_http*.go` | patched CONNECT parser and gost listener lifecycle (proxy / closed / passthrough) |
| `system/programs/systemd/nftables-verify.nix` | expected-JSON verification, result files, timer, opt-in repair |
| `system/programs/systemd/netsec/rules.py` | structured nft JSON comparison (order-sensitive) and CIDR containment; only bounded DNS lease elements are normalised |
| `system/programs/systemd/netsec/link.py` | physical uplink, raw DHCP resolvers and session identity |
| `system/programs/systemd/netsec/dns_policy.py` | kernel DNS lease broker and split-DNS discovery from an approved profile |
| `system/programs/systemd/netsec/resolved_policy.py` | TUN DNS isolation and authenticated conditional MagicDNS routing |
| `system/config/network-time.nix` | Chrony NTS-only time synchronization |
| `system/config/private-addresses.nix` | shared private-target ranges for Unbound and Mihomo |
| `system/programs/systemd/netsec/state.py` | shared readiness/freshness checks for `proxy-control` and `proxy-status` |
| `system/programs/systemd/netsec/alerts.py` | per-owner alert records plus the aggregate flag |
| `system/programs/systemd/netsec/managed_profile*.go`, `managed_route.go`, `patch_mihomo.py` | final-parser and controller-PATCH enforcement of the managed core profile (listeners, tunnels, DNS, TUN routing fields); patched into the pinned mihomo build |
| `system/programs/systemd/netsec/patch_sing_tun.py`, `patch_socks.py`, `socks_addr_test.go` | nftables interval endpoints, TUN-local OUTPUT DNAT, SOCKS address parsing and boundary tests |
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
* Mode B's dedicated DNS/NTP exceptions allow `unbound` (DoT, tcp/853) and `chronyd` (NTP udp/123 and NTS-KE tcp/4460); the core mark and root-UDP exception also apply. Ordinary application plaintext resolver queries are redirected into `dnsmasq`. Mode A adds the DHCP-provided resolvers, or `my.machine.dotTransportFallback` when the link supplies none, only within the bounded plaintext window (section 3.2).
* The kill switch exempts the core by mark rather than a blanket uid-0 allow. DHCP exceptions are pinned to uid 0/`dhcpcd`. Mode B permits **all root UDP** on uplinks to preserve Tailscale arbitrary-port peer/STUN hole punching; this also allows other root UDP. This is an accepted Mode B compatibility exception, not a root boundary: root can already set the core mark. Mode C cuts unmarked tailnet data.
* Mode C keeps the core alive, so mihomo's own `DIRECT` traffic still leaves; loopback stays open for local IPC, but the app-facing proxy ports (`7897`/`33332`/`33333`) are refused for non-root in both TCP and UDP, and a hop-limit-255 ICMPv6 NDP carve-out keeps link maintenance alive without opening a data path. Guest DHCP/DNS replies are allowed by bridge, gateway source and port (`67`/`53`), not by uid: a privileged-port daemon may send them after dropping privileges, and the accepted TCP sockets carry the dropped uid. The guard is a base chain evaluated before mihomo's redirect, so this holds for locally generated TCP too.
* Health is decided by `health_ok` through `netsec/health.py`: each target supplies its own HTTP/log/selected-leaf proof, and policy names are resolved exactly. The successful HTTP request proves the recorded leaf; supervision avoids the delay API, which updates node health/history and can affect automatic selection. DIRECT/REJECT is a routing failure, not a reason to try an unrelated healthy node. Targets share a 12 s deadline by default; the probe runs in a cancellable process group while supervision re-checks core/TUN/configuration. `config`/`route`/`tun` cut immediately; HTTP/log/selection failures use two consecutive `obs` rounds, with the next round scheduled at the 2 s minimum interval after the first failure. An event-cancelled probe does not consume an observation. Polling sleep is capped at 5 s; this is not the end-to-end protection/recovery delay. Layer durations are in `health.json`; actual delay depends on the fault and event source. The transparent TCP probe sends a real HTTP request to a fake-IP and requires its exact-source route log; it does not independently prove every UDP/IPv6 TUN device path. The guard allows root to fake-IP only on 53/80/443 without requiring a specific output interface; the shared route checks and exact-source core log prove the path. In the managed non-mark auto-redirect mode, the patched OUTPUT rule DNATs TUN-routed TCP to the TUN's own local address rather than loopback, preserving SO_BINDTODEVICE sockets with `route_localnet=0`. Root TCP replies to fake-IP are allowed only with conntrack reply/dnat and an original fake-IP destination on port 80/443; the guard runs after conntrack and before NAT. The loopback-source exception remains for the PREROUTING redirect path. B mode returns every fake-IP destination before the gost redirect for the same reason. If that redirect or reply path is missing, the probe times out and the host stays in C.
* TCP/UDP DNS and mixed listeners must match the expected address, UID and service cgroup; the health gate, coordinator, verifier and relay share passive identity checks. Gost requires exactly one owner per endpoint. Identity failure is a configuration-layer failure; a successful DNS answer alone is insufficient. The "Clash is on" decision requires the core, not just the GUI.
* The mihomo controller socket is mode 0777 without a secret, but its `/run/user/<uid>` ancestor is mode 0700: desktop same-uid processes can reconfigure the root core, rather than every local uid. Managed mode/mark/port/TUN invariants are checked and fingerprinted; rules, geodata and every DIRECT decision are outside that fingerprint. A profile reload can preserve all sampled gates while sending other traffic from the real address. The root core reads user-owned configuration and databases. The helper socket is restricted to `clash-verge-control`; same-uid GUI trust remains part of this desktop architecture. Supervisor/loader capabilities are narrowed, but the design does not contain a compromised root core.
* The managed-profile allow-list is checked at final profile parsing and the controller PATCH entry point. PATCH rejects duplicate keys and noncanonical schema field names; validation and application use the same decoded upstream schema. This is a small patch against the pinned mihomo source (`managed_profile.go`, `managed_route.go`, `patch_mihomo.py`). Anchor drift fails the package build instead of silently dropping the check; revalidate it on every mihomo bump. Likewise, `unbound` is a source override of nixpkgs' `unbound-with-systemd`: it must retain `--enable-systemd`, because the NixOS module uses `Type=notify` and a plain unbound build would hang its start job until the timeout.
* `systemctl stop nftables.service` does not remove the firewall: the unit has no teardown, so the rules stay loaded. To reinstall or reconcile them, use `sudo systemctl restart nftables.service` (or `reload`), which re-runs the scope loader — never `nft flush ruleset`, which also destroys mihomo's own tables and the TUN's routing, and only a TUN restart recreates them.
* Mode A needs the core stopped, not the window closed: in service mode the always-on `clash-verge.service` helper owns the core, so use `clash-off`/`clash-on`. Only these express intent — starting or stopping the core from the GUI does not, so after a `clash-off` (direct intent) a GUI-started core stays in Mode C until you `clash-on`, and after a `clash-on` a GUI-stopped core (or a crash) stays in Mode C until you `clash-off`. That is the fail-closed behaviour, not a bug.
* Recovery without a working network, in this order. On the running system (Ctrl+Alt+F3): `cat /var/lib/dns-upstream/history.log` and `/run/dns-upstream/state.json` name the layer that failed (`local` verdict versus `transport`), and `touch /run/dns-upstream/force-plaintext` keeps plaintext DNS while you read them. To leave a generation, reboot and pick an older entry — systemd-boot keeps eight, because it copies each generation's kernel/initrd onto the 1 GiB ESP. A generation that already fell out of that window can still be re-activated offline, as long as `nix.gc` kept its store path (`ls -l /nix/var/nix/profiles/system-*-link` shows the mapping): `nix-env -p /nix/var/nix/profiles/system --set /nix/store/<nixos-system-…>` creates a new generation from it, then `/nix/var/nix/profiles/system/bin/switch-to-configuration boot` writes the menu entry and makes it the default. If no generation boots at all, start a NixOS ISO, unlock the LUKS root, mount it and the ESP at `/mnt` and `/mnt/boot`, and run the same two commands inside `nixos-enter --root /mnt`.
* Forwarded guest/VM traffic carries no host uid and follows the pre-NAT/forward contract in section 4.2. It is not proxied by default; IPv4 TPROXY is an explicit subset option. Existing connections must pass the early guards after switching to C. Socket-based rootless transports need separate host-path validation.
* Documented trade-offs: the Mode A plaintext budget in section 3.2 is a maximum session deadline, not accumulated usage. The window is renewed while the budget lasts, so a flaky encrypted upstream keeps DNS working up to that deadline instead of blacking out on the first miss. DHCP parameter changes cannot refresh it; real reconnection starts a new session unless an optional boot-wide deadline has expired. `force-plaintext` is a continuous override while present. In Mode B private/CGNAT access (including `100.64.0.0/10`) is intentional with `allowPrivateEgress=true`; a private address is not a trust guarantee. Cold boot begins in A when no proxy intent/core has been seen; supervision cannot retroactively protect traffic sent before a proxy request.
* `allowPrivateEgress` and `guestHostServices` control private egress and guest desktop-service access. The former wildcard `externalDns` option is removed: LAN/tailnet DNS requires a separately designed listener, client ACL and answer route. The module rejects the legacy option rather than silently exposing a stub.
* TUN routing-rule priorities and internal marks are managed by the system. Profiles may omit these fields or use `0` to select the system defaults; any other explicit values must match the system configuration.
* When accessing IPv6 destinations through the local HTTP proxy, use the standard `[IPv6]:port` target format. The Gost relay also accepts bare IPv6 CONNECT targets with a valid port for client compatibility.
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

当前配置未导入 `system/programs/ssh.nix`，因此不部署 sshd 服务或端口 22 监听；启用 sshd 还需在 `system/config/network.nix` 明确放行。Nix daemon 信任权限仅授予 root；普通用户仍可正常构建并使用声明的签名缓存，管理配置使用 sudo。

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
* 模式 B 上游：`mihomo :1053`（fake-ip）。模式 A / blocked 上游：`unbound :1055`（DoT 到 360）。
* Unbound 过滤上游答案中的 LAN/CGNAT、访客、loopback、未指定、link-local、ULA 地址及对应的 IPv4-mapped IPv6 范围；显式 `local-data` 和窄范围 `private-domain` 仍可配置。防护覆盖模式 A 的 DoT 路径；批准的 split DNS 和有租约的门户明文后备直接经过 dnsmasq，不在此范围内。Mihomo 域名目标另有下述保护；浏览器独立 DoH 的答案不在 Unbound 的覆盖范围内。
* Mihomo DNS 设置由 `my.proxy.managedDns` 提供：禁用额外的 `direct-nameserver` 与 `proxy-server-nameserver-policy` 解析器，固定 fake-IP filter mode 和 IPv6 fake-IP range。启用 TUN 时，模板、最终解析器/PATCH 策略和健康门共同固定 `tun.dns-mode: hijack`。
* 静态 `lf_nat` 链把所有经上行口发出的 `:53`（v4 与 v6）重定向进 `dnsmasq`，因此门户/局域网/CGNAT 范围内硬编码的解析器不会明文出网。
* 明文后备是**内核租约**而非协调器状态：`dns-policy` 把 DHCP 解析器写入 `inet lf_filter dns_plaintext4`，超时不超过剩余窗口；静态 `output` base chain（`lf_dns_guard`，优先级 `-199` → `dns_egress`）丢弃 `dnsmasq` 的其它上游查询。因此停止、杀死或卡住 `dns-upstream` 都不能让明文许可活过租约。split DNS 租约按接口与服务器匹配，截止不超过原始观测后 120 秒，并绑定批准的 UUID、ActiveConnection、ifindex、carrier-down 计数和撤销代次；dispatcher 在 pre-up/pre-down/down 撤销旧授权，broker 授权前重验连接。

有线 `ens1` 继续由 dhcpcd 管理；NetworkManager 按名称与 MAC 排除它，并管理 Wi-Fi `wlo1`。`netsec/nm_dns.py` 在受管 Wi-Fi 的 pre-up/up/DHCP 事件协调当前连接 profile：IPv4/IPv6 都忽略自动下发的 DNS，清除不需要的显式服务器与搜索域，并给 resolved 指定受管解析器及 `~.` 路由域。`my.proxy.splitDnsProfileUuids` 可为企业 split DNS 保留指定 profile；该例外不改变 nftables 出站策略。明文后备读取 dhcpcd 原始租约或 NetworkManager `DHCP4.OPTION`，不读取已经生效的 `IP4.DNS` 列表。

Wi-Fi 扫描使用随机 MAC，连接默认使用 `stable-ssid`：由 SSID 与本机持久身份派生地址，同一 SSID 重连时保持稳定，不同 SSID 使用不同身份。`/var/lib/NetworkManager` 与 `/etc/machine-id` 已持久化，根文件系统回滚不会使该身份每次重建。同名 SSID 的网络会共享该身份；MAC 随机化不隐藏 IP 地址或登录账户。

`my.hardening.wifi.clonedMacAddress` 控制此默认策略，连接 profile 中显式设置的 `802-11-wireless.cloned-mac-address` 优先。需要硬件 MAC 的网络可执行 `nmcli connection modify uuid <UUID> 802-11-wireless.cloned-mac-address permanent`，也可填写固定 MAC；用 `nmcli connection modify uuid <UUID> 802-11-wireless.cloned-mac-address ""` 清除该属性即可重新继承默认值。设置在下次连接激活时应用；`preserve` 仅保留当前地址，不会恢复硬件 MAC。有线身份保持固定。

split DNS 仅支持已由 `allowPrivateEgress` 排除在 TUN 外的私网/ULA 上游，dnsmasq 将条件转发绑定到受管 Wi-Fi。批准 profile 无法读取或上游不受支持时进入 `split-error`，暂停转发并告警；旧连接快照只撤销、不续租。白名单默认为空。

内部上游端口 1053/1055 仅供 root 诊断和 dnsmasq UID 查询；1054 保留客户端访问及上行 DNS 重定向。静态 output guard 拒绝其它 UID 从这些保留端口发出的应答。协调器与健康门共用 `netsec/listeners.py` 验证 TCP/UDP 监听身份；本地解析器身份失败时以 `resolver-error` 暂停默认转发，不因此开放明文后备；已批准的条件转发仍受 split 租约约束。

dnsmasq 保留 root 启动、自行降权和 D-Bus 热加载流程，能力集合受限，除状态目录外系统文件只读。Unbound 的 loopback 高端口转发进程不保留 ambient/bounding capabilities；透明监听和特权端口不属于该服务的配置合同。访客 DHCP/DNS 仍由独立进程提供。

模式 A 可为门户或 bootstrap 恢复临时使用明文解析器，并在加密链路不健康时用同样有界的窗口维持 DNS 可用。每次窗口最多 120 秒，预算内可续期，且不能超过连接会话首次需要明文时起算的 1800 秒（30 分钟）截止；加密链路重新验证成功后该预算会被重置，之后的新故障不再受旧故障拖累。会话键由 boot ID、接口 ifindex、carrier-down 计数及 Wi-Fi 的 NetworkManager ActiveConnection 对象路径组成；DHCP 地址、网关、DNS 更新只改变参数指纹，不重发预算，服务重启也保留预算。`plaintextBootBudgetSeconds` 可再增加本次开机的总截止。`/run/dns-upstream/force-plaintext` 是文件存在期间持续生效的 override，删除或重启机器才取消；它表示运维明确决定在验证失败时仍保留明文 DNS，且仍然受 120 秒续期约束。会话身份、截止、剩余预算、override 状态与最近一次探测结论发布在 `/run/dns-upstream/state.json`，并追加到 `/var/lib/dns-upstream/history.log`。


模式 A 使用 360 DoT 的三个地址（`101.226.4.6`、`218.30.118.6`、`123.125.81.6`），校验证书名称 `dot.360.cn`。在本条线路上实测：所有境外 DoT（Cloudflare `1.1.1.1`、Quad9 `9.9.9.9`/`149.112.112.112`、Google `8.8.8.8`）在传输层全部超时，无法作为被验证上游；能应答的国内端点并不等价——AliDNS（`223.5.5.5`/`223.6.6.6`）与 `dot.pub` 会剥离 RRSIG，遇到签名区在 `harden-dnssec-stripped` 下会变成 SERVFAIL，而 360 对根查询与签名 `A` 查询都返回 RRSIG，是本地验证器需要的那种上游。Mihomo 的国内/bootstrap DNS 独立配置。Unbound 在 A 中通过 `validator iterator` 本地验证 DNSSEC，忽略客户端 CD 请求，并在 `/var/lib/unbound` 维护 RFC 5011 根信任锚。初始锚来自锁定的 `dns-root-data` 包，启动无需通过未认证网络下载。健康探测把两个阶段分开：先以 8 秒超时向 Unbound 查询根下的一个全新随机名，用其被验证的 NSEC 否定应答作为验证结论（要求 AD；EDE 5–12 是唯一签名错误）——全新名字不可能命中缓存，这一点与重复查询根 DNSKEY 不同；结论不明确时再直接对加密上游做 TLS 查询（每次 5 秒，遇到第一个可达即停止，慢上游不会被当成死上游），把问题归到网络而不是归到签名，并顺带报告上游是否真的返回了 RRSIG 材料，因为剥离签名的上游无法支撑本地验证器。冷启动解析器的前几轮结论会延后判定，且只在传输层有应答时才延后，避免把冷启动的根锚 priming 读成故障。结论为不可用或否定时不再导致 DNS 断网：dnsmasq 转入有界明文窗口（窗口内租约 120 秒，会话预算 1800 秒，预算内可续期，加密链路恢复验证后预算重置），使用 DHCP 下发的解析器；链路未提供时使用 `1.1.1.1`、`1.0.0.1`。该后备刻意不作为 Unbound 上游，并且始终与被验证的端点属于不同运营商。结论、rcode、EDE 码与故障类别发布在 `/run/dns-upstream/state.json`，并追加到 `/var/lib/dns-upstream/history.log`，因此即使启动失败后回退旧代，现场证据仍然保留。预算耗尽后只保留加密解析器，`force-plaintext` 仍是运维显式覆盖。探测验证解析器健康，并不表示所有被查询域名都有签名；split DNS、租约内明文后备与 B 的 Mihomo DNS 不因此获得本地 DNSSEC 验证。

完整配置解析只将精确匹配的旧受管 DNS 值转换为当前 root 策略；`lf-private-targets` 始终取 root 策略的值——旧代缓存下来的配置不能阻止核心启动，profile 中的值也永远无法削弱实际生效的防护。其他配置漂移，以及运行期 PATCH 对策略的削弱，仍会被拒绝。

受管 Mihomo 在路由/sniffing 后、TCP/UDP 出站前检查域名目标，复用 UDP 会话中的每个报文也会检查。它拒绝私网、loopback、link-local、未指定和组播答案，包括公私混合答案与 IPv4-mapped IPv6。运营商级 NAT 仍在该表内：Tailscale 对端走字面地址或已认证的 `*.ts.net` 后缀（两者都已豁免），因此没有合法拨号需要把域名解析进 `100.64.0.0/10`。重试一次后仍失败的解析不构成目标结论：连接继续并按自身路径解析，解析器抖动因此不会被误报为节点失效。DIRECT 和代理连接均固定到已检查的地址，防止远端再次解析。显式 IP 请求、核心内部基础设施和 `my.proxy.privateTargetDomains` 中明确的后缀（默认 `localhost`、`lan`、`local`、`home.arpa`）除外；当前已认证的 MagicDNS 后缀作为动态例外。公网域名目标因此在本地解析后再向代理拨号，会影响远端 DNS 行为，并遵循核心的 IPv4 优先选择。这是域名目标防护，不能作为通用 SSRF 边界，也不能约束不遵守请求目标的代理服务器。

Tailscale 声明 `accept-dns=false`、`accept-routes=false`，不宣告子网路由或 exit node 服务，不使用 exit node，并关闭 Tailscale SSH。`my.hardening.tailscale.magicDns` 默认 true：root helper 从运行中的守护进程读取当前 MagicDNS 后缀，仅将这个搜索/路由域交给 `tailscale0`，并设置 `DefaultRoute=no`。读取使用 5 秒期限；守护进程不可读或较慢不等于退出登录，此时保留上一次已认证路由，条件解析器因此不会来回抖动，例外文件也不会每分钟被删除多次。退出登录、切换 tailnet 或关闭 MagicDNS 才会撤销旧后缀，不接受接管公网 DNS 的 tailnet `~.` 路由。Mihomo 为 `ts.net` 返回真实 IP，并通过本地 Tailscale 解析器 DIRECT 条件解析，保证 GLOBAL profile 下仍能使用 Tailscale IP 路由；私网目标例外仍只限当前已认证的后缀。

时间同步使用 Chrony 与仅启用 NTS 的源（`my.machine.ntsUpstreams`，默认 Cloudflare、Netnod），设置 `authselectmode require` 并保持证书时间检查。B 的防火墙例外将 UDP/123、TCP/4460 限定到 chrony UID；C 继续拒绝非核心出站，包括时间同步。认证失败会保持未同步，不改用普通 NTP。持久化的 `/var/lib/chrony` 与 `chronyd -s` 为重启后的 RTC/drift-file 时间下限提供支持；模块的 `rtcfile`/`rtcautotrim` 让该下限保持准确，因此偏差过大以致 NTS 证书无法验证时可以靠 RTC 恢复，而不是与 DNS 互相等待。本地命令端口保持开启，`chronyc sources` 与 `chronyc authdata` 可区分 NTS-KE 失败与源不可用。若时钟仍然偏差到 TLS 无法验证，需要管理员校正时间，配置不跳过证书时间检查，也不提供明文时间后备。

## 4. 模式

`proxy-mode` 就是状态机。它依据实时状态决策（核心 cgroup、TUN、控制口、mihomo 自身日志）；记录下来的 status 只在变更/迟滞判定与展示时被读回：

| 判据 | 来源 | 含义 |
|---|---|---|
| `isClashOn` | `clash-verge.service` cgroup 内的 `verge-mihomo` | 内核在运行 |
| `intent` | `/run/proxy-mode/intent`（`proxy` \| `direct`），由 root 写入 | 最近一次显式模式请求；缺失表示本次开机自动判定 |
| `tun_ready` | `Mihomo` 设备 UP + FIB `my.proxy.routeTable` + 一条经它的路由（IPv4，及启用时的 IPv6）；root 与桌面 UID 的未打标 IPv6 实际选路 | 必需的内核选路观测通过 |
| `mode_direct` | 经控制口 `GET /configs` | mihomo 处于 `direct` 模式 |
| `health_ok` | `netsec/health.py` 核对受管 API 不变量（模式、`managed-policy` 摘要、mark、端口、LAN 暴露、TUN 参数），再按 `nodeProbeUrls` 逐个执行完整的 HTTP 200/204、精确源端口/主机日志与当前所选叶子节点核对，之后经 TUN/auto-redirect 向 fake-IP 发真实 HTTP 请求并校验精确源端口的路由日志，最后复核配置指纹 | 受管核心配置、**实际所选节点**与透明 TCP 数据路径在共享总截止内全部通过 |

| | **模式 A** | **模式 B** | **模式 C** |
|---|---|---|---|
| 进入条件 | `clash-off`（显式直连意图），或本次开机从未见过内核且没有代理意图 | 内核在、TUN 就绪、节点健康 | 内核在，且满足任一：TUN 未就绪；`direct` 模式；代理意图下整个 helper/core cgroup 消失；一次明确的 `config`/`route`/`tun` 健康失败，或连续两次 `obs` 失败（包括 HTTP 超时） |
| nftables | 片段为空 | 装载 `proxyModeRules` | 装载 `blockedModeRules` |
| 出站 | 直连，无断网保护 | 主机公网流量代理或丢弃，保留明确例外；访客公网默认拒绝，可显式启用 IPv4 TPROXY | 仅 loopback、核心 mark、root 对面向 Clash 的端口（`7897` mixed、`1053` DNS、`33332`/`33333` gost）与到 fake-ip 网段 53/80/443 的透明自测访问（及其 loopback/TUN DNAT 源应答）、上行口 DHCP，以及发往访客网桥的 DHCP/DNS 应答（网桥 + 网关源地址 + 67/53，刻意**不**按 uid 匹配）存活；非 root 对这些端口的流量（TCP 与 UDP）以及所有其它非 loopback/非核心流量都被丢弃。guard 是一条在 mihomo 重定向之前求值的 base chain，因此非 root 的 TCP 流在仍携带真实目的地址时就被切断 |
| DNS | `unbound` DoT；加密链路没有结论时回退到有界明文窗口 | `mihomo` fake-ip | `unbound`（其 DoT 出站已被切断，DNS 随之停止） |

模式由单次开机内的操作员意图驱动：`clash-on` 在 `/run/proxy-mode/intent` 记下代理意图，`clash-off` 记下直连意图；`proxy-mode` 首次见到内核时写 `/run/proxy-mode/core-seen`。只要意图为 `proxy`，或意图从未被显式清除且本次开机见过内核，内核缺失就保持模式 C——因此崩溃的内核、乃至**整个 helper/core cgroup 消失**都按故障处理，绝不会静默退回模式 A。只有显式 `clash-off`（直连意图）与无意图的全新开机才回到模式 A。`core-gone` 路径保留一轮宽限，避免在 `clash-off` 拆除过程中被误触。

### 4.1 两个片段

两者都由 `system/config/network.nix` 生成。scope loader（`proxy-mode --reload`）把静态 ruleset 与该模式的片段拼接、在**同一个 `nft -f` 事务**里提交，因此启动或 reload 都不会让片段链短暂为空；`rules_off` 则全清。

* `proxyModeRules` — `proxymode_drops`（核心 mark accept、固定到 uid 0/`dhcpcd` 的 DHCP 客户端、为 Tailscale 打洞保留的全部 root UDP，然后 drop）、`proxymode_tail`（对未固定接口的反向 default-deny）、`proxymode_forward`（访客拒绝），以及 `:33333` 的 REDIRECT 表。核心保留 mark，节点可达。
* `blockedModeRules` — 填充 `lf_blocked_guard`，它是 **`output` 钩子上、优先级 `-197` 的 base chain**，即在 conntrack（`-200`）之后、mihomo 自身的自动重定向（`nat/output` `-150`）与普通 output 放行规则之前求值。它放行 loopback、核心 mark、root 到 fake-ip 网段 53/80/443 的访问、loopback 源应答，以及 TUN 本机地址到 fake-IP 的 root TCP DNAT 应答（须有 conntrack reply/dnat，原目的为 fake-IP 的 80/443；路由日志仍须验证自测由核心承载）、固定到 uid 0/`dhcpcd` 的 DHCP 客户端（服务器应答仅限显式开启的 AP/子网；L2 本地），以及一项 hoplimit 255 的 ICMPv6 邻居发现例外（用于链路维护），最后一条终态 `drop`。**先于重定向**求值正是切断能生效的原因：一旦重定向跑过，本机非 root 的 TCP 流目的地址已被改写成 `127.0.0.1:<inbound>`，guard 的端口列表就匹配不到它了。guard 是无条件的，因此普通片段里的 LAN/DHCP/DNS/NTP accept 都活不过它；普通片段与重定向表被拆除，`proxymode_forward` 重新填入"拒绝公网目的地址"的规则给访客。

另有一条静态链 `redirect_guard`（`output` 钩子、优先级 `-201`，在 NAT 之前）：对显式连接 loopback 上 gost redirect 端口的流量回以 TCP RST；而真正被透明重定向的流在此刻仍携带真实目的地址，不受影响。

GUI 启动器保留 `clash-on`/`clash-off` 识别的公开进程名 `clash-verge`。启动当前包前会等待同一用户的旧 GUI 实例退出，避免旧单例继续保留先前的核心路径；当前包中已经运行的 GUI 会被保留。

模式 C 中核心**不被**阻断：它保留出站，以便恢复节点或从 GUI 经 loopback 重配。代价是 mihomo 自己判为 `DIRECT` 的流量（国内域名，或 profile 的 DIRECT 兜底）仍会离开。

### 4.2 启动基线、静态恢复与访客路径

`network-firewall-bootstrap.service` 在物理网络启动前装入 `inet lf_bootstrap`：入站与转发默认拒绝，放行 loopback、已建立的入站连接、DHCP 与必要 ICMPv6。NetworkManager 和 dhcpcd 都要求该单元成功。完整静态规则与模式片段提交成功时，才在同一事务中删除 bootstrap 表；因此首次加载失败仍保留入站保护，同时不改变正常冷启动进入模式 A 的合同。`my.proxy.bootMode = "proxy"` 可在同一早期事务里（仍早于物理网络）写入代理意图并直接装入模式 C；默认 `direct` 保持 A 的合同。

监督器分别比较静态基线与当前模式片段。静态漂移会在已有共享锁内调用完整 loader：直连会话装入 A，代理或恢复会话装入 C，再重新通过健康门进入 B。完整修复失败按 30 秒限频并发布 `unenforced`；后置校验失败不能继续标记为 `proxy`。这一状态表示无法确认保护已安装，状态发布本身不能代替缺失的内核规则。

`my.proxy.guestInterfaces` 定义全部转发访客入口，默认 `virbr0`、`waydroid0`；仅在显式开启热点时加入独立的 AP 接口 `my.machine.hotspot.interface`。作为客户端上行的 Wi-Fi 接口**不**再算作访客入口，因此发往宿主公网 IPv4/IPv6 地址的回复不会被前置 guard 误杀。B/C 的 `lf_guest_guard` 在 prerouting priority `-201`、透明重定向之前检查原始公网目标；`proxymode_forward` 还在 conntrack accept 前双向拒绝，不依赖出口接口。LAN/CGNAT 目标保留可达。libvirt/Waydroid 的 DHCP 与 DNS 服务在所有模式都由 input 放行，模式 C 的应答例外按网桥、网关源地址与服务端口识别，而不是按 uid（会降权的守护进程保留监听 socket 创建者的 uid，但 accept 出来的 TCP socket 携带降权后的 uid）。`transparentGuestInterfaces` 默认空：显式列出的接口仅在健康 B 安装 IPv4 TPROXY 动态规则，A/C 清空，C 的提前拦截覆盖 input 路径；访客 IPv6 公网仍被拒绝。passt/rootless 容器等转成宿主 socket 的流量遵循宿主策略，与桥接转发路径不同。

libvirt/Waydroid 访客 input 在通用 conntrack accept 前经过 `lf_guest_host`，默认仅允许对应网桥的 DHCP/DNS（以及显式启用的 TPROXY）；撤销服务端口也会切断已建立的访客主动连接。libvirt 桌面服务需同时设置 `guestHostServices=true` 和明确的 `guestTcpPorts`/`guestUdpPorts`。私网转发默认保留（`my.hardening.guestPrivateEgress=true`）；设为 false 可在 NAT 与 established-flow accept 之前限制这两个网桥，通过 `guestPrivateExceptions` 仅授权指定接口/CIDR/TCP 或 UDP/端口组合。网关基础设施与宿主主动连接的应答保留，原有 A/B/C 限制仍然适用；该选项不改变独立开启的热点策略。


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
| `/run/proxy-mode/reason` | 阻断原因：`no-tun` \| `direct-mode` \| `core-gone` \| `reload` \| `request`，或健康门失败层：`config`（受管 API 漂移）\| `obs`（HTTP/日志/选择证明不可用）\| `route`（DIRECT/REJECT）\| `tun`（TUN）；带 `obs` 迟滞时读作 `obs:1/2`（失败一次，已有 proxy 保持）或 `obs:2/2` \| 空 |
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

监督器与审计器共用 `netsec/tun.py`：在有界期限内读取设备、双栈规则/路由表，以及 root 和桌面 UID 的未打标 IPv6 实际选路。即使 TUN 表仍存在，只要选路偏向上行口，监督器也会通过既有决策/原子提交路径进入模式 C。受管 TUN 同时固定规则优先级 9000、内部 mark 8227/8228、fallback 优先级 32768 与 file-descriptor 0；最终解析、PATCH、模板和健康视图共用这些值。RawTun schema 覆盖测试要求为上游新增字段作出显式分类。

健康门按完整已知名称还原本次日志中的 `group[leaf]`，并检查该实际叶子仍与当前图一致。随后读取的 `now` 不能覆盖已记录的 DIRECT；LoadBalance 按连接叶子验证，不要求 `now`。受管核心在 global 模式也记录实际链。名称有歧义或选择发生变化时，本轮观测失效。

审计结果携带 CLOCK_BOOTTIME 时间戳与 boot ID，就绪判断同时检查时效、同启动与同策略代次。`my.proxy.verificationMaxAgeSeconds` 默认 420 秒，独立于消费者心跳的 120 秒上限，为审计的 300 秒周期、30 秒调度精度和有界 60 秒执行留出空间。缺失、未来或其它启动的校验结果不视为就绪。

## 7. 配置面

* `my.proxy.tunMode`（`system/config/proxy-options.nix`，默认 true）——是否要求 TUN。Merge 模板据此写 `enhanced-mode: fake-ip` 与 `tun.enable`；`proxy-mode` 与 `nftables-verify` 据此决定是否校验 TUN。`false` 选择纯 HTTP 代理模型（缺 TUN 不进入模式 C，DNS 用 redir-host）。
* `my.proxy.tunDev`（默认 `my.machine.tunDevice` = `Mihomo`）——防火墙、TUN 片段与校验器都读取。
* `my.proxy.routeTable`（默认 2022）——TUN 的 `iproute2-table-index`，Merge 模板、`proxy-mode` 与校验器共用。
* `my.proxy.fakeIpRange`（默认 `198.18.0.1/16`）——写入 `dns.fake-ip-range`；guard 的 `fakeIpNet` 由它派生（只读），校验器检查客户端答案落在完整 CIDR 内。
* `my.proxy.nodeProbeUrls`（默认 HTTPS gstatic 与 Cloudflare generate_204）——每个目标都可执行完整 HTTP/日志/所选叶子证明；域名目标加入 Merge script 的强制代理规则。`tunProbeName` 是 fake-IP TUN 自测域名。
* `my.proxy.delayTestUrl`（默认 `https://www.gstatic.com/generate_204`）——全局 profile 脚本为 url-test/fallback/load-balance 组和已开启的 provider health-check 补齐或替换 HTTP 测速 URL，保留已有 HTTPS 地址和关闭的检查。GUI 启动时也会为已有配置中未设置的测速地址补上该值，保留自定义地址及手动/组内选择的优先级；首次启动由 GUI 自行初始化配置。
* `my.proxy.healthBudgetSeconds`（默认 12，范围 8–30）——全部目标共享的一轮探针总截止。
* `my.proxy.healthRequestTimeoutSeconds`（默认 5，范围 1–10）——每次真实 HTTP 健康请求的上限，包含建连/TLS 阶段，并受共享总截止约束。
* `my.proxy.tunStack`（默认 `mixed`）、`tunMtu`（1500）与 `tunCongestionControl`（`cubic`，仅用于 `mips`）——显式栈实验入口；核心 API 必须与受管值一致，mips TCP 控制器与宿主 BBR 独立。
* `my.proxy.tunIpv6Address`（默认 `fdfe:dcba:9876::1/126`）——显式 TUN IPv6 地址。双栈 TUN 下 helper 向核心子进程传递 `SKIP_SYSTEM_IPV6_CHECK=1`，让预期结构不依赖启动瞬间的上行 IPv6 状态，IPv6 FIB/路由校验仍然严格。
* `my.proxy.guestInterfaces` 与 `transparentGuestInterfaces`——转发访客入口（VM/容器网桥，仅在开启热点时加入独立 AP 接口）及其显式启用的 IPv4 TPROXY 子集，后者默认空。
* `my.hardening.guestTcpPorts`/`guestUdpPorts`——`guestHostServices` 开启时 libvirt 访客可访问的显式桌面服务；DHCP/DNS 基础设施不受该开关影响。
* `my.proxy.bootMode`（默认 `direct`，可选 `proxy`）——可选的 fail-closed 开机意图，随物理网络前的基线一起安装。
* `my.proxy.stateMaxAgeSeconds`（默认 120）——`proxy-control status`/`wait` 与 `proxy-status` 使用的观测新鲜度上限。
* `my.proxy.splitDnsProfileUuids`（默认空）与 `plaintextBootBudgetSeconds`（默认 null）——split DNS profile 例外与额外的本次开机明文总截止。被批准的 profile 会生成 dnsmasq `server=/domain/server@interface` 与绑定连接的接口/服务器租约（不支持公网上游）；resolved 自身仍走受管解析器。
* `proxy-inspect`——只读输出接口/队列/offload/qdisc、softirq、服务 CPU/RSS 与当前状态 JSON，不发测试流量，不修改调优参数。
* `my.hardening.allowPrivateEgress`（默认 true）——在 B 中保留 `privateV4`/ULA/link-local 直达；`false` 会把宿主私网流量也置于代理策略约束下（影响 tailnet 与门户）。`my.hardening.guestHostServices`（默认 false）开启显式配置的 VM→宿主服务端口，两组端口列表默认为空。resolved 不提供通配 DNS 监听，访客网关 DNS 独立保留。
* `my.browser.zen.dohOnly`（默认 false）——浏览器严格 DoH-only；`false` 保持 DoH 优先、以受管解析器为后备。
* `my.machine`——接口名、端口（`mihomoDns`/`dnsmasq`/`unbound`/`gostHttp`/`gostRedirect`/`mihomoMixed`/`mihomoTproxy`/`dot`/`ntp`）、uid、`mihomoMark`，以及本地地址段（`privateV4`、`multicastV4`、`ulaV6`、`linkLocalV6`、`multicastV6`、`limitedBroadcastV4`）。
* Clash 内核与其 Merge 模板配置在 `home/config/cvr-merge.nix`（TUN 栈、`dns-hijack`、`route-exclude-address`、`routing-mark`、fake-ip DNS 与过滤器、sniffer、`respect-rules`）。

## 8. 文件映射

| 文件 | 职责 |
|---|---|
| `system/config/machine.nix` | 接口/uid/端口/mark/TUN/可调项的单点定义 |
| `system/config/proxy-options.nix` | `my.proxy.tunMode`、`tunDev`、`fakeIpRange`、`fakeIpNet`、`routeTable`、`nodeProbeUrls` |
| `system/config/network.nix` | nftables ruleset、`proxyModeRules`、`blockedModeRules`、`redirect_guard`、`:53` 重定向、env 代理变量、`dnsmasq`/`unbound`/`resolved`、NM dispatcher、dhcpcd hook |
| `system/config/network-hardening.nix` | 网络 sysctl 共用基线、接口 redirect/source-route 限制与 RDS/TIPC 模块加载限制 |
| `system/programs/systemd/proxy-mode.nix` | 模式状态机、判据、scope loader、`proxy-control`、`request_wake`/`request_verify`、`proxy-net-watch`/`proxy-net-wake` |
| `system/programs/systemd/dns-upstream.nix` | `servers-file` 协调与热更新、有界明文窗口、DNS 状态与 watchdog、持久化判定历史 |
| `system/programs/systemd/netsec/dnssec_probe.py` | 两阶段解析器探测：本地全新名字判定 + 直连加密上游的 TLS 检查 |
| `system/programs/systemd/netsec/dns_policy.py` | 内核 DNS 租约 broker，及从批准 profile 生成 split DNS |
| `system/programs/systemd/netsec/resolved_policy.py` | TUN DNS 隔离与认证后的 MagicDNS 条件路由 |
| `system/config/network-time.nix` | Chrony 仅 NTS 时间同步 |
| `system/config/private-addresses.nix` | Unbound 与 Mihomo 共用的私网目标范围 |
| `system/programs/systemd/netsec/state.py` | `proxy-control` 与 `proxy-status` 共用的就绪/新鲜度检查 |
| `system/programs/systemd/netsec/alerts.py` | 按 owner 的告警文件与聚合标记 |
| `system/programs/systemd/netsec/managed_profile*.go`、`managed_route.go`、`patch_mihomo.py` | 在最终解析器与控制口 PATCH 处强制受管核心配置（监听、隧道、DNS、TUN 路由字段），随固定版本 mihomo 构建打入 |
| `system/programs/systemd/netsec/patch_sing_tun.py`、`patch_socks.py`、`socks_addr_test.go` | nftables interval 端点、TUN 本机 OUTPUT DNAT、SOCKS 地址解析与边界测试 |
| `system/programs/systemd/gost-relay.nix`、`netsec/patch_gost.py`、`netsec/gost_http*.go` | CONNECT 解析补丁与 gost 监听生命周期（proxy / closed / 直通） |
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
* 模式 B 的专门 DNS/NTP 例外放行 `unbound`（DoT，tcp/853）与 `chronyd`（NTP udp/123 与 NTS-KE tcp/4460），核心 mark 和 root UDP 例外也适用；普通应用的明文解析器查询被重定向进 `dnsmasq`。模式 A 仅在有预算的明文窗口内加入 DHCP 解析器；链路未提供时使用 `my.machine.dotTransportFallback`（见 3.2）。
* 断网时的自救顺序：先在运行中的系统上按 Ctrl+Alt+F3，`cat /var/lib/dns-upstream/history.log` 与 `/run/dns-upstream/state.json` 会指出失败层（`local` 判定还是 `transport`），`touch /run/dns-upstream/force-plaintext` 可在读日志期间维持明文 DNS。要离开某一代就重启并在菜单里选旧条目——systemd-boot 保留 8 代，因为它把每代的 kernel/initrd 复制到 1 GiB 的 ESP 上。若想要的代已经滑出该窗口，只要 `nix.gc` 还留着它的 store 路径（`ls -l /nix/var/nix/profiles/system-*-link` 可查映射），就能离线救回：`nix-env -p /nix/var/nix/profiles/system --set /nix/store/<nixos-system-…>` 会基于它创建新的一代，随后 `/nix/var/nix/profiles/system/bin/switch-to-configuration boot` 写入菜单条目并设为默认。若一代都起不来，用 NixOS ISO 启动、解锁 LUKS 根分区、把它与 ESP 分别挂到 `/mnt` 和 `/mnt/boot`，在 `nixos-enter --root /mnt` 里执行同样两条命令。
* 断网保护按 mark 豁免核心，不对 uid 0 整体放行；DHCP 例外固定到 uid 0/`dhcpcd`。模式 B 在上行口放行**全部 root UDP**，保留 Tailscale 任意对端/STUN 端口的打洞，其它 root UDP 也会放行。这是模式 B 的兼容取舍，不构成 root 边界，root 本就可以设置核心 mark。模式 C 切断未打标的 tailnet 数据。
* 模式 C 保留核心存活，因此 mihomo 自身判为 `DIRECT` 的流量仍会离开；loopback 保留供本机 IPC 使用，但面向应用的代理端口（`7897`/`33332`/`33333`）对非 root 的 TCP 与 UDP 都被拒绝，另有一项 hoplimit 255 的 ICMPv6 邻居发现例外维持链路维护而不开放数据通道。访客 DHCP/DNS 应答按网桥、网关源地址与端口（`67`/`53`）放行，不按 uid：特权端口守护进程会降权，accept 出来的 TCP socket 携带降权后的 uid。guard 是在 mihomo 重定向之前求值的 base chain，因此对本机产生的 TCP 同样成立。
* 健康检查由 `health_ok` 调用 `netsec/health.py`：每个目标提供独立的 HTTP/日志/所选叶子证明，策略名精确解析，成功的 HTTP 请求直接证明日志记录的叶子节点；监督器不调用会更新节点存活状态/历史、可能影响自动选择的 delay API。DIRECT/REJECT 属于路由失败，不能换无关健康节点充数。全部目标默认共享 12 秒截止；探针运行在可取消的进程组中，监督器期间复查核心/TUN/配置。`config`/`route`/`tun` 失败立即切断；HTTP/日志/选择证明失败归为 `obs`，需连续两轮失败，首次失败后按 2 秒最短间隔安排下一轮，事件取消不消耗观测次数。轮询睡眠最多 5 秒，不代表端到端保护/恢复时延；分层耗时在 `health.json`，实际时延取决于故障及事件来源。透明 TCP 探针会向 fake-IP 发真实 HTTP 请求并校验精确源端口的路由日志；它不单独证明 TUN 设备的全部 UDP/IPv6 数据路径。guard 对 root 到 fake-IP 只放行 53/80/443，不要求特定出口接口，由共享选路检查和精确源端口的核心日志证明实际路径。在当前受管的非 mark auto-redirect 模式下，补丁将 TUN 路由 TCP 的 OUTPUT 目的 DNAT 到 TUN 本机地址，保留 SO_BINDTODEVICE 约束，`route_localnet` 仍为 0。TUN 地址发往 fake-IP 的 root TCP 应答须有 conntrack reply/dnat，且原目的为 fake-IP 的 80/443；guard 因此位于 conntrack 之后、NAT 之前。loopback 源例外保留供 PREROUTING redirect 路径使用。B 模式同样在 gost 重定向前放行所有 fake-IP 目的地址。若重定向或回包路径缺失，探针超时并保持 Mode C。
* TCP/UDP DNS 与 mixed 监听须符合预期地址、UID 和服务 cgroup；健康门、协调器、校验器和中继共用被动身份检查。gost 各端口也须恰好一个合法监听者。身份失败属于 config 层失败，不能只凭 DNS 能应答放行。“Clash 在运行”要求核心，而非仅 GUI。
* mihomo 控制 socket 为 mode 0777、无 secret，但祖先 `/run/user/<uid>` 是 0700：桌面同 UID 进程可重配 root 核心，并非所有本机 UID 都能访问。模式/mark/端口/TUN 不变量被检查并记录指纹，规则、geodata 与全部 DIRECT 决策不在指纹范围内；profile 重载仍可能保留全部抽样健康门，同时使其它流量以真实地址离开。root 核心读取用户所有的配置与数据库，helper socket 限定到 `clash-verge-control` 组；GUI 同 UID 信任仍属桌面架构取舍。监督器/loader 已收窄 capability，设计不隔离已失陷的 root 核心。
* 受管配置白名单在最终 profile 解析和控制口 PATCH 入口检查。PATCH 拒绝重复键和 schema 字段的非规范名称，校验与应用使用同一次解码的上游 schema。这是对固定版本 mihomo 源码的小补丁（`managed_profile.go`、`managed_route.go`、`patch_mihomo.py`）。上游锚点变化会让包构建失败，而不是悄悄丢掉检查；每次升级 mihomo 都要复核。`unbound` 则是对 nixpkgs `unbound-with-systemd` 的源码覆盖，必须保留 `--enable-systemd`，因为 NixOS 模块使用 `Type=notify`，换成普通 unbound 构建会让启动 job 挂到超时。
* `systemctl stop nftables.service` 不会移除防火墙：该 unit 没有拆除动作，规则保持装载。要重新装载或校正，用 `sudo systemctl restart nftables.service`（或 `reload`），它会重跑 scope loader —— 绝不要 `nft flush ruleset`，它还会毁掉 mihomo 自己的表与 TUN 路由，只有重启 TUN 才能重建。
* 模式 A 需要停掉核心，而非只关窗口：service mode 下常驻的 `clash-verge.service` helper 托管核心，因此用 `clash-off`/`clash-on`。只有这两条会表达意图——从 GUI 起停核心不会：所以 `clash-off`（直连意图）之后用 GUI 起核心仍停在模式 C，直到再 `clash-on`；`clash-on` 之后用 GUI 停核心（或崩溃）也停在模式 C，直到 `clash-off`。这是 fail-closed 行为，不是 bug。
* 转发访客/虚拟机流量不带宿主 uid，遵循 4.2 的 pre-NAT/forward 合同。默认不代理，IPv4 TPROXY 须显式指定子集；切入 C 后已有连接也须通过提前拦截。转成宿主 socket 的 rootless 传输须另按宿主路径验证。
* 已记录的取舍：3.2 中模式 A 的明文预算是会话最长截止，不是累计时长；预算内窗口可续期，因此加密上游抖动时 DNS 最多持续到该截止，而不是第一次失败就断网。DHCP 参数变化不刷新，实际重连建立新会话，但可选的开机总截止仍然有效。`force-plaintext` 存在期间持续显式覆盖预算。模式 B 在 `allowPrivateEgress=true` 时按设计直达私网/CGNAT（含 `100.64.0.0/10`），私网地址不代表可信。无代理意图且从未见核心的冷启动以 A 开始，监督器不能追溯保护提出代理请求之前已发出的流量。
* `allowPrivateEgress` 与 `guestHostServices` 分别控制私网出站和访客桌面服务权限。旧的通配 `externalDns` 选项已移除；LAN/tailnet DNS 需要独立设计监听、客户端 ACL 与答案路由，模块对旧选项明确报错，不再静默暴露 stub。
* TUN 路由规则优先级与内部 mark 由系统统一管理。profile 中可省略相应字段，或填写 `0` 使用系统默认值；显式指定其它值时须与系统配置一致。
* 通过本地 HTTP 代理访问 IPv6 目标时，使用标准的 `[IPv6]:port` 地址格式。Gost 中继也兼容部分客户端发送的带合法端口的裸 IPv6 CONNECT 地址。
* `gost` 只从 store 路径运行（`gost-relay` 用绝对路径）；不在系统 PATH 上，也不复制进 initrd。
