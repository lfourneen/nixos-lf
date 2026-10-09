# Proxy Architecture

---

## 1. Data path

Egress is a single chain. Three kinds of application traffic converge on the Clash core (mihomo, service mode, runs as root); nftables decides which kind may leave, and every locally generated flow it can see is proxied, redirected or dropped. The one thing the firewall cannot judge is the core's own routing: a `DIRECT` match in the profile leaves from the real address by design, and the health gate only samples it (section 9).

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
| `gost-relay` | `127.0.0.1:33332` HTTP, `:33333` redirect | 987 | relay in front of the core; forwards while Clash runs, passthrough when it does not |
| `dnsmasq` | `127.0.0.1:1054` | 985 | single client-facing resolver; upstream is switched per mode |
| `unbound` | `127.0.0.1:1055` | 983 | encrypted DoT resolver used while mihomo is not the trusted upstream |
| `proxy-mode` | — | root | decides the mode, loads the nftables fragment |
| `dns-upstream` | — | root | rewrites `dnsmasq`'s upstream |
| `nftables-verify` | — | root | reads kernel state and reports mismatches |

Ports, uids, the packet mark (`6666` → `0x00001a0a`) and the TUN device name are single-sourced in `system/config/machine.nix`; the ruleset, the services and the Clash template all read them from there.

## 3. How traffic flows

### 3.1 Host flows (Mode B)

1. **Env-proxy consumers** connect to `127.0.0.1:33332`; `gost-relay` forwards to `127.0.0.1:7897`. The session variables `http_proxy`/`https_proxy`/`all_proxy` (and their uppercase twins), GSettings (`home/config/dconf.nix`) and Flatpak overrides (`system/programs/flatpak.nix`) point here.
2. **Proxy-ignorant TCP** keeps its public destination; the `lf_proxymode_nat` output chains REDIRECT it to `127.0.0.1:33333`, which `gost-relay` also forwards to the core.
3. **Everything the core routes through the TUN** (including fake-ip answers) is read off the `Mihomo` device.
4. The core matches its profile. `DIRECT` and proxy-node sockets both leave on the uplink; the switch rule is `meta mark 0x00001a0a accept`, so only the core may egress.
5. Any other locally generated flow on an uplink to a non-local destination is dropped by the `proxymode_drops` chain — root included, which is why the core must carry the mark rather than rely on uid 0.

### 3.2 DNS

* `systemd-resolved` is NetworkManager's pinned resolver and forwards to `dnsmasq :1054`.
* `dns-upstream` writes `/run/dns-upstream/servers.conf`; `dnsmasq` restarts only when the upstream actually changes.
* Mode B upstream: `mihomo :1053` (fake-ip). Mode A / blocked upstream: `unbound :1055` (DoT to AliDNS).
* The static `lf_nat` chains redirect every `:53` leaving an uplink (v4 and v6) into `dnsmasq`, so a hardcoded resolver in a portal/LAN/CGNAT range cannot leave in the clear.

## 4. Modes

`proxy-mode` is the state machine. It reads live state only — never a status file it wrote itself:

| Predicate | Source | Meaning |
|---|---|---|
| `isClashOn` | `verge-mihomo` in `clash-verge.service`'s cgroup | the core is running |
| `isClashServiceOn` | `clash-verge-service` in the same cgroup | the unit is up (core may be absent) |
| `tun_ready` | `Mihomo` device up + FIB `table 2022` + a route via it | the TUN is actually capturing |
| `mode_direct` | `GET /configs` over the control socket | mihomo is in `direct` mode |
| `health_ok` | (a) HTTP 200/204 from `http://www.gstatic.com/generate_204` through `:7897` (plain HTTP, 0-byte body); (b) mihomo's log for it is neither `using DIRECT`/`using REJECT` nor a group member named `DIRECT`/`REJECT`, the effective group is resolved against `GET /proxies`, and Clash passes a `/proxies/<group>/delay` test on it (names URL-encoded; the probe takes its source port from a fixed range and its log line is matched on that exact port); (c) the fake-ip of a DIRECT name is answered by the core through the TUN (a DNS query to the fake-ip, hijacked by `dns-hijack`) | the core, the **selected** node of the effective group, and the TUN data path are all working |

