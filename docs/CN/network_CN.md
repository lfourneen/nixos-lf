# 网络架构

[English](../network.md) | 简体中文 | [文档目录](README_CN.md)

本机使用严格加密 DNS 和已登记的订阅来源。
日常 Firefox 使用系统 DNS，与其它桌面应用共用透明 A/B/C 路径。
`firefox-identity` 使用独立网络命名空间，只通过 B 中的专用代理入口联网。
A 中的门户登录使用临时浏览器。
初始化方法见[来源登记](operations_CN.md#来源登记)。

## 流量路径

宿主通过 nftables 选择直连、受管代理或阻断。

应用使用原生 TCP 和 UDP socket。健康 B 中，TUN 路径与 TCP 后备路径汇入 Mihomo。

```text
原生 TCP 和 UDP                       TCP 后备
        |                                 |
   TUN "Mihomo"                nft REDIRECT -> Gost :33333
        |                                 |
        +------> Mihomo <--------- mixed :7897
                    |
               rules 或固定出口
```

Gost 监听于宿主命名空间。
宿主回环核心入口经私有 veth 转发，核心监听和实际 TUN 位于 `lf-clash-core`。
本机启用 `my.proxy.transparentApplications`。Niri、Nushell、GSettings、Flatpak、Zed、Nix daemon 和 Hermes 使用原生路径，无须受管应用代理设置。
应用配置代次后，注销并重新登录，使会话进程使用新的环境变量。
A 中原生 socket 直连，C 阻断普通外部流量。
采用兼容默认值 `my.proxy.transparentApplications = false` 的安装仍可让应用使用 `127.0.0.1:33332` 上的 Gost HTTP。

启用透明应用路径时，防火墙在 A、B 和 C 中拒绝普通应用使用 TCP 或 UDP 直访 Gost 和 Mihomo 入口。
它在 NAT 前检查本机目标，并允许受管 relay 连接核心的 mixed 和 strict 入口。
核心控制器使用私有 Unix socket，Clash Verge 通过受管 Unix 转发器访问。
身份浏览器使用独立的[隔离 relay](browser_CN.md#网络策略与身份)。

### 保留端口与服务身份

`user.slice` 通过 `SocketBindDeny` 保留固定代理和本地 DNS 端口，覆盖 TCP/UDP 与 IPv4/IPv6。
桌面会话和用户管理器继承此限制，其它开发端口仍可使用。
`ip_local_reserved_ports` 将这些端口排除在自动分配范围外，覆盖 `bind(0)` 和客户端隐式绑定。

宿主 Gost 应答要求 root/Gost socket UID。
核心应答必须经过受管 veth，并匹配已跟踪的入口连接。
就绪检查使用监听地址、UID、cgroup 和网络命名空间。

端口保留依赖 systemd 的 cgroup `bind4`/`bind6` hook 及内核 cgroup-BPF 支持。
桌面进程继承 `user.slice`，Hermes 使用 `hermes.slice`。其它声明了非 root 身份的 system service 必须配置受保护的 slice，或显式审核其基础设施监听。
Root 和受管服务身份属于信任边界。
应答 UID 检查确认响应 socket 的身份，绑定限制阻止数据交给未授权的 UDP 监听者。

机制见 [systemd 绑定策略](https://www.freedesktop.org/software/systemd/man/260/systemd.resource-control.html#SocketBindAllow=bind-rule)。

### 路由与 DNS

Mihomo 在自己的命名空间内设置 `6666` / `0x1a0a` socket mark。
宿主清除 veth 入站 packet mark，独立检查源身份、目标范围、保留端口和回包归属。
旧宿主 socket 例外同时要求两个 mark 与 root/core UID。
隔离核心不能靠伪造 mark 获得该例外。

默认 `my.proxy.proxyOnly=false` 时 Rule 可选 DIRECT。
可选的[统一代理策略](proxy_CN.md#路由策略)在核心内拒绝应用 DIRECT。
核心以外的私网和基础设施路径仍由各自规则控制。

[DNS](dns_CN.md) 与该路径并行：resolved → dnsmasq → Mihomo 或 Unbound。
日常 [Firefox](browser_CN.md) 使用 Gost。
可选透明路径使用本机 DNS 和宿主 socket，遵循相同 TUN/防火墙策略，支持 HTTPS-RR/ECH 发现。

身份入口使用专用 Unix relay 与严格核心入口。
两者均在[外层文件系统及桌面接口沙箱](browser_CN.md#外层沙箱)内运行，保留桌面 UID。
严格入口额外隔离 IP 网络。

核心内的字面或解析后私网目标按[本地 DIRECT 与远程叶节点](proxy_CN.md#域名目标)分别授权。
订阅规则负责选择节点，节点侧内部目标由目标授权规则控制。

## 核心网络命名空间

[core-network.nix](../../system/config/network/proxy/core-network.nix) 建立私有命名空间及 veth 链路。
宿主接口使用配置的 `tunDev` 名称，真正的同名 TUN 只存在于核心命名空间内。
无 capability 的 root 观察服务通过固定操作读取私有监听和路由。
就绪要求宿主及核心两侧的选路检查在共用的两秒截止内完成。

`my.proxy.coreLink` 提供私有 IPv4 /30、IPv6 /64 端点及固定本地 MAC。
建立拓扑和就绪检查均拒绝与现有 LAN/VPN 地址或路由重叠。
veth 丢失不能把核心入口转发到物理 LAN。
宿主主路由表没有可用路径时，核心传输查询直接失败，避免落入 TUN 表绕回自身。

物理地址/路由变化和恢复钩子更新已授权的 helper/core。
显式 `clash-on` 重建命名空间，并断开已有核心连接。

宿主防火墙双向复核核心传输，允许公网 TCP/UDP 和声明的 `localTargetCidrs`，另行限制危险目标、宿主保留服务与外部明文 DNS。
这是传输边界，不是按工作负载限定业务目标。
localhost 兼容和 GUI 通道使用[独立 Unix relay](proxy_CN.md#组件与身份)，不向核心传递宿主 INET/netlink 描述符。

## 模式与执行

| 模式 | 进入条件 | 应用出站 | 默认 DNS 上游 |
|---|---|---|---|
| A / `direct` | 默认直连策略下冷启动或显式 `clash-off` | 直连 | 本地验证的 Unbound DoT，必要时有界明文恢复 |
| B / `proxy` | 代理意图、已授权核心、所需 TUN 选路和健康证明均成立 | 代理、明确例外或丢弃 | 受管 fake-IP，配置 WireGuard 出口时返回已验证真实地址 |
| C / `blocked` | 已请求代理但未就绪、崩溃或不健康 | 除恢复与链路基础设施外阻断 | Unbound。外部 DoT 被切断，符合缓存条件的答案继续返回 |

`proxy-mode` 遵循 root 写入的单次开机意图。
关闭 GUI 会保留代理意图。保留代理意图时，核心停止或崩溃会使宿主进入 C。
使用 `clash-off` 可请求 A。要在 C 中获取所选来源，见[恢复已登记来源](operations_CN.md#恢复已登记来源)。
只启动 GUI 无法授权 A → B。
可选 `my.proxy.bootMode="proxy"` 在物理网络启动前安装 C，等待 B。
默认是 `"direct"`。

DNS 传输独立设置：`my.proxy.dnsEncryption="strict"` 禁用 A 的公网明文恢复及运行时 override，保留已批准 split DNS 和 MagicDNS。
默认 `"opportunistic"` 保留门户恢复。
两种策略都让 dnsmasq 和核心共享域名过滤数据，覆盖代理目标检查，见 [DNS 各路径的责任](dns_CN.md#各路径的责任)。
`unenforced` 表示预期内核策略无法确认或修复，是错误状态。
使用共用就绪检查的应用会拒绝该状态。

健康判断检查核心 cgroup、所属命名空间内的 TCP/UDP 监听身份、受管 API、私有 TUN 与宿主 veth 路由，以及 root/桌面 UID 未打标 IPv6 的实际选路。
先执行 mixed 入口的 HTTP 请求，使其结果可以与 DNS/TUN 检查分别诊断。
启用 TUN 时并发预热全部候选名称，并在同一轮探测中复用得到的 fake-IP。
本机先尝试 `cp.cloudflare.com`，再尝试 `connectivity-check.ubuntu.com`。在 rules 模式下，受管来源将这两个域名路由到 `lf-remote`。
TUN TCP 按配置顺序尝试候选，至少一个名称须完成 fake-IP DNS、TUN DNS hijack 和真实 HTTP 请求。
HTTP 路由证明使用核心日志，或请求仍在进行时采集的连接表记录，精确匹配源地址、源端口和目标。
记录的叶子必须符合当前策略图。Mixed HTTP 要求远程代理，TUN 遵循 root 为目标批准的路由类别。
日志最多等待 2.5 秒，仍受共享截止约束。
模块默认共享截止为 12 秒；本机设为 18 秒以容纳冷 DNSSEC 查询。
明确的配置、路由或 TUN 失败立即阻断。
观测、DNS、叶子或应用解析器失败需连续三轮，间隔至少两秒。叶子恢复和系统解析器诊断仅在失败时运行，共用健康预算。
每次 fake-IP 查询和 TUN DNS 自检使用 `my.proxy.healthDnsTimeoutSeconds`，默认每次尝试 3 秒，在共享截止内重试一次。

`data_plane_ok`、`route_ok`、`dns_ok` 和 `tun_ok` 分别报告诊断结果，`null` 表示未证明或不在本次检查范围内。
只有完整的 `ok=true` 结论可授权 B 或续租。

`health.json.scope` 声明本次调用的检查范围，只有 `ok=true` 才证明这些检查完成。
`scope.requests` 列出本轮请求路径，`--fast` 时为空。WireGuard 出口的完整证明包含真实地址 DNS，以及原生 IPv4 和 IPv6 echo。
`scope.udp_data_plane="not-probed"` 明确 UDP 边界：核对监听身份，但不持续证明 UDP 可达性和 association 回程。
单独的 UDP 故障因此可能让 B 与 TCP 健康结果保持正常。运行时 proxy-only 在所选适配器不支持 UDP 时报告 `unsupported`，IPv6 目标能力仍取决于节点。

B 有 `rules` 和 `proxy-only` 两种路由选择。`clash-rules`、`clash-proxy-only` 经 C 选择目标并等待已验证的 B。
`proxy-status` 在 B 和 C 中显示路由选择及事务状态。
Off/on 保留本次启动的选择，重启恢复 rules。见[路由策略](proxy_CN.md#路由策略)。

### B 授权时效

本机设置 `my.proxy.authorizationLeaseSeconds=60`。
模块默认值为 `null`，此时 B 授权仍依赖运行中的监督器。
启用内核到期保证时，可选择 30 至 120 秒：

```nix
my.proxy.authorizationLeaseSeconds = 60;
```

只有完整成功的健康检查轮次可以续租。
每份证明只能消费一次，并绑定本次启动、受管配置、策略代次和休眠 epoch。
续租仅使用证明的剩余有效期，延迟观测及滞回轮次不能重新获得完整租期。

带超时的 nftables 授权覆盖两种 IP 家族。
到期后，已安装规则在 TUN/重定向及 established 放行前执行 C 的请求与回包守卫，覆盖已有宿主和访客连接。
C 原有的恢复与链路维护例外继续可用。
监督器冻结或停止不能保留应用授权，也不会选择直连。
最后的模式文件可能仍为 `proxy`，就绪检查会独立报告授权租约已过期。

休眠钩子在挂起前撤销授权。
恢复后需要新的成功证明，休眠前的证明不能续租。
在重新构建的 generation 中选择 `null`，会明确移除这项内核到期保证。

## 防火墙生命周期

[network.nix](../../system/config/network/network.nix) 集成静态策略。
[mode-fragments.nix](../../system/config/network/firewall/mode-fragments.nix) 生成 B/C 片段。
[coordinator.nix](../../system/config/network/proxy/coordinator.nix) 在共享锁下以一次 `nft -f` 事务安装静态规则和安全模式，只替换本仓库拥有的宿主表及其中的 `lf_core_*` 链。

Mihomo 管理私有命名空间内的自有表，固定 setup 另安装私有 `lf_core_inlets` 和 `lf_core_local_udp` 表，启用本地 DNSSEC 时还安装 `lf_core_dns`。
其它服务的宿主表仍保留。

### 表归属

本仓库管理以下精确的表 family/name 组合：

| 表 | 职责 |
|---|---|
| `inet lf_filter` | 宿主、访客和 DNS 过滤，包含随模式变化的链 |
| `ip lf_nat`、`ip6 lf_nat` | 受管 DNS 重定向。IPv4 另提供已声明访客网络的地址伪装 |
| `ip lf_proxymode_nat`、`ip6 lf_proxymode_nat` | B 的 TCP 重定向 |
| `ip lf_mangle` | 可选 IPv4 访客 TPROXY |
| `arp lf_arp` | 在声明的链路上检查 IPv4 ARP 帧的可选策略 |
| `inet lf_bootstrap` | 早期入站/转发基线，由成功的完整加载移除 |

启动、重载和漂移修复使用同一范围。
共享 iptables-nft 表（如 `ip nat`、`ip6 nat`、`ip mangle` 和 `ip`/`ip6 filter`）以及 `inet mihomo` 等服务原生表均不在其中。
外部服务应使用独立表。
插入本仓库自有表的规则会受到整体替换和漂移修复管理。

日常操作保留 `inet filter`、`ip proxymode_nat` 和 `ip6 proxymode_nat`。
显式[表迁移](operations_CN.md#防火墙表迁移)在同一持锁事务中删除目标表并安装完整策略，清理范围限定在该命令内。
事务被拒绝时保留已安装的表和规则。

### 规则优先级

| Hook / priority | 用途 |
|---|---|
| Output `-202` | 受限 socket 访客的原始目标/UID 守卫 |
| Output `-201` | NAT 前拒绝显式使用 Gost 透明入口 |
| Prerouting `-201` | 重定向前限制访客公网目的地 |
| Prerouting `-199` | 访客源地址、私网目标和网关服务检查 |
| Prerouting `-198` | B 授权到期时执行 C 的访客入站守卫 |
| Output `-199` | 跟踪受限 socket 访客账户。将 dnsmasq 上游 DNS 限定到目的地租约。严格模式阻断核心外部 53，仅保留 MagicDNS |
| Output `-198` | 保留内部 DNS 端口、检查 DNS/代理应答身份、跟踪普通应用连接并检查访客宿主服务回包 |
| Output `-197` | C guard 及 B 授权到期守卫，位于 conntrack `-200` 之后、auto-redirect `-150` 之前 |
| Output `-196` | 限制独立使用 853 端口的 DoT/DoQ |
| Output `-195` | B 可选私网/多播授权与 Unbound DoT 关闭，位于 NAT/TUN 前 |
| Output `-194` | A/B 可选 Chrony 原始目标 CIDR 限制 |
| Output NAT `-190`、filter `-90` | 将受限 socket 访客 DNS 强制送至 1056，并拒绝旧解析器关联 |
| Input `-3`、forward `-2` | B 授权到期时执行 C 的接收/转发守卫 |
| Input `-2`、forward `-1` | 按当前授权检查受限 socket / 转发访客回包 |
| Input `-1` | C 在 established 放行前切断已解密 tailnet 数据及已跟踪宿主/访客应用回包 |

NAT 优先级排列内核共享 destination-NAT hook 中的规则查找顺序。
socket 访客 DNS 的 `-90` filter 在该 hook 之后检查实际改写后的目标。

### B 中的流量

B 重定向宿主公网 TCP，fake-IP 保留核心路径，其余公网出站在明确例外后丢弃。
例外包括核心 mark、按 UID 限定的 Unbound DoT 与 Chrony NTS/NTP、窄 DHCP、组播和默认私网访问。
Tailscale UDP 例外要求 UID 0、物理上行口，且 socket 的 `SO_MARK` 与 packet mark 都等于上游 Linux bypass mark `0x80000`。

保留动态 peer/STUN 端口，未打标 root UDP 按普通规则处理。
Root 服务负责宿主策略。
核心只能修改私有网络命名空间，在宿主授权的传输范围内仍负责可信的应用路由。
当前 LKL 内核支持 `socket mark`，不支持 `socket cgroupv2`。

Tailscale 的 bypass mark 是上游常量 `0x80000`，不会随连接的网络变化。
[transport.nix](../../system/config/network/tailscale/transport.nix) 在系统构建时检查所选软件包的打标行为。
打标失败时，B 仍会阻断未打标的 root UDP。

[直连例外清单](egress_CN.md)逐项记录主体、目标、端口和模式。
可选受限 B 按接口/UID/CIDR/端口授权，在 TUN/NAT 前切断撤权后的已有宿主主动连接。
宿主 Unbound 即使在 A 也只能使用声明的 DoT 目标，B 的此项例外可关闭。
Chrony 可在 A/B 固定目标 CIDR。
默认保留 LAN 与 NTS 地址变化兼容性。

### C 中的流量

C 的早期 output guard 在核心重定向前阻断应用，保留 loopback IPC、核心 mark、root 到代理/DNS 端口和 fake-IP 53/80/443 的恢复访问、窄 DHCP 与 hop-limit 255 的 NDP。
非 root 到核心/Gost 应用端口的 TCP/UDP 被阻断。

自测应答例外限定为预期 loopback 源，或 root/core 的 conntrack DNAT reply 且原目的地为 fake-IP HTTP。
访客 DHCP/DNS 应答按网桥、网关源地址和端口识别，不依赖守护进程 UID。
宿主 Unbound 和普通 tailnet 流量不能外出，包括已有远控连接。

B 的授权不提供 C 远控例外。
另有入站守卫在 established 放行前丢弃已解密 tailnet 数据，包括切换时仍在到达的数据包。

C 阻断已跟踪、由宿主应用发起的 TCP/UDP 连接回包，覆盖原生 TUN、fake-IP 和显式 SOCKS UDP。
优先级为 `-198` 的 output hook 为尚无标记的普通应用连接设置 conntrack mark `0x4c460001`。
受限访客使用独立标记。
root、核心和 Gost 的传输连接不在标记范围内。
C 的 input 守卫丢弃已标记的网络及固定代理入口回包。
接收侧阻断使存活的 SOCKS UDP 关联可以随 B 恢复接收。

接收方向的约束依赖这些 conntrack 标记，其它防火墙所有者须保留其含义。
conntrack 最高位记录送入核心 veth 的连接，应用/访客身份检查保留并屏蔽该位。
Socket 队列内的数据不在此约束范围内。
C 仍保留 loopback IPC、本地 DNS、root/核心恢复和链路维护流量。

### 加载与恢复

[bootstrap.nix](../../system/config/network/firewall/bootstrap.nix) 在 NetworkManager/dhcpcd 前安装入站和转发基线。
首次完整装载失败仍保留它。
静态漂移触发完整修复：直连意图回到 A，代理意图先回 C，再检查健康。
失败报告 `unenforced`。

协调器和验证器在仅 root 可进入的运行目录中抓取完整 nftables 规则集，并在比对后删除快照。桌面诊断读取公开的模式、验证结果和计数摘要。

停止 `nftables.service` 保留规则。
restart/reload 用于协调恢复。
避免 `nft flush ruleset`，它会同时破坏核心自己的表。
验证和恢复方式见[操作与诊断](operations_CN.md)。

## 上行与本地服务

有线 `ens1` 由 dhcpcd 管理，NetworkManager 按名称和硬件 MAC 排除它。
接口名按永久 MAC 匹配，独立于克隆后的线上地址。
dhcpcd 使用原生单接口模式，将 DHCPv6 监听绑定到有线链路本地地址，使 Wi-Fi DHCPv6 可以同时工作。

Wi-Fi `wlo1` 由 NetworkManager 管理，以 PCI 路径稳定匹配。
扫描使用随机 MAC，连接默认 `stable-ssid`。
持久化的 NetworkManager 状态和 machine-id 使这一 MAC 身份跨根回滚保持稳定。
Profile 显式设置优先于 MAC 默认值。

[链路身份](identity_CN.md)介绍按 profile 协调 MAC/DHCP/IPv6 迁移、有线替代策略、持久状态及重新授权。
[IPv4 ARP 防护](identity_CN.md#ipv4-arp-防护)介绍可信网关、应答范围和链路隔离。

Tailscale 不自动接受 DNS、子网路由或出口节点。
只有已认证 tailnet 的 MagicDNS 后缀获得条件 DNS 路由。
宿主入站默认仅 Sunshine 串流 TCP 47984/47989/48010 和 UDP 47998/47999/48000/48002/48010。
Sunshine 随图形会话启动，管理界面仅本机可用，关闭 UPnP，见[远程桌面操作](operations_CN.md#远程桌面)。

Avahi 仅在 loopback、tailnet 及显式启用的 AP 上发布。
外网 Wi-Fi 客户端不发布。
本配置没有导入 sshd 模块，防火墙允许端口也不会自行创建 SSH 监听。

[time.nix](../../system/config/network/time.nix) 为 Chrony 配置认证 NTS、UDP 123 和 NTS-KE TCP 4460。
RTC/drift 恢复提供离线时间下限。
证书时间校验保留，不配置明文时间回退。
[hardening.nix](../../system/config/network/hardening.nix) 提供共享 sysctl、redirect/source-route 和模块限制。
Nix daemon 的 trusted-users 仅含 root。

## 访客与容器

[policy.nix](../../system/config/network/guests/policy.nix) 区分经宿主转发的访客网络与创建宿主 socket 的后端。
信任范围由声明的入口接口、源 CIDR 和专用后端 UID 确定。

| 类别 | 身份依据 | 私网目标 | 公网目标 |
|---|---|---|---|
| 受限桥接 / 热点，默认 | 精确入口接口与声明的源 CIDR | 明确的接口/CIDR/TCP 或 UDP 端口授权 | A 允许 IPv4。B 须显式选择 IPv4 TPROXY。C 拒绝 |
| 可信开发桥 | 相同的接口/源地址检查，显式 `trust="trusted"` | 允许私网路由，仍拒绝不安全目标 | 与受限桥相同 |
| 受限宿主 socket 后端 | 独立固定 UID 及其全部委派 subordinate UID | 明确的账户/原始出口接口/CIDR/端口授权 | 宿主 A/B/C 策略。C 也切断已跟踪回包 |
| 桌面账户的 passt / rootless Docker | 共用桌面 UID | 宿主策略 | 宿主策略 |

`my.proxy.guestInterfaces` 默认包含 `virbr0`、`waydroid0`。
独立 AP 在热点启用后加入。
`my.hardening.guestNetworks.<接口>` 声明信任级别、规范且互不重叠的 `sourceCidrs`、网关地址及宿主服务端口。
内建角色默认使用 machine 中的 IPv4 子网/网关。

IPv6 需要显式 ULA 源子网。
所有模式均拒绝访客 IPv6 公网。
Wi-Fi 客户端上行不能作为访客角色。
创建 AP、DHCP 服务和网桥与安装防火墙策略仍是独立步骤。

所有角色的私网转发默认受限，热点也遵循此限制。
`guestPrivateExceptions` 按精确入口接口、私网目标 CIDR、TCP/UDP 端口授权。
开发网络可显式设 `trust="trusted"` 放行私网路由，但不会开放任意宿主服务或公网 B/C 转发。
兼容选项 `guestPrivateEgress=true` 仅信任未明确设置 `trust` 的 libvirt/Waydroid 角色，逐接口选择优先。

网关 DHCP/DNS 与宿主 loopback 解析器分开。
`hostTcpPorts` / `hostUdpPorts` 仅在该角色声明的网关地址开放服务。
旧 libvirt `guestHostServices` 与端口清单汇入同一策略。
源检查先于重定向，宿主访问检查先于 established 入站放行，当前授权同时约束既有转发连接的双向流量及宿主服务回包。
因此撤销也会切断服务端主动推送。
不同信任级别需要独立网桥：此策略识别网络，不隔离同桥二层流量，也不认证具体访客。

`transparentGuestInterfaces` 默认空，仅为健康 B 显式选择 IPv4 TPROXY 入口。
有效 fake-IP 映射需要该入口。
其它合成地址、选定元数据端点及已知转换/过渡网段仍拒绝。
C 在保留核心恢复路径的同时切断已代理访客的回包。
桥接私网授权与公网模式守卫独立。

Passt 与 rootless 网络将访客报文转换为宿主 socket，桥接规则无法据此识别容器。
对不可信后端，应以 `my.hardening.socketGuestUsers` 声明独立固定普通账户。
正常构建拒绝桌面/root/基础设施身份、共用 UID 及重叠的委派范围。
`socketGuestPrivateExceptions` 按账户、原始出口接口、私网 CIDR 和目标端口授权。

loopback/代理入口、保留代理/DNS 目标端口、fake-IP 和已知转换目标不可授权。
TCP/UDP 53 强制进入[过滤后的真实地址解析器](dns_CN.md#受限-socket-访客-dns)，避免 B 的合成地址遮蔽 UID 守卫需要检查的私网最终目标。
这些账户的 rootless Docker 启动器会清除继承的显式代理环境变量。

先安装策略，再以声明账户运行后端。
新增/更改受限账户或 UID 映射前，应停止旧后端。
回包跟踪无法追溯识别旧的未标记 socket。
在身份保持声明时撤销私网授权，会切断已跟踪连接的双向流量。
桌面账户的 rootless Docker 仍属于可信开发环境，因为它与桌面应用共用 UID。
UID 策略约束网络出站。

文件权限、共享 Unix socket、入站端口发布及同桥隔离需要另行控制。
授权匹配宿主 socket/路由的原始目标，不检查应用隧道内部的目标。
root 负责可信宿主管理，隔离核心在其传输授权内负责可信应用路由。
见[配置示例](operations_CN.md#配置入口)。

本机将 Hermes Docker 工作负载交给独立账户 `hermes-containers`，默认 UID 1200，并使用独立的 subuid/subgid 范围。
其宿主 socket 使用受限账户策略。
Hermes agent、dashboard、本地 terminal 和 MCP 进程仍按自身宿主身份运行，可在健康 B 中使用 Gost。

C 阻断它们的应用代理流量。
加入 `hermes` 组不会获得另一套网络例外。
同一后端内的容器共享该账户的网络授权。
入口、数据与服务管理见 [Hermes 容器](operations_CN.md#hermes-容器)。
