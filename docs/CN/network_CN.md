# 网络架构

[English](../network.md) | 简体中文 | [文档目录](README_CN.md)

这台主机默认强制使用严格加密 DNS，并要求代理订阅先经过登记。日常 Firefox 走系统 DNS，与其他桌面应用共用透明 A/B/C 策略；`firefox-identity` 跑在独立网络命名空间里，只走 **B** 模式下的专用代理入口；在 **A** 模式下登录门户则拉起临时浏览器。初始化步骤见[来源登记](operations_CN.md#来源登记)。

## 流量路径

宿主机通过 `nftables` 在直连、受管代理和阻断三种状态之间切换。应用直接用原生 TCP/UDP socket 发起连接；在健康的 **B** 模式下，TUN 和 TCP 后备两条路径都会汇入 Mihomo：

```text
原生 TCP 和 UDP                       TCP 后备
        |                                 |
   TUN "Mihomo"                nft REDIRECT -> Gost :33333
        |                                 |
        +------> Mihomo <--------- mixed :7897
                    |
               rules 或固定出口
```

Gost 监听在宿主命名空间，宿主回环到核心端口的流量通过私有 `veth` 转进 `lf-clash-core` 命名空间，核心的监听端口和真正的 TUN 设备都住在里面。由于本机启用了 `my.proxy.transparentApplications`，Niri、Nushell、GSettings、Flatpak、Zed、Nix daemon 和 Hermes 都直接走原生路径，不需要配置应用级代理环境变量。

开启透明代理后，Flatpak 安装服务首次运行时会清掉系统全局覆盖里那些仍与旧声明一致、且有 `nix-flatpak` 归属记录的 Gost 代理变量（手改过或归属不明的键会原样保留，并在服务日志里点名；用户级和按应用区分的覆盖不受影响）。切完代次记得注销重新登录，让会话进程拿到新环境。在 **A** 下原生 socket 直接出站，**C** 下则阻断普通外部流量；如果是采用兼容默认值 `my.proxy.transparentApplications = false` 的系统，客户端仍可指向 `127.0.0.1:33332` 的 Gost HTTP 代理。

启用透明应用路径后，防火墙会在 **A**、**B**、**C** 三个模式下统一拦截普通应用用 TCP/UDP 直连 Gost 和 Mihomo 入口——它在 NAT 前检查本机目标，只放行受管 relay 去连核心的 mixed 和 strict 入口。核心控制器则放在私有 Unix socket 上，Clash Verge 通过受管的 Unix 转发器访问，身份浏览器另走自己的[隔离 relay](browser_CN.md#网络策略与身份)。

### 保留端口与服务身份

`user.slice` 用 `SocketBindDeny` 把固定代理端口和本地 DNS 端口保留下来（覆盖 TCP/UDP 和 IPv4/IPv6），桌面会话和用户管理器都会继承这条限制，其他开发端口照常可用。`ip_local_reserved_ports` 也把这些端口连同固定的 Tailscale UDP 端口排除在自动分配范围外，覆盖 `bind(0)` 和客户端隐式绑定。同时，宿主 Gost 应答要求匹配预期的 root/Gost socket UID，核心应答必须经由受管 `veth` 和已跟踪的入口连接回来，就绪检查则会核对监听地址、UID、cgroup 和网络命名空间。

端口保留依赖 systemd 的 cgroup `bind4`/`bind6` hook 和内核 cgroup-BPF 支持。桌面进程继承 `user.slice`，Hermes 使用 `hermes.slice`，其他非 root 系统服务也必须放在受保护 slice 里，或者显式审核其基础设施监听。Root 和受管服务身份构成信任边界：应答 UID 检查用来认证回包 socket 的身份，绑定限制则防止数据落到未授权的 UDP 监听者手里（机制见 [systemd 绑定策略](https://www.freedesktop.org/software/systemd/man/260/systemd.resource-control.html#SocketBindAllow=bind-rule)）。

### 路由与 DNS

Mihomo 在自己的命名空间里会给 socket 打上 `6666` / `0x1a0a` 的 mark。宿主机在收到 `veth` 入站报文时会先清掉 packet mark，再独立检查源身份、目标范围、保留端口和回包归属，所以隔离核心不可能靠伪造 mark 混进旧版宿主 socket 例外（那条例外同时要求两个 mark 和 root/core UID）。默认 `my.proxy.proxyOnly=false` 时，`Rule` 路由可以选 `DIRECT`；可选的[统一代理策略](proxy_CN.md#路由策略)会在核心内部拒绝应用 `DIRECT`，而核心之外的私网和基础设施路径仍归各自防火墙规则管。核心内无论字面还是解析出来的私网目标，都按[本地 DIRECT 与远程叶节点](proxy_CN.md#域名目标)分别授权——订阅规则负责选节点，访问内部目标还得看目标授权。

[DNS](dns_CN.md) 与这条数据面并行工作，走的是 `resolved → dnsmasq → Mihomo 或 Unbound`。日常 [Firefox](browser_CN.md) 默认走 Gost，或者在开启透明路径后直接用本机 DNS 和宿主 socket，跟随同一套 TUN/防火墙策略，从而支持 HTTPS-RR/ECH 发现。身份入口则走专用 Unix relay 和严格核心入口，两者都套着保留桌面 UID 的[外层文件系统及桌面接口沙箱](browser_CN.md#外层沙箱)，严格入口还会额外隔离 IP 网络。

## 核心网络命名空间

[core-network.nix](../../system/config/network/proxy/core-network.nix) 负责拉起私有命名空间和 `veth` 链路。宿主机一侧看到的接口使用配置的 `tunDev` 名称，真正的同名 TUN 设备只存在于 `lf-clash-core` 里面；一个无 capability 的 root 观察服务通过固定操作读取私有监听和路由，就绪要求宿主和核心两侧的选路检查在共用的两秒截止内一起完成。

`my.proxy.coreLink` 提供私有的 IPv4 `/30`、IPv6 `/64` 端点以及固定本地 MAC，建立拓扑和就绪检查都会拒绝与现有 LAN/VPN 地址或路由重叠。这样一来，就算 `veth` 掉了也不会把核心入口误转到物理局域网上；宿主主路由表没有可用路径时，核心传输查询也会直接失败，避免落进 TUN 表绕回自身。物理地址/路由变化或休眠唤醒时会自动刷新已授权的 helper/core，显式执行 `clash-on` 则会重建命名空间并掐断已有核心连接。

宿主防火墙会双向复核核心传输：允许转发公网 TCP/UDP 和声明的 `localTargetCidrs`，同时限制危险目标、保留端口和外部明文 DNS。从核心 `veth` 进宿主的入站流量，只接受带 core-link conntrack 标记的已跟踪回包，拒绝核心向宿主私网或公网监听发起的新连接。这是一道传输边界，而不是按工作负载细分业务目标；localhost 兼容和 GUI 通道都走[独立 Unix relay](proxy_CN.md#组件与身份)，不会把宿主 INET/netlink 描述符传进核心。

## 模式与执行

| 模式 | 进入条件 | 应用出站 | 默认 DNS 上游 |
|---|---|---|---|
| **A** / `direct` | 默认直连策略下冷启动或显式 `clash-off` | 直连 | 本地验证的 Unbound DoT，必要时有界明文恢复 |
| **B** / `proxy` | 代理意图、已授权核心、所需 TUN 选路和健康证明均成立 | 代理、明确例外或丢弃 | 受管 fake-IP，配置 WireGuard 出口时返回已验证真实地址 |
| **C** / `blocked` | 已请求代理但未就绪、崩溃或不健康 | 除恢复与链路基础设施外阻断 | Unbound（外部 DoT 被切断，符合缓存条件的答案继续返回） |

`proxy-mode` 遵循 root 写入的单次开机意图。关掉 GUI 会保留代理意图，因此在代理意图下如果核心停了或崩了，宿主机就会掉进 **C**；要回 **A** 请跑 `clash-off`，想在 **C** 里拉取所选来源请看[恢复已登记来源](operations_CN.md#恢复已登记来源)。光打开 GUI 绝不会授权 `A → B`。如果设了 `my.proxy.bootMode="proxy"`（默认 `"direct"`），系统会在物理网络起来前就先装上 **C** 等着 **B**。

DNS 传输由另一个独立选项控制：`my.proxy.dnsEncryption="strict"` 会禁掉 **A** 下的公网明文恢复和运行时 override，但保留已批准的 split DNS 和 MagicDNS；默认的 `"opportunistic"` 则保留门户恢复能力。两种模式下 `dnsmasq` 和核心都共用同一份域名过滤数据，覆盖代理目标检查（见 [DNS 各路径的责任](dns_CN.md#各路径的责任)）。至于 `unenforced`，它不是另一种保护模式，而是错误状态——代表预期的内核策略没能确认或修复，使用共用就绪检查的应用会直接拒绝它。

健康判断会综合检查核心的 cgroup、所属命名空间内的被动 TCP/UDP 监听身份、受管 API 值、私有 TUN 与宿主 `veth` 路由，以及 root 和桌面 UID 未打标 IPv6 的实际选路。mixed 入口的 HTTP 请求会先跑，方便和 DNS/TUN 检查分开定位。启用 TUN 时会并发预热所有候选域名并在同一轮探测里复用 fake-IP——本机先试 `cp.cloudflare.com`，再试 `connectivity-check.ubuntu.com`（在 `rules` 模式下受管来源会把这两个域名路由到 `lf-remote`）。TUN TCP 按顺序试这些候选，至少要有一个完整跑通 fake-IP DNS、TUN DNS hijack 和真实 HTTP 请求。HTTP 路由证明会用核心日志或请求进行中的连接表记录精确匹配源地址、源端口和目标（日志交付最多等 2.5 秒，整体受共享截止时间约束：模块默认 12 秒，本机为了容纳冷 DNSSEC 查询设为 18 秒）。Mixed HTTP 要求走到远程代理，TUN 则遵循 root 为目标批准的路由类别。明确的配置、路由或 TUN 失败会立刻阻断；观测、DNS、叶子或应用解析器失败则给连续三轮机会（每轮间隔至少两秒），并与叶子恢复、候选会话抽查和系统解析器诊断共用健康预算。每次 fake-IP 查询和 TUN DNS 自检使用 `my.proxy.healthDnsTimeoutSeconds`（默认每次尝试 3 秒，在共享截止内重试一次）。

`data_plane_ok`、`route_ok`、`dns_ok` 和 `tun_ok` 分别给出各层诊断，`null` 代表未证明或不在本次检查范围内，只有完整的 `ok=true` 结论才能授权 **B** 或续租。`health.json.scope` 会说明本次检查覆盖了什么：`scope.requests` 列出本轮请求路径（`--fast` 时为空），配了 WireGuard 出口还会把真实地址 DNS 和原生 IPv4/IPv6 echo 纳入完整证明。`scope.udp_data_plane="not-probed"` 则把 UDP 边界说清楚：系统核对监听身份，但不会持续探测 UDP 可达性和 association 回程，所以单独的 UDP 故障仍可能让 **B** 和 TCP 健康结果保持正常。在运行时 `proxy-only` 下，如果所选适配器不支持 UDP 会报告 `unsupported`，IPv6 目标能力仍取决于节点。

**B** 下有 `rules` 和 `proxy-only` 两种路由选择。`clash-rules` 和 `clash-proxy-only` 都会经 **C** 切换目标并等 **B** 验证通过，`proxy-status` 会显示 **B** 和 **C** 里的路由选择及事务状态。同一轮开机内执行 `clash-off`/`clash-on` 会保留选择，重启后恢复 `rules`（见[路由策略](proxy_CN.md#路由策略)）。

### B 授权时效

本机把 `my.proxy.authorizationLeaseSeconds` 设为 `60`（模块默认是 `null`，此时 **B** 授权完全依赖运行中的监督器；若要启用内核到期保证，可选 30 到 120 秒）：

```nix
my.proxy.authorizationLeaseSeconds = 60;
```

只有完整跑通的健康检查轮次才能续租。每份证明只能消费一次，并且绑定本次启动、受管配置、策略代次和休眠 epoch；续租只用证明剩下的有效期，延迟观测或滞回轮次没法凭空续出一段完整租期。

带超时的 `nftables` 授权同时覆盖 IPv4 和 IPv6：一旦到期，已安装规则就会在 TUN/重定向和 established 放行之前执行 **C** 的请求与回包守卫——连同已有的宿主和访客连接一起拦下——只留下 **C** 原有的恢复与链路维护例外。这样即使监督器卡死或停掉，也留不住应用授权，更不会退回直连；最后的模式文件可能还写着 `proxy`，但就绪检查会独立报出租约已过期。休眠钩子会在挂起前主动撤权，唤醒后必须重新拿到新鲜证明（休眠前的证明不能拿来续租）；如果在重建的 generation 里把选项改回 `null`，就会显式移除这项内核到期保证。

## 防火墙生命周期

[network.nix](../../system/config/network/network.nix) 负责集成静态策略，[mode-fragments.nix](../../system/config/network/firewall/mode-fragments.nix) 生成 B/C 片段，[coordinator.nix](../../system/config/network/proxy/coordinator.nix) 则在共享锁下用单次 `nft -f` 事务把静态规则和安全模式一起装进去。事务只替换本仓库自己拥有的宿主表及其中的 `lf_core_*` 链：Mihomo 管私有命名空间里的自有表，初始化另安装私有的 `lf_core_inlets` 和 `lf_core_local_udp` 表（开启本地 DNSSEC 时还有 `lf_core_dns`），其他服务的宿主表都原封不动。

### 表归属

加载器精确管理下面这些表 family/name 组合：

| 表 | 职责 |
|---|---|
| `inet lf_filter` | 宿主、访客和 DNS 过滤，包含随模式变化的链 |
| `inet lf_policy_guard` | 代理意图下独立逐包检查，主表或必要模式链丢失时切断流量 |
| `ip lf_nat`、`ip6 lf_nat` | 受管 DNS 重定向；IPv4 另提供已声明访客网络的地址伪装 |
| `ip lf_proxymode_nat`、`ip6 lf_proxymode_nat` | B 的 TCP 重定向 |
| `ip lf_mangle` | 可选 IPv4 访客 TPROXY |
| `arp lf_arp` | 在声明的链路上检查 IPv4 ARP 帧的可选策略 |
| `inet lf_bootstrap` | 早期入站/转发基线，由成功的完整加载移除 |

开机启动、重载和漂移修复都走同一套归属范围。共享的 iptables-nft 表（如 `ip nat`、`ip6 nat`、`ip mangle`、`ip`/`ip6 filter`）以及 `inet mihomo` 这类服务原生表都不在里面——外部服务应该用自己的表，往本仓库自有表里插规则会被整体替换或漂移修复清掉。日常运行也不会动 `inet filter`、`ip proxymode_nat` 和 `ip6 proxymode_nat`；显式执行[表迁移](operations_CN.md#防火墙表迁移)时才会在同一持锁事务里删掉目标表并重装完整策略（事务被拒绝则保留现有表和规则）。

### 规则优先级

| Hook / priority | 用途 |
|---|---|
| Output `-202` | 受限 socket 访客的原始目标/UID 守卫 |
| Output `-201` | NAT 前拒绝显式使用 Gost 透明入口 |
| Prerouting `-201` | 重定向前限制访客公网目的地 |
| Prerouting `-199` | 访客源地址、私网目标和网关服务检查 |
| Prerouting `-198` | B 授权到期时执行 C 的访客入站守卫 |
| Output `-199` | 跟踪受限 socket 访客账户；将 dnsmasq 上游 DNS 限定到目的地租约；严格模式阻断核心外部 53，仅保留 MagicDNS |
| Output `-198` | 保留内部 DNS 端口、检查 DNS/代理应答身份、跟踪普通应用连接并检查访客宿主服务回包 |
| Output `-197` | C guard 及 B 授权到期守卫，位于 conntrack `-200` 之后、auto-redirect `-150` 之前 |
| Output `-196` | 限制独立使用 853 端口的 DoT/DoQ |
| Output `-195` | B 可选私网/多播授权与 Unbound DoT 关闭，位于 NAT/TUN 前 |
| Output `-194` | A/B 可选 Chrony 原始目标 CIDR 限制 |
| Output NAT `-190`、filter `-90` | 将受限 socket 访客 DNS 强制送至 1056，并拒绝旧解析器关联 |
| Input `-3`、forward `-2` | B 授权到期时执行 C 的接收/转发守卫 |
| Input `-2`、forward `-1` | 按当前授权检查受限 socket / 转发访客回包 |
| Input `-1` | C 在 established 放行前切断已解密 tailnet 数据及已跟踪宿主/访客应用回包 |

NAT 优先级决定了内核共享 destination-NAT hook 里的查表顺序，socket 访客 DNS 的 `-90` filter 则在这个 hook 之后检查实际改写过的目标地址。

### B 中的流量

在 **B** 下，宿主公网 TCP 会被重定向，fake-IP 保留核心路径，其余公网出站在过完明确例外后全部丢弃。这些例外包括打了 mark 的核心 socket、按 UID 限定的 Unbound DoT 与 Chrony NTS/NTP、窄 DHCP、组播以及默认私网访问。Tailscale UDP 要求 UID 0、物理上行口，且 socket 的 `SO_MARK` 与 packet mark **同时**等于上游 Linux bypass mark `0x80000`，peer/STUN 端口保持动态；未跟踪的物理入站走 `services.tailscale.port` 指定的固定端口，在已有流量放行前检查接收 socket 的 bypass mark（**A** 和 Rules **B** 支持双上行双栈收包，严格 **B**、**C** 和 **B** 租约到期时切断；若 `services.tailscale.port = 0`，则改用自动分配和普通连接跟踪回包路径）。

未打标的 root UDP 没有任何兜底放行。Root 服务负责宿主策略，核心只能改自己的私有网络命名空间，在宿主授权的传输范围内负责可信的应用路由（当前 LKL 内核支持 `socket mark`，不支持 `socket cgroupv2`）。Tailscale 的 bypass mark 是上游常量 `0x80000`，不会随网络变化；[transport.nix](../../system/config/network/tailscale/transport.nix) 会在系统构建时核对所选软件包的打标行为，哪怕打标失效，**B** 依然会阻断未打标的 root UDP。

[直连例外清单](egress_CN.md)逐项列出了主体、目标、端口和模式。可选受限 **B** 按接口/UID/CIDR/端口授权，在 TUN/NAT 前切断撤权后的已有宿主主动连接；宿主 Unbound 在 **A** 只能连声明的 DoT 目标（单独的基础设施 Unbound 在 Rules **B** 的例外也可以关掉），Chrony 则可在 **A**/**B** 固定目标 CIDR，默认保留 LAN 与 NTS 地址变化的兼容性。

### C 中的流量

在 **C** 下，早期 output guard 会在核心重定向前把普通应用流量拦下来，只留 loopback IPC、打了 mark 的核心 socket、root 经代理/DNS 端口和 fake-IP 53/80/443 的恢复访问、窄 DHCP 以及 hop-limit 255 的 NDP。非 root 到核心/Gost 应用端口的 TCP/UDP 一律阻断；自测应答例外只认预期的 loopback 源，或者原目的地为 fake-IP HTTP 的 root/core conntrack DNAT 回包。

访客 DHCP/DNS 应答按网桥、网关源地址和端口识别，不看守护进程 UID。宿主 Unbound 和普通 tailnet 流量都失去了外部出站能力（包括已有的远控连接），**B** 的授权不会带到 **C** 里，另外还有入站守卫在 established 放行前丢掉已解密的 tailnet 数据包（包括切换时已经在途的包）。

**C** 同时会切断已跟踪的宿主应用 TCP/UDP 连接回包，覆盖原生 TUN、fake-IP 和显式 SOCKS UDP：优先级 `-198` 的 output hook 会给尚无标记的普通应用连接打上 conntrack mark `0x4c460001`（受限访客用独立标记，root、核心和 Gost 传输连接排除在外），**C** 的 input 守卫则丢掉带标记的网络和固定代理入口回包。把拦截放在接收侧，能让存活的 SOCKS UDP association 在 **B** 恢复后继续收包。因为接收侧约束依赖这些 conntrack 标记，其他防火墙组件必须保留其含义（conntrack 最高位用来记录送入核心 `veth` 的连接，应用/访客身份检查会屏蔽并保留该位）。已经在 socket 队列里的数据不在此约束范围内，而 loopback IPC、本地 DNS、root/核心恢复和链路维护在 **C** 下照常可用。

### 加载与恢复

[bootstrap.nix](../../system/config/network/firewall/bootstrap.nix) 会在 NetworkManager 或 `dhcpcd` 启动前就装好入站、转发和 DNS 防护，哪怕首次完整装载失败也会留着这道基线。静态规则发生漂移时会触发完整修复——直连意图回到 **A**，代理意图先回 **C** 再跑健康检查，修复失败则报 `unenforced`。在代理意图下，`inet lf_policy_guard` 会逐包检查是否经过了必要的主策略链；只要主表或这些链丢了，哪怕旧 **B** 租约还没到期，新建和已有流量也会立刻被切断。

协调器和验证器会在仅 root 可进的运行目录里抓取完整 `nftables` 规则集，比对完就删掉快照；桌面诊断只读取公开的模式、验证结论和计数摘要。停掉 `nftables.service` 不会卸掉规则，restart/reload 负责重新协调，千万别跑 `nft flush ruleset`，那会把核心自己的表也一并冲掉。具体验证与恢复操作见[操作与诊断](operations_CN.md)。

## 上行与本地服务

有线 `ens1` 归 `dhcpcd` 管，NetworkManager 按名称和硬件 MAC 把它排除在外；接口名按永久 MAC 匹配，不受克隆后的线上地址影响。`dhcpcd` 以原生单接口模式运行，把 DHCPv6 监听绑到有线链路本地地址上，让 Wi-Fi 的 DHCPv6 客户端可以同时工作。

Wi-Fi `wlo1` 归 NetworkManager 管，按稳定的 PCI 路径匹配。扫描用随机 MAC，连接默认用 `stable-ssid`，配合持久化的 NetworkManager 状态和 `machine-id`，跨根回滚也能保持这个 MAC 身份不变（profile 显式设置优先于默认值）。按 profile 协调 MAC/DHCP/IPv6 迁移、有线选项、持久状态和重新授权见[链路身份](identity_CN.md)，可信网关、应答范围和链路隔离见 [IPv4 ARP 防护](identity_CN.md#ipv4-arp-防护)。

Tailscale 不会自动接受 DNS、子网路由或出口节点，只给当前已认证 tailnet 的 MagicDNS 后缀配条件 DNS 路由。宿主入站默认只放行 Sunshine 串流（TCP 47984/47989/48010 和 UDP 47998/47999/48000/48002/48010），Sunshine 随图形会话启动，管理界面仅本机可访并关闭 UPnP，用法见[远程桌面操作](operations_CN.md#远程桌面)。

Avahi 只在 loopback、tailnet 和显式开启的 AP 上广播，外网 Wi-Fi 客户端不发布。本配置没有导入 `sshd` 模块，单靠防火墙放行端口也不会自己起一个 SSH 监听。[time.nix](../../system/config/network/time.nix) 为 Chrony 配了认证 NTS（UDP 123、NTS-KE TCP 4460），靠 RTC/drift 恢复提供离线时间下限，保持证书时间校验且不设明文时间回退。[hardening.nix](../../system/config/network/hardening.nix) 提供了共享 sysctl、redirect/source-route 和内核模块限制，Nix daemon 的 `trusted-users` 也只留了 root。

## 访客与容器

[policy.nix](../../system/config/network/guests/policy.nix) 把经宿主机转发的访客网络和直接在宿主开 socket 的后端区分开来，按声明的入口接口、源 CIDR 和专用后端 UID 划分信任范围。

| 类别 | 身份依据 | 私网目标 | 公网目标 |
|---|---|---|---|
| 受限桥接 / 热点（默认） | 精确入口接口与声明的源 CIDR | 明确的接口/CIDR/TCP 或 UDP 端口授权 | **A** 允许 IPv4；**B** 须显式选择 IPv4 TPROXY；**C** 拒绝 |
| 可信开发桥 | 相同的接口/源地址检查，显式 `trust="trusted"` | 允许私网路由，仍拒绝不安全目标 | 与受限桥相同 |
| 受限宿主 socket 后端 | 独立固定 UID 及其全部委派 subordinate UID | 明确的账户/原始出口接口/CIDR/端口授权 | 宿主 **A**/**B**/**C** 策略；**C** 也切断已跟踪回包 |
| 桌面账户的 passt / rootless Docker | 共用桌面 UID | 宿主策略 | 宿主策略 |

`my.proxy.guestInterfaces` 默认包含 `virbr0` 和 `waydroid0`，开启独立 AP 后也会加进来。`my.hardening.guestNetworks.<接口>` 用来声明信任级别、规范且互不重叠的 `sourceCidrs`、网关地址和宿主服务端口（内建角色默认取 `machine` 里的 IPv4 子网/网关，IPv6 需要显式 ULA 源子网，所有模式都拒绝访客 IPv6 公网，Wi-Fi 客户端上行也不能充当访客角色）。创建 AP、DHCP 服务和网桥与安装防火墙策略依然是两步独立操作。

所有角色的私网转发默认都是受限的，热点也不例外。`guestPrivateExceptions` 按精确入口接口、私网目标 CIDR 和 TCP/UDP 端口放行；开发网络可以设 `trust="trusted"` 来放开私网路由，但不会顺便开放任意宿主服务或公网 B/C 转发（兼容选项 `guestPrivateEgress=true` 只信任未明确设置 `trust` 的 libvirt/Waydroid 角色，逐接口设置优先）。

网关 DHCP/DNS 和宿主 loopback 解析器分开，`hostTcpPorts`/`hostUdpPorts` 只在对应角色声明的网关地址开放服务（旧 `guestHostServices` 和端口清单合并进同一策略）。源检查先于重定向，宿主访问检查先于 established 入站放行，且当前授权同时约束已有转发连接的双向流量与宿主服务回包，因此撤销授权也会切断服务端主动推送。不同信任级别要建独立网桥：这套策略认的是网络而不是隔离同桥二层流量，也不认证具体访客。

`transparentGuestInterfaces` 默认为空，只在健康 **B** 中为显式选定的接口开放 IPv4 TPROXY 入口（有效 fake-IP 映射依赖这个入口，其余合成地址、选定元数据端点和已知转换/过渡网段仍被拒绝；**C** 会切断已代理访客的回包同时保留核心恢复路径）。桥接私网授权与公网模式守卫各自独立。

因为 `passt` 和 rootless 网络会把访客报文转成宿主 socket，网桥规则认不出容器。不可信后端需要通过 `my.hardening.socketGuestUsers` 配一个独立固定的普通账户（正常构建拒绝桌面/root/基础设施身份、共用 UID 和重叠的委派范围），再用 `socketGuestPrivateExceptions` 按账户、原始出口接口、私网 CIDR 和目标端口授权。回环/代理入口、保留代理/DNS 目标端口、fake-IP 和已知转换目标都不能授权；TCP/UDP 53 强制走[过滤后的真实地址解析器](dns_CN.md#受限-socket-访客-dns)，防止 **B** 的合成地址把 UID 守卫要查的私网最终目标遮住，同时这些账户的 rootless Docker 启动器会清掉继承的显式代理环境变量。

切记先装好策略再以声明账户启动后端，新增或改动受限账户与 UID 映射前先停掉旧后端（回包跟踪没法追溯认出旧的未标记 socket）。在身份保持声明期间撤销私网授权，会切断已跟踪连接的双向流量。桌面账户下的 rootless Docker 因为和桌面应用共用 UID，仍算可信开发环境。另外，UID 策略只管网络出站——文件权限、共享 Unix socket、入站端口发布和同桥隔离得靠各自手段控制；授权匹配的也是宿主 socket/路由的原始目标，不管应用隧道里面的目标。Root 负责可信宿主管理，隔离核心在其传输授权内负责可信应用路由，配置示例见[配置入口](operations_CN.md#配置入口)。

本机把 Hermes Docker 工作负载交给了独立账户 `hermes-containers`（默认 UID 1200，带独立的 subuid/subgid 范围），它的宿主 socket 走受限账户策略；而 Hermes agent、dashboard、本地 terminal 和 MCP 进程保留自身宿主身份，可在健康 **B** 里走 Gost（**C** 会阻断它们的应用代理流量，加入 `hermes` 组也不会多出一套网络例外）。同一后端里的容器共享该账户的网络授权，入口、数据和服务管理见 [Hermes 容器](operations_CN.md#hermes-容器)。