| | **Mode A** | **Mode B** | **Mode C** |
|---|---|---|---|
| entry | core stops (`clash-off`), or a boot where the core was never seen | core on, TUN ready, node healthy | core on and any of: the TUN is not ready (device/FIB/route); `direct` mode; core vanished while the service stayed up; one `route`/`node`/`tun` health failure, or two consecutive `obs` failures (a single transient `obs` no longer cuts) |
| nftables state | fragments empty | `proxyModeRules` loaded | `blockedModeRules` loaded |
| egress | direct, no kill switch | every host flow proxied or dropped; guests refused | only loopback, the core's mark, root's access to the Clash-facing ports (`7897` mixed, `1053` DNS, `33332`/`33333` gost) and to the fake-ip network (the TUN self-test), and DHCP on the uplinks survive; non-root traffic to those ports (TCP and UDP) and every non-loopback/non-core flow are dropped. The guard is a base chain evaluated before mihomo's redirect, so a non-root TCP flow is cut while it still carries its real destination |
| DNS | `unbound` DoT | `mihomo` fake-ip | `unbound` (its own DoT egress is cut, so DNS stops) |

The core stopped/missing distinction is boot-scoped: `proxy-mode` writes `/run/proxy-mode/core-seen` when it sees the core, and blocks only if that marker exists **and** the service is still up (a crash). `clash-off` stops the unit and a fresh boot has no marker, so both stay Mode A. The `core-gone` path has a one-pass grace so the teardown in `clash-off` is not caught mid-flight.

### 4.1 The two fragments

Both are emitted by `system/config/network.nix` and applied atomically by `proxy-mode`; each flushes what the other loads, and `rules_off` flushes both.

* `proxyModeRules` — `proxymode_drops` (core-mark accept, DHCP, tailnet, then drop), `proxymode_tail` (reverse default-deny for interfaces not pinned), `proxymode_forward` (guest refusal), and the `:33333` REDIRECT tables. The core keeps its mark and its nodes stay reachable.
* `blockedModeRules` — fills `lf_blocked_guard`, a **base chain on the `output` hook at priority `-200`**, i.e. evaluated before mihomo's own auto-redirect (`nat/output` `-150`) and before every static accept. It accepts loopback, the core's mark, root's access to the fake-ip network (so the TUN self-test runs while blocked) and DHCP on the uplinks (both directions; L2-local), then a terminal `drop`. Running *before* the redirect is what makes the cut real: once the redirect has run, a locally generated non-root TCP flow already carries `127.0.0.1:<inbound>` as its destination and the guard's port list would not match it. The guard is unconditional, so the normal fragment's LAN/DHCP/DNS/NTP accepts do not survive it; the normal fragment and the redirect tables are torn down, and `proxymode_forward` is refilled with the public-destination refusal for guests.

The core is **not** blocked in Mode C: it keeps egress so it can recover a node or be reconfigured from the GUI over loopback. The cost is that traffic mihomo itself routes `DIRECT` (domestic names, or a profile DIRECT fallback) still leaves.

## 5. Coordination and lifecycle

Supervisors are event-driven, with an adaptive backstop (2 s while transitioning, capped at 5 s in an enforced mode / 60 s in direct).

* `proxy-net-watch.path` watches the mihomo control socket and its directory (`/run/user/<desktopUser>/clash-verge-rev`). A core start/stop fires `proxy-net-wake.service`.
* `proxy-net-wake.service` sends `SIGWINCH` to `proxy-mode`, `dns-upstream` and `gost-relay`; each loop's `wait` is interrupted and it re-decides immediately.
* A NetworkManager dispatcher script (`system/config/network.nix`) starts the coordinator on a link change; `nftables.service`'s `ExecStartPost` first runs one `proxy-mode --once` (so the fragment is re-applied before the unit reports started, instead of relying on the asynchronous wake) and then wakes the coordinator.
* `proxy-tun-watch.service` runs `ip -o monitor link` and wakes the coordinator on any link event naming the TUN device: turning the TUN off/on in the GUI is a second-level event, not poll-bound. (sysfs emits no inotify, so a `systemd.path` cannot see it; rtnetlink is the reliable source.)
* On a mode change, `proxy-mode` also signals `gost-relay` and `dns-upstream` directly (`request_wake`) so they do not back off through a transition, and starts `nftables-verify` (`request_verify`).
* `proxy-mode` runs `--once` as `ExecStartPre`, so a recorded mode is never stale.

State files (all under `/run`):

| Path | Content |
|---|---|
| `/run/proxy-mode/status` | `proxy` \| `blocked` \| `direct` \| `unenforced` |
| `/run/proxy-mode/reason` | block reason: `no-tun` \| `direct-mode` \| `core-gone` \| `reload`, or the health layer that failed: `obs` (route unobservable) \| `route` (DIRECT/REJECT) \| `node` (selected node) \| `tun` (TUN); with the `obs` hysteresis it reads `obs:1/2` (one failure, still proxy) or `obs:2/2` \| empty |
| `/run/proxy-mode/core-seen` | boot-scoped marker (crash detection) |
| `/run/gost-relay/status`, `/run/dns-upstream/status`, `/run/dns-upstream/reason` | relay mode, resolver mode, why |

`gost-relay` maps the proxy-mode state directly: `proxy` → forward to `:7897`, `blocked` → no listener (refuse), `direct` → passthrough; every other value, including `unenforced` and an unreadable or absent file, → no listener. It reaps the previous child before each start and requires exactly one listener per port (an orphan sharing the ports through `SO_REUSEPORT` would otherwise serve a share of the traffic). `nushell`'s `proxy-status` prints these plus a live probe.

## 6. Verification, alerting, repair

`nftables-verify` compares live kernel state with what the generation writes:

* static skeleton: all `lf_filter` chains exist (including `lf_blocked_guard`), `input` policy is `drop`, `forward` jumps to `proxymode_forward`, `lf_blocked_guard` is an `output`-hook base chain at priority `-200` and `chain output` no longer jumps to it, `dnsmasq`/`unbound` listen, the `:53` redirect exists, `ip_forward=1`, the unbound uid matches and its DoT exemption is present;
* per mode: `proxy` — the fragment text, the core mark, the TUN/FIB/table-2022 shape, gost and mihomo listeners, and the fake-ip answer at `:1054`; `blocked` — the guard text matches this generation's blocked fragment (so a missing IPv6 cut, a changed port set or a lost DHCP accept is caught), the redirect tables are gone, `proxymode_drops` is empty and `proxymode_forward` is not; `direct` — no fragments and no TUN.

It never mutates state. A mismatch raises `netsec-alert@` (journal + `/run/netsec/failed` + `wall`); `nftables-verify-repair.service` is the opt-in repair, and `nftables-recover` retries a failed `nftables.service` once a minute.

## 7. Configuration surface

* `my.proxy.tunMode` (`system/config/proxy-options.nix`, default true) — whether a TUN is required. The Merge template writes `enhanced-mode: fake-ip` and `tun.enable` from it; `proxy-mode` and `nftables-verify` gate the TUN checks on it. `false` selects the plain HTTP-proxy model (no Mode C for a missing TUN, redir-host DNS).
* `my.proxy.tunDev` (default `my.machine.tunDevice` = `Mihomo`) — read by the firewall, the TUN fragment and the verifier.
* `my.proxy.fakeIpRange` (default `198.18.0.1/16`) — written into `dns.fake-ip-range` and asserted by the verifier.
* `my.machine` — interface names, ports (`mihomoDns`/`dnsmasq`/`unbound`/`gostHttp`/`gostRedirect`/`mihomoMixed`/`mihomoTproxy`/`dot`/`ntp`), uids, `mihomoMark`, and the local address ranges (`privateV4`, `multicastV4`, `ulaV6`, `linkLocalV6`, `multicastV6`, `limitedBroadcastV4`).
* The Clash core and its Merge template are configured in `home/config/cvr-merge.nix` (TUN stack, `dns-hijack`, `route-exclude-address`, `routing-mark`, fake-ip DNS + filter, sniffer, `respect-rules`).

## 8. File map

| File | Responsibility |
|---|---|
| `system/config/machine.nix` | interface/uid/port/mark/TUN/tunable single sources |
| `system/config/proxy-options.nix` | `my.proxy.tunMode`, `tunDev`, `fakeIpRange` |
| `system/config/network.nix` | nftables ruleset, `proxyModeRules`, `blockedModeRules`, `:53` redirect, env-proxy vars, `dnsmasq`/`unbound`/`resolved`, NM dispatcher |
| `system/programs/systemd/proxy-mode.nix` | mode state machine, predicates, fragment loading, `request_wake`/`request_verify`, `proxy-net-watch`/`proxy-net-wake` |
| `system/programs/systemd/dns-upstream.nix` | `dnsmasq` upstream switching, bounded plaintext windows |
| `system/programs/systemd/gost-relay.nix` | gost listener lifecycle (proxy / closed / passthrough) |
| `system/programs/systemd/nftables-verify.nix` | kernel-state verification, timer, opt-in repair |
| `system/programs/systemd/netsec-alert.nix` | `netsec-alert@` alert channel |
| `system/programs/systemd/nftables-recover.nix` | retry a failed `nftables.service` |
| `system/programs/clash-verge.nix` | service-mode core and the sing-tun `route-exclude` patch |
| `home/config/cvr-merge.nix` | Clash Merge template (TUN, fake-ip DNS, sniffer, `routing-mark`) |
| `home/config/dconf.nix` | GSettings system proxy → gost |
| `system/programs/flatpak.nix` | Flatpak proxy environment overrides |
| `home/config/nushell.nix` | `proxy-status`, `clash-on`/`clash-off`, `egress-audit` |

## 9. Limitations and trade-offs

* The `:33333` redirect is the only path for TCP that the TUN does not carry (source-/device-bound sockets, and anything after a manual flush); `gost` is a single point of failure for those, and flows reaching it are re-sourced to `127.0.0.1`, so per-source profile rules do not apply. A direct `connect()` to `:33333` makes gost re-enter its own listener and spawn sockets until the fd limit; the supervisor detects the self-connected sockets and restarts, so nothing should connect to `:33333` to test it.
* In Mode B only `unbound` (DoT, tcp/853) and `systemd-timesyncd` (udp/123) may reach the network directly for DNS/NTP; other plaintext resolver queries are dropped unless redirected into `dnsmasq` first. In Mode A the DHCP-provided resolvers are added inside the plaintext window (last bullet).
* The kill switch exempts the core by its mark, not by uid: unmarked root traffic is dropped or redirected like anything else. DHCP has explicit exceptions in both fragments; the tailnet rule is a destination carve-out scoped to uid 0 (tailscaled), so it is not a general UDP/41641 channel for other processes.
* Mode C keeps the core alive, so mihomo's own `DIRECT` traffic still leaves; loopback stays open for local IPC, but the app-facing proxy ports (`7897`/`33332`/`33333`) are refused for non-root in both TCP and UDP. The guard is a base chain evaluated before mihomo's redirect, so this holds for locally generated TCP too.
* Health is decided by `health_ok`: a cheap `http://` request through the mixed port, a Clash `/proxies/<group>/delay` on the group that request used (Clash-centric — it tests the **selected** node of the effective group, not "any subscription node that is not timing out"; the group is resolved against `GET /proxies`), and a TUN self-test (a DNS query to the fake-ip, answered by the core through the TUN — no third-party site whose port policy could fail a healthy host). TUN off/on is event-driven via rtnetlink. The "5 s" figure is a cap on the *sleep*, not on the decision period: a pass plus the interval is ~5.8 s in the steady state. `route`/`node`/`tun` cut immediately; the `obs` layer requires two consecutive failures. Group and node names are URL-encoded before the delay call, so whitespace or `/` no longer break it, and a group whose endpoint gives no answer falls back to the member's own delay test; the policy text is parsed exactly, so a group named e.g. `DIRECT-中转` is not misread as a DIRECT route.
* The DNS listener (`:1053`) must belong to `clash-verge.service` by cgroup (`nftables-verify`), and `gost-relay` makes the same cgroup check for the mixed port (`:7897`), so a local process cannot attract traffic by binding either; the "Clash is on" decision requires the core, not just the GUI.
* The mihomo external controller is a world-writable socket with no secret — any login-user process can reconfigure the core. A mode or port flag is bounded (the next pass observes it and loads Mode C), but a full config reload or a rule-set/geodata replacement is not: it can keep all gate layers green while sending everything else out from the real address. The root core also reads its config and databases from the user's home. Accepted for a single-user desktop; the Merge template cannot override it.
* `systemctl stop nftables.service` does not remove the firewall (teardown is the same `nft -f` transaction, deletions file empty). To re-apply it, use `sudo systemctl restart nftables.service` — never `nft flush ruleset`, which also destroys mihomo's own tables and the TUN's routing, and only a TUN restart recreates them.
* Mode A needs the core stopped, not the window closed: in service mode the always-on `clash-verge.service` helper owns the core, so use `clash-off`/`clash-on`.
* Forwarded guest/VM traffic never traverses `output` and carries no uid; it is refused public destinations in `proxymode_forward` and is never proxied.
* Documented trade-offs: Mode A keeps a logged plaintext DHCP-resolver window — 120 s per open from a link change, bounded overall by a 300 s per-uplink budget, so even a sustained `portal`/`limited` verdict cannot keep cleartext DNS open indefinitely (once spent, DNS fails closed until the link changes or an operator touches `force-plaintext`); in Mode B `100.64.0.0/10` and the tailnet stay directly reachable through the early `local4` accept; the A→B window is irreducible but bounded by the supervisors' backstop.
* `gost` runs from its store path only (`gost-relay` uses absolute paths); it is not on the system PATH and is not copied into the initrd.

---

## 1. 数据路径

出站只有一条链路。三类应用流量最终汇聚到 Clash 内核（mihomo，service mode，以 root 运行）；由 nftables 决定哪一类可以离开，凡是它能看到的本机出站流，要么被代理、要么被重定向、要么被丢弃。防火墙唯一无法判断的是内核自身的路由：profile 里命中 `DIRECT` 的流量会按设计以真实地址离开，健康门只能对它抽样（见第 9 节）。

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
| `gost-relay` | `127.0.0.1:33332` HTTP、`:33333` redirect | 987 | 内核前置中转；Clash 运行时转发，未运行时直通 |
| `dnsmasq` | `127.0.0.1:1054` | 985 | 面向客户端的唯一解析器；上游随模式切换 |
| `unbound` | `127.0.0.1:1055` | 983 | mihomo 不作为可信上游时使用的加密 DoT 解析器 |
| `proxy-mode` | — | root | 决定模式、装载 nftables 片段 |
| `dns-upstream` | — | root | 改写 `dnsmasq` 的上游 |
| `nftables-verify` | — | root | 读取内核状态并报告不一致 |

端口、uid、packet mark（`6666` → `0x00001a0a`）与 TUN 设备名都在 `system/config/machine.nix` 单点定义；ruleset、各服务与 Clash 模板都从这里读取。

## 3. 流量如何工作

### 3.1 主机流量（模式 B）

1. **认环境变量的消费者**连接 `127.0.0.1:33332`，`gost-relay` 转发到 `127.0.0.1:7897`。会话变量 `http_proxy`/`https_proxy`/`all_proxy`（及其大写版本）、GSettings（`home/config/dconf.nix`）与 Flatpak 覆盖（`system/programs/flatpak.nix`）都指向这里。
2. **忽略代理的 TCP** 保留公网目的地址，由 `lf_proxymode_nat` 的 output 链 REDIRECT 到 `127.0.0.1:33333`，`gost-relay` 同样转发到内核。
3. **内核经 TUN 路由的一切**（包括 fake-ip 地址）从 `Mihomo` 设备读出。
4. 内核套用 profile。`DIRECT` 与代理节点 socket 都经上行口离开；放行判据是 `meta mark 0x00001a0a accept`，即只有内核可以出站。
5. 其它任何在本机产生、经上行口发往非本地目的地址的流量，由 `proxymode_drops` 链丢弃——root 也不例外，这正是内核必须打 mark 而不能靠 uid 0 的原因。

### 3.2 DNS

* `systemd-resolved` 是 NetworkManager 锁定的解析器，转发给 `dnsmasq :1054`。
* `dns-upstream` 写入 `/run/dns-upstream/servers.conf`；只有上游真正变化时 `dnsmasq` 才重启。
* 模式 B 上游：`mihomo :1053`（fake-ip）。模式 A / blocked 上游：`unbound :1055`（DoT 到 AliDNS）。
* 静态 `lf_nat` 链把所有经上行口发出的 `:53`（v4 与 v6）重定向进 `dnsmasq`，因此门户/局域网/CGNAT 范围内硬编码的解析器不会明文出网。

## 4. 模式

`proxy-mode` 就是状态机。它只读实时状态，从不相信自己写下的状态文件：

| 判据 | 来源 | 含义 |
|---|---|---|
| `isClashOn` | `clash-verge.service` cgroup 内的 `verge-mihomo` | 内核在运行 |
| `isClashServiceOn` | 同一 cgroup 内的 `clash-verge-service` | 单元在运行（内核可能不在） |
| `tun_ready` | `Mihomo` 设备 UP + FIB `table 2022` + 一条经它的路由 | TUN 确实在抓流量 |
| `mode_direct` | 经控制口 `GET /configs` | mihomo 处于 `direct` 模式 |
| `health_ok` | (a) 经 `:7897` 请求 `http://www.gstatic.com/generate_204` 得 200/204（纯 HTTP、0 字节）；(b) mihomo 该连接日志既不是 `using DIRECT`/`using REJECT`，成员也不是 `DIRECT`/`REJECT`，实际生效的组对 `GET /proxies` 解析后，Clash 对其 `/proxies/<组>/delay` 通过（名字做 URL 编码；探测的源端口取自固定端口段，日志行按该端口精确匹配）；(c) 一个 DIRECT 域名的 fake-ip 由核心经 TUN 应答（对该 fake-ip 发 DNS 查询，被 `dns-hijack` 接管） | 核心、实际生效组的**所选节点**、TUN 数据路径三者都正常 |

| | **模式 A** | **模式 B** | **模式 C** |
|---|---|---|---|
| 进入条件 | 内核停止（`clash-off`），或本次开机从未见过内核 | 内核在、TUN 就绪、节点健康 | 内核在，且满足任一：TUN 未就绪；`direct` 模式；核心在 service 仍在时消失；一次 `route`/`node`/`tun` 健康失败，或连续两次 `obs` 失败（单次瞬时 `obs` 不再切断） |
| nftables | 片段为空 | 装载 `proxyModeRules` | 装载 `blockedModeRules` |
| 出站 | 直连，无断网保护 | 每条主机流要么被代理要么被丢弃；访客被拒 | 仅 loopback、核心 mark、root 对面向 Clash 的端口（`7897` mixed、`1053` DNS、`33332`/`33333` gost）与 fake-ip 网段（TUN 自测）的访问，以及上行口的 DHCP 存活；非 root 对这些端口的流量（TCP 与 UDP）以及所有非 loopback/非核心流量都被丢弃。guard 是一条在 mihomo 重定向之前求值的 base chain，因此非 root 的 TCP 流在仍携带真实目的地址时就被切断 |
| DNS | `unbound` DoT | `mihomo` fake-ip | `unbound`（其 DoT 出站已被切断，DNS 随之停止） |

"内核停止/缺失"的区分按单次开机生效：`proxy-mode` 见到内核时写 `/run/proxy-mode/core-seen`，只有当该标记存在**且** service 仍在时才判为崩溃并阻断。`clash-off` 会停掉单元、刚开机则没有标记，两者都留在模式 A。`core-gone` 路径有一轮宽限，避免在 `clash-off` 拆除过程中被误触。

### 4.1 两个片段

两者都由 `system/config/network.nix` 生成、由 `proxy-mode` 原子装载；每个都会清掉对方装载的内容，`rules_off` 则全清。

* `proxyModeRules` — `proxymode_drops`（核心 mark accept、DHCP、tailnet，然后 drop）、`proxymode_tail`（对未固定接口的反向 default-deny）、`proxymode_forward`（访客拒绝），以及 `:33333` 的 REDIRECT 表。核心保留 mark，节点可达。
* `blockedModeRules` — 填充 `lf_blocked_guard`，它是 **`output` 钩子上、优先级 `-200` 的 base chain**，即在 mihomo 自身的自动重定向（`nat/output` `-150`）与所有静态 accept 之前求值。它放行 loopback、核心 mark、root 对 fake-ip 网段的访问（让 TUN 自测在 blocked 时也能跑）以及上行口的 DHCP（双向；L2 本地），最后一条终态 `drop`。**先于重定向**求值正是切断能生效的原因：一旦重定向跑过，本机非 root 的 TCP 流目的地址已被改写成 `127.0.0.1:<inbound>`，guard 的端口列表就匹配不到它了。guard 是无条件的，因此普通片段里的 LAN/DHCP/DNS/NTP accept 都活不过它；普通片段与重定向表被拆除，`proxymode_forward` 重新填入"拒绝公网目的地址"的规则给访客。

模式 C 中核心**不被**阻断：它保留出站，以便恢复节点或从 GUI 经 loopback 重配。代价是 mihomo 自己判为 `DIRECT` 的流量（国内域名，或 profile 的 DIRECT 兜底）仍会离开。

## 5. 协调与生命周期

各守护脚本事件驱动，并带自适应兜底（切换中 2 秒，enforced 模式封顶 5 秒 / direct 封顶 60 秒）。

* `proxy-net-watch.path` 监听 mihomo 控制 socket 及其目录（`/run/user/<desktopUser>/clash-verge-rev`）。内核起停触发 `proxy-net-wake.service`。
* `proxy-net-wake.service` 向 `proxy-mode`、`dns-upstream`、`gost-relay` 发 `SIGWINCH`；各循环的 `wait` 被打断并立即重新决策。
* NetworkManager dispatcher 脚本（`system/config/network.nix`）在链路变化时启动协调器；`nftables.service` 的 `ExecStartPost` 先跑一次 `proxy-mode --once`（在单元报告已启动前就把片段重新装好，而不是依赖异步唤醒），再唤醒协调器。
* 模式变化时，`proxy-mode` 还会直接唤醒 `gost-relay` 与 `dns-upstream`（`request_wake`），使其不会在切换期间继续 backoff；并启动 `nftables-verify`（`request_verify`）。
* `proxy-mode` 以 `ExecStartPre` 跑一次 `--once`，保证记录的模式不会过时。

状态文件（均在 `/run` 下）：

| 路径 | 内容 |
|---|---|
| `/run/proxy-mode/status` | `proxy` \| `blocked` \| `direct` \| `unenforced` |
| `/run/proxy-mode/reason` | 阻断原因：`no-tun` \| `direct-mode` \| `core-gone` \| `reload`，或健康门失败层：`obs`（路由不可观测）\| `route`（DIRECT/REJECT）\| `node`（所选节点）\| `tun`（TUN）；带 `obs` 迟滞时读作 `obs:1/2`（失败一次，仍在 proxy）或 `obs:2/2` \| 空 |
| `/run/proxy-mode/core-seen` | 单次开机内的崩溃检测标记 |
| `/run/gost-relay/status`、`/run/dns-upstream/status`、`/run/dns-upstream/reason` | 中继模式、解析器模式、原因 |

`gost-relay` 直接映射 proxy-mode 状态：`proxy` → 转发到 `:7897`，`blocked` → 无监听（拒绝），`direct` → 直通；其余一切取值（含 `unenforced`、以及不可读或缺失的状态文件）→ 无监听。它在每次启动前先回收上一个子进程，并要求每个端口恰好一个监听者（否则通过 `SO_REUSEPORT` 共享端口的孤儿进程会分走一部分流量）。`nushell` 的 `proxy-status` 会打印这些状态外加一次实时探测。

## 6. 校验、告警、修复

`nftables-verify` 把实时内核状态与本代所写内容比对：

* 静态骨架：所有 `lf_filter` 链存在（含 `lf_blocked_guard`）、`input` 策略为 `drop`、`forward` 跳 `proxymode_forward`、`lf_blocked_guard` 是 `output` 钩子上优先级 `-200` 的 base chain 且 `chain output` 不再跳它、`dnsmasq`/`unbound` 在监听、`:53` 重定向存在、`ip_forward=1`、unbound uid 匹配且其 DoT 豁免存在；
* 按模式：`proxy` —— 片段文本、核心 mark、TUN/FIB/table-2022 形态、gost 与 mihomo 监听、以及 `:1054` 的 fake-ip 应答；`blocked` —— guard 文本与本代 blocked 片段完全一致（因此缺 IPv6 切断、端口集变化或丢 DHCP accept 都会被捕获）、重定向表已消失、`proxymode_drops` 为空而 `proxymode_forward` 非空；`direct` —— 无片段、无 TUN。

它从不改动状态。不一致会触发 `netsec-alert@`（journal + `/run/netsec/failed` + `wall`）；`nftables-verify-repair.service` 是可选修复入口，`nftables-recover` 每分钟重试一次失败的 `nftables.service`。

## 7. 配置面

* `my.proxy.tunMode`（`system/config/proxy-options.nix`，默认 true）——是否要求 TUN。Merge 模板据此写 `enhanced-mode: fake-ip` 与 `tun.enable`；`proxy-mode` 与 `nftables-verify` 据此决定是否校验 TUN。`false` 选择纯 HTTP 代理模型（缺 TUN 不进入模式 C，DNS 用 redir-host）。
* `my.proxy.tunDev`（默认 `my.machine.tunDevice` = `Mihomo`）——防火墙、TUN 片段与校验器都读取。
* `my.proxy.fakeIpRange`（默认 `198.18.0.1/16`）——写入 `dns.fake-ip-range` 并由校验器断言。
* `my.machine`——接口名、端口（`mihomoDns`/`dnsmasq`/`unbound`/`gostHttp`/`gostRedirect`/`mihomoMixed`/`mihomoTproxy`/`dot`/`ntp`）、uid、`mihomoMark`，以及本地地址段（`privateV4`、`multicastV4`、`ulaV6`、`linkLocalV6`、`multicastV6`、`limitedBroadcastV4`）。
* Clash 内核与其 Merge 模板配置在 `home/config/cvr-merge.nix`（TUN 栈、`dns-hijack`、`route-exclude-address`、`routing-mark`、fake-ip DNS 与过滤器、sniffer、`respect-rules`）。

## 8. 文件映射

| 文件 | 职责 |
|---|---|
| `system/config/machine.nix` | 接口/uid/端口/mark/TUN/可调项的单点定义 |
| `system/config/proxy-options.nix` | `my.proxy.tunMode`、`tunDev`、`fakeIpRange` |
| `system/config/network.nix` | nftables ruleset、`proxyModeRules`、`blockedModeRules`、`:53` 重定向、env 代理变量、`dnsmasq`/`unbound`/`resolved`、NM dispatcher |
| `system/programs/systemd/proxy-mode.nix` | 模式状态机、判据、片段装载、`request_wake`/`request_verify`、`proxy-net-watch`/`proxy-net-wake` |
| `system/programs/systemd/dns-upstream.nix` | `dnsmasq` 上游切换、有界明文窗口 |
| `system/programs/systemd/gost-relay.nix` | gost 监听生命周期（proxy / closed / 直通） |
| `system/programs/systemd/nftables-verify.nix` | 内核状态校验、定时器、可选修复 |
| `system/programs/systemd/netsec-alert.nix` | `netsec-alert@` 告警通道 |
| `system/programs/systemd/nftables-recover.nix` | 重试失败的 `nftables.service` |
| `system/programs/clash-verge.nix` | service mode 内核与 sing-tun `route-exclude` 补丁 |
| `home/config/cvr-merge.nix` | Clash Merge 模板（TUN、fake-ip DNS、sniffer、`routing-mark`） |
| `home/config/dconf.nix` | GSettings 系统代理 → gost |
| `system/programs/flatpak.nix` | Flatpak 代理环境覆盖 |
| `home/config/nushell.nix` | `proxy-status`、`clash-on`/`clash-off`、`egress-audit` |

## 9. 限制与取舍

* `:33333` 重定向是 TUN 未承载的 TCP（绑定源/设备的套接字、以及手工 flush 之后的流量）的唯一路径；`gost` 是这些流的单点故障，且到达它的流会被改源为 `127.0.0.1`，因此按来源匹配的 profile 规则不适用。直接 `connect()` 到 `:33333` 会让 gost 重新进入自身监听并不断产生套接字直到 fd 上限；supervisor 会检测这种自连套接字并重启，因此不应为了"测试"去连接 `:33333`。
* 模式 B 下只有 `unbound`（DoT，tcp/853）与 `systemd-timesyncd`（udp/123）可直接出网做 DNS/NTP；其它明文解析器查询除非先被重定向进 `dnsmasq`，否则被丢弃。模式 A 下 DHCP 提供的解析器会在明文窗口内被加入（见最后一条）。
* 断网保护按 mark 而非 uid 豁免核心：未打标的 root 流量与其它流量一样被丢弃或重定向。DHCP 在两个片段里都有显式例外；tailnet 规则是目的地址层面的豁免，且限定 uid 0（tailscaled），因此不是其它进程通用的 UDP/41641 通道。
* 模式 C 保留核心存活，因此 mihomo 自身判为 `DIRECT` 的流量仍会离开；loopback 保留供本机 IPC 使用，但面向应用的代理端口（`7897`/`33332`/`33333`）对非 root 的 TCP 与 UDP 都被拒绝。guard 是在 mihomo 重定向之前求值的 base chain，因此对本机产生的 TCP 同样成立。
* 健康检查由 `health_ok` 决定：经 mixed 端口的一次廉价 `http://` 请求、对日志中那个组做 Clash 的 `/proxies/<group>/delay`（Clash 本位——测的是实际生效组的**所选节点**，而不是"订阅里任意一个没超时的节点"；组名会先对 `GET /proxies` 解析），以及一条 TUN 自测（对该 fake-ip 发 DNS 查询，由核心经 TUN 应答——不依赖任何第三方站点的端口策略）。TUN 开关是事件驱动（rtnetlink）。"5 秒"是对**睡眠**的封顶，不是决策周期：稳态下一次 pass 加一个 interval 约为 5.8 秒。`route`/`node`/`tun` 立即切断；`obs` 层需要连续两次失败。组名与节点名在 delay 请求前会做 URL 编码，因此空白字符或 `/` 不再破坏它；组的端点无应答时会退回到成员自身的 delay 测试；策略文本被精确解析，因此形如 `DIRECT-中转` 的组名不会被误判为 DIRECT 路由。
* DNS 监听（`:1053`）须按 cgroup 属于 `clash-verge.service`（`nftables-verify` 校验），`gost-relay` 对 mixed 端口（`:7897`）做同样的 cgroup 校验，因此本机进程无法靠绑定两者中的任一引走流量；“Clash 在运行”要求核心，而非仅 GUI。
* mihomo 外部控制口是全局可写、无 secret 的 socket——登录用户的任意进程都能重配内核。模式或端口标志是有界的（下一个 pass 会察觉并转入模式 C），但整份 config 重载或规则集/geodata 替换没有上界：它能让所有健康门保持绿色，同时把其余流量以真实地址发出。root 内核还从用户家目录读取配置与数据库。单用户桌面下接受；Merge 模板无法覆写。
* `systemctl stop nftables.service` 不会移除防火墙（拆除与装载是同一个 `nft -f` 事务，deletions 文件为空）。要重新装载，用 `sudo systemctl restart nftables.service` —— 绝不要 `nft flush ruleset`，它还会毁掉 mihomo 自己的表与 TUN 路由，只有重启 TUN 才能重建。
* 模式 A 需要停掉核心，而非只关窗口：service mode 下常驻的 `clash-verge.service` helper 托管核心，因此用 `clash-off`/`clash-on`。
* 转发的访客/虚拟机流量不经过 `output`、不带 uid；它在 `proxymode_forward` 里被拒绝公网目的地址，且永不代理。
* 已记录的取舍：模式 A 保留一个有日志的明文 DHCP 解析器窗口——每次开启 120 秒，并受每条上行链路 300 秒的总预算约束，因此即使 NetworkManager 持续报告 `portal`/`limited` 也无法让明文 DNS 无限期开启（预算用尽后 DNS 直接 fail-closed，直到链路变化或操作员 touch `force-plaintext`）；模式 B 下 `100.64.0.0/10` 与 tailnet 经 output 链早期的 `local4` accept 按设计直接可达；A→B 的窗口不可消除，但由 supervisor 的兜底间隔界定。
* `gost` 只从 store 路径运行（`gost-relay` 用绝对路径）；不在系统 PATH 上，也不复制进 initrd。
