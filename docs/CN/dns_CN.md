# DNS

[English](../dns.md) | 简体中文 | [文档目录](README_CN.md)

本机在 `host.nix` 选择 `dnsEncryption="strict"` 和 `localDnssec=true`：A 由 Unbound 和 360 DoT 端点本地验证 DNSSEC；B 由核心验证器本地验证，`geosite:cn` 经 360 DoH 直连解析，其余经远程代理走 Cloudflare/Google DoH。
模块的 `localDnssec` 默认值为 `false`。
下文 opportunistic 租约及恢复额度属于模块兼容策略。
strict 不授予公共明文恢复，也不接受运行时 force-plaintext。
DoT 故障时保留缓存/本地回答。
字面 IP 门户使用独立入口。
备用旧 generation 必须显式选择，不能自动降低策略。

## 解析链路

```text
应用 -> systemd-resolved -> dnsmasq 127.0.0.1:1054
                              | 本地后缀黑名单 / 例外
                              +-> B: Mihomo 127.0.0.1:1053（fake-IP）
                              +-> A: Unbound 127.0.0.1:1055（DoT）
                              +-> 批准的 split DNS / A 中有租约的明文回退
```

[default.nix](../../system/config/network/network.nix) 配置解析服务，[upstream.nix](../../system/config/network/dns/upstream.nix) 协调 dnsmasq 的 `/run/dns-upstream/servers.conf`，以 SIGHUP 热更新，失败会保留待应用状态并重试。

内部 1053/1055 仅允许 root 诊断及 dnsmasq UID 查询，1054 面向客户端。
运行时 proxy-only 的外层边界同时阻断宿主 DoT、split/MagicDNS 路径，C 中也不例外；下述真实地址网关保留核心验证器路径。
[listeners.py](../../system/config/network/proxy/listeners.py) 共用检查 TCP/UDP 监听地址、UID 和 cgroup。

两种 IP 家族的上行明文 DNS 均重定向到 dnsmasq，包括热插拔网卡和应用硬编码的私网或门户解析器。
dnsmasq 自身上游和批准的 MagicDNS 走明确策略。
严格加密 DNS 模式下，启动防火墙在配置上行前拦截外发的 TCP/UDP 53 和 853，仅放行指定 Unbound UID 到已声明 DoT 地址的 TCP 查询。完整策略原子接管，按现行 DNS 租约拦截重定向后仍要发往普通上行的明文 DNS。
早期 853 guard 允许 root、核心 UID 加 mark 及配置的 Unbound 路径，在 auto-redirect 隐藏原端口前阻断应用独立 DoT/DoQ。
DoH 与 HTTPS 共用 443，不能据此区分。

## 各路径的责任

| 路径 | 域名过滤 | 传输与验证 |
|---|---|---|
| 系统查询，含 Gost 在 A 的直连代解析 | dnsmasq 在选定上游前执行后缀规则 | A 使用 Unbound DoT 和本地 DNSSEC。默认恢复可使用有租约的明文。B 转交核心 |
| 核心 DNS 监听与 TUN DNS hijack | 在 hosts、缓存和 fake-IP 分配前执行共享后缀规则 | 启用 `localDnssec` 时先本地验证再合成地址，否则使用受管 DoH/DoT |
| B 中 HTTP/SOCKS 代理目标 | 应用 TCP/UDP 拨号前执行共享规则，私网域名豁免也要先过过滤 | 公网及已批准内部域名均检查并固定真实 IP。`localDnssec` 还在本地验证公网答案。私网目标需实际叶节点的出口授权 |
| 节点与加密解析器引导 | 核心解析器在缓存/上游访问前执行共享过滤 | 启用 `localDnssec` 时使用独立验证器，否则使用字面地址加密引导解析器。Rules 保留独立引导传输；运行时 proxy-only 从已接受的固定节点启动，两个验证器角色均经远程组传输。内部基础设施不受应用目标 IP 检查约束 |
| 已批准 split DNS / 原生 MagicDNS | split 转发遵守 dnsmasq 域名优先级。resolved 的原生 MagicDNS 条件路由绕过 dnsmasq | 明确的私网解析器 / Tailscale 隧道例外，不提供本地公网 DNSSEC 保证 |

后缀过滤器为阻断域返回本地 NXDOMAIN。
A 由 Unbound 在本地验证 DNSSEC，B 在启用 `localDnssec` 时使用本地验证器，否则使用核心的加密解析器。
私网地址检查分别作用于 DNS 答案和应用目标。
字面 IP、应用独立 DoH、受管核心之外的远端解析及原生 MagicDNS 不在共享后缀过滤范围内。

## A：验证与恢复

### 加密上游与健康检查

Unbound 使用 360 DoT：`101.226.4.6`、`218.30.118.6`、`123.125.81.6`，认证名称 `dot.360.cn`。
本地 `validator iterator` 验证 DNSSEC，忽略客户端 CD 请求。
锁定的 `dns-root-data` 提供初始信任锚，RFC 5011 状态持久化在 `/var/lib/unbound`，启动不依赖未经认证的初始锚下载。
未签名区域不会因此获得签名。

上游私网/CGNAT、访客网段、loopback、未指定、链路本地和 ULA 答案受到过滤，包含 IPv4-mapped 形式。
明确本地数据和窄 private-domain 仍可用。
上游故障期间可在 900 秒内服务已验证缓存，应答 TTL 为 30 秒。
Split DNS 和明文回退不经过 Unbound。

[dnssec_probe.py](../../system/config/network/dns/dnssec_probe.py) 检查本地 DNSSEC 验证，以及与上游新建的、经证书认证的 TLS/DNS 交换。
仅有缓存验证成功，不能说明上游可用。
两项均通过时才报告 `secure`，本地验证成功而传输失败时报告 `unavailable`。
本地查询限时八秒。
每个上游交换共用五秒截止，覆盖 TLS 握手和完整 DNS 响应。

两个结果分别记录在 `/run/dns-upstream/state.json` 和 `/var/lib/dns-upstream/history.log` 的 `dnssec_probe.local`、`dnssec_probe.transport` 字段。
手工调用未指定传输端点时，报告 `scope=validation-only`。

### 明文恢复与额度

加密路径无法提供可用判定时，A 可用原始 DHCP4 DNS 打开有界明文窗口。
没有 DHCP DNS 时使用 `1.1.1.1`/`1.0.0.1`。
这条恢复路径与 Unbound 上游分开。

该恢复行为属于默认 `my.proxy.dnsEncryption="opportunistic"`。
网络阻断加密上游可以诱发额度内的明文恢复。
不接受这一取舍时，可独立选择下方严格策略。

| 控制 | 默认行为 |
|---|---|
| 租约 | 每次续租最多 120 秒，受剩余额度限制 |
| 会话额度 | 1800 秒。明文每使用一秒扣一秒 |
| 恢复额度 | 同一协调器、策略及网络会话下，连续验证与传输均成功的轮次最多每秒补一秒。恢复不重置额度 |
| 会话身份 | boot ID + ifindex，Wi-Fi 另加 ActiveConnection |
| Carrier / DHCP 变化 | 不产生新额度。服务重启保留记账 |
| 可选开机截止 | `my.proxy.plaintextBootBudgetSeconds`，默认 `null` |
| 显式 override | root 的 `/run/dns-upstream/force-plaintext`，删除或重启前有效 |

Override 可以超过额度，但仍记账并受单次租约续期约束。
删除不会恢复已花费时间。
C 的更早出站规则始终切断外部 DNS，不受回退选择影响。

[dns_budget.py](../../system/config/network/dns/dns_budget.py) 在 `/run/dns-upstream/budgets/` 保存本次启动的会话额度。
授权时间跨越租约到期或协调器重启时仍计入消耗，账本无效时禁止续租。
持久历史只供诊断，不能用于恢复额度。

补充额度要求同一协调器、策略和网络会话内，两次成功观测相隔不超过 90 秒。
重启、应用失败、睡眠和没有观测的时间都不增加额度。
设置 `plaintextBootBudgetSeconds` 后，补充额度也受该截止时间限制。

[dns_policy.py](../../system/config/network/dns/dns_policy.py) 是特权内核租约 broker，通过带超时的 nft set 仅允许 dnsmasq UID 到精确上游。
协调器停止或卡死不能延长过期租约。
休眠和连接变化先撤销权限，再决定续租。

### 上游配置与时间

`my.machine.dotEndpoints` 声明字面 IPv4/IPv6 端点及证书名称 `authName`。
宿主 Unbound、A 模式探针和宿主防火墙共用该列表，默认由 `dotUpstreams` 与 `dotAuthName` 派生。
应选择在目标网络中可用的端点。

`my.proxy.dnsStaleWaitMilliseconds` 限制返回有效陈旧缓存前等待新数据的时间，默认 300 ms。
核心 DNS 的 IPv6 设置跟随宿主支持。

DoT 与 NTS 依赖准确时钟。
RTC 错误时，先通过独立可信来源核对时间，在本地校时并重启加密服务。
证书时间检查保持开启，明文 DNS 策略不替代时间认证。

## 可选严格加密

```nix
my.proxy.dnsEncryption = "strict";
```

此选项控制受管的**公网 DNS 传输**，独立于 A/B/C、`bootMode`、浏览器隐私和 `proxyOnly`。
A 保留认证 DoT，B 保留受管加密解析及节点引导端点。
协调器不开明文恢复窗口并忽略 `force-plaintext`，broker 拒绝明文授权，防火墙不安装后备集合的放行规则。

旧预算、override 或残留集合元素都不能重开这条路径。
早期 DNS guard 也阻断核心的外部 TCP/UDP 53，仅保留经 tailnet 接口到指定 MagicDNS 地址的例外。

已批准私网 split DNS 和 MagicDNS 仍是有意例外，loopback DNS 是本机 IPC。
本选项不加密所有应用协议、不识别任意 HTTPS/DoH 载荷、不为未签名区域提供认证，也不约束特权 root 诊断。
[Mihomo 的解析器角色](https://wiki.metacubex.one/config/dns/)区分应用 DNS、代理端点和解析器引导。

加密 DNS 故障时，新的公网域名会失败。
本地和符合条件的缓存答案继续返回。
状态报告 `encryption_policy=strict`、`degraded-dot-only`、零窗口且无有效明文 override，就绪状态需等待加密路径恢复。
可更换上行、在 A 使用字面 HTTP 门户地址，或修复声明的认证上游。

门户若依赖不可用的公网 DNS，需要有意选择预先构建的 `opportunistic` generation。
`clash-off` 和运行时 override 都不会放宽严格策略。
参见[离线恢复](operations_CN.md#离线恢复)。

## B：受管 Mihomo DNS

[profile.nix](../../system/config/network/proxy/profile.nix) 定义受管 DNS。
TUN 模式使用 fake-IP（默认 `198.18.0.1/16`）、固定 blacklist 过滤模式和 DNS hijack。
本地域及部分连通性/STUN 名称返回真实地址。
fake-IP 过滤仅改变解析，不改变路由。
HTTP-only（`tunMode=false`）采用 redir-host。

启用 `localDnssec=true` 时，应用 DNS 转交私有 Unbound `127.0.0.1:1055`，节点和解析器引导使用独立验证器 `127.0.0.1:1056`。
两者均在核心命名空间内，与宿主相同端口号的服务相互独立。
它们分别维护由锁定 `dns-root-data` 初始化的持久 RFC 5011 信任锚，并忽略客户端 CD 请求。
每个验证器通过 TCP loopback 转交私有 Mihomo 传输监听：应用查询使用 1057，引导查询使用 1058。
这些监听传递完整 DNSSEC 材料，不使用 hosts 替换、fake-IP 合成或普通解析器缓存。

`rules` 中应用传输保留国内分流：`geosite:cn` 经 360 DoH 直连解析，其余使用 Cloudflare/Google DoH。
启用受控来源时，海外查询使用独立的 `lf-dns` 选择组，成员来自授权的 `lf-remote`。每个协议、固定服务器地址和端口组合保留一个叶子，优先排列不同服务器地址和协议的候选。
应用和 DNS 分别选叶子。`profile.store-selected` 在核心重启后恢复仍符合组成员资格的选择。
受管解析器的健康检查失败时，监督器分批抽查获授权的 DNS 叶子。同一快照和路由事务的后续探针继续抽查其他成员。已选中的可用会话可以继续等待 DNSSEC 验证预热。
未启用受控来源的配置保留 `respect-rules` 选路。
核心命名空间内 1056 端口的独立引导验证器只负责节点和解析器引导。应用域名及公网拨号目标校验由应用验证器处理。
国内 DoH 端点来自 `my.machine.mihomoDomesticValidatingDnsUpstreams`，必须回传完整 DNSSEC 材料；默认的 360 端点满足，非验证路径的 AliDNS 不满足。
海外冷查询可能需要多次远程交换以完成验证，国内查询则保持国内往返。
A 的 `my.machine.dotEndpoints` 列表与 B 的两类角色相互独立。
Mihomo 保持 TLS 证书校验，上游故障不授予明文回退。
DNS 叶子的运营方可观察 DoH 端点和时序。认证的 DoH 解析器收到海外查询，并看到 DNS 叶子的出口地址。

运行时 `proxy-only` 保留两个本地验证器及独立预算，但两类查询均通过认证的字面地址 DoH 端点，绑定到与应用相同的固定远程叶节点。
配置 [WireGuard 出口](proxy_CN.md#包含真实-ping-的-wireguard-出口)后，系统 DNS 也使用应用验证器的真实地址网关，因此域名 ping 可以通过原生 ICMP 到达目标。
该模式移除国内解析策略、直连引导及 MagicDNS 传输。
核心使用已接受并固定的节点地址启动，保留 TLS 服务名；切换无需联网获取来源或直连解析节点。
节点或验证器不可用时保持 C。

B 中 DNS 等待确认（`proxy-priming`）或探测失败（`degraded-proxy`）时，普通解析器继续将公网查询送往已选定的核心路径。
受管路径不可用时，新查询可能失败。切到 B 时也会切断 A 的应用 Unbound（1055）尚未完成的 DoT 和 loopback 回包。

1056 的真实地址过滤解析器处理订阅和 provider 主机名、用于固定端点的节点域名及受限 socket 后端查询。启用运行时切换时，该解析器使用 1062 的基础设施网关。
Rules 和 A 使用 1061 的专用宿主 Unbound，其 DoT 连接和缓存独立。代理意图为 proxy-only 时，网关通过 root 所有的 Unix socket 进入核心应用验证器。
1059 服务于 WireGuard 出口下的普通真实地址查询，在 A 和 Rules 中拒绝此类查询。
宿主 DoT、split DNS 和原生 MagicDNS 无法穿过 proxy-only 的外层防火墙。
切换时清空宿主解析缓存并重建核心验证器；受管 fake-IP 映射持久保存，使应用缓存的地址继续对应原域名。

`localDnssec=false` 时，公共解析使用 Cloudflare/Google DoH，国内域与代理端点引导使用配置的国内解析器。
该兼容路径不在本地验证 B 的 DNSSEC 答案。
两种配置均启用 `respect-rules`，禁用额外 direct 解析器和 proxy-server resolver-policy map，parser/controller 防止运行时削弱受管字段。

核心在路由后检查并固定域名拨号目标，包含已批准内部后缀。
启用本地验证后，公网拨号目标使用本地验证器确认的答案，再固定真实地址。
订阅 hosts 和系统 hosts 不能替换为未经验证的公网答案。
字面及解析后的私网目标还需本地 DIRECT 或限定远程叶节点的授权。
新 UDP 映射重新验证，缓存映射不重解析，但每包重新检查出口和端口授权。
[目标授权策略](proxy_CN.md#域名目标)与 DNS 答案过滤是不同层。
DNS 或后缀例外不授予远程内部访问。

公网拨号目标检查合并同名并发查询，在共用的三秒截止内并行查询 A/AAAA。
一个地址族已有可用的验证结果时，不会等待另一族一直超时。
成功结果最多复用十秒，且不超过验证器返回的 TTL，包含 CNAME 的 TTL。
零 TTL 结果和失败不进入缓存。
缓存与在途工作归各自策略所有，替换策略不能复用旧答案或未完成的查询。

受管 fake-IP 模式保留地址记录的合成行为，同时让 HTTPS/SVCB 查询使用声明的解析器，保留服务参数。
两者均先经过共享过滤。
本地验证在公网 fake-IP 分配前执行，保留 bogus 失败、已签名 NXDOMAIN 和 NODATA，包含末端缺少所请求地址类型的 CNAME 链。
合成地址清除 AD，因为 fake 映射本身没有签名。
未签名答案仍未经认证，明确的私网域名例外保持独立范围。
受管 DNS 拒绝包含多个 question 的查询。
Firefox 如何使用这些记录、CONNECT 限制和可选原生路径见[浏览器手册](browser_CN.md#ech-发现与代理兼容)。

### 本地验证与角色预算

```nix
my.proxy.dnsEncryption = "strict";
my.proxy.localDnssec = true;
my.proxy.dnsQueryBudget = {
  application = 256;
  infrastructure = 64;
};
```

本地验证要求严格加密和 `coreNetns=true`。
示例查询预算为模块默认值，允许范围分别为 32–4096 和 8–1024。
两个验证器各自拥有查询表、缓存、文件描述符和内存上限，应用超载不会消耗引导验证器的预算。
应用验证器为 DNS 消息和 RRset 分别分配 16 MiB 和 32 MiB，在热门答案临近到期时预取，并可在过期后的 300 秒内返回符合条件的答案。
`my.proxy.dnsStaleWaitMilliseconds` 指定等待新答案的时间，默认 300 毫秒。过期应答的 TTL 最多为 30 秒，新记录仍遵循上游 TTL 上限。
引导验证器为消息和 RRset 分别分配 4 MiB 和 8 MiB，只返回未过期的答案。
超限请求可以被拒绝，在途工作结束后容量可重新使用。
A 的宿主验证器和受限 socket 访客 DNS 仍相互独立。
这些控制隔离 DNS 角色，不对每个应用设置 conntrack、UDP association 或代理队列配额。

拨号目标守卫另有固定上限：最多缓存 256 个名称，并处理 256 个不同名称的在途查询。
这些上限不随验证器查询预算调整。

只有验证器 UID 从私有 TUN 排除，其命名空间防火墙仅允许查询私有 TCP 传输监听及本地回包。
验证器自身不能向外发送 DNS 流量。
应用 DIRECT 目标不能借用验证器或传输端口。
验证器故障会使新的公网解析失败，不回退到明文或应用直连。

启用本地验证需要重新构建 generation 并重启受管服务。
已知的旧版受管 DNS 和 TUN 值会迁移到新的 root 策略。
外来值及削弱策略的 controller 写入仍被拒绝。
回到兼容路径需要在重新构建的 generation 中显式选择 `localDnssec=false`。

## 受限 socket 访客 DNS

`my.hardening.socketGuestUsers` 非空或启用受控来源时，额外的 dnsmasq 以专用 `socket-guest-dns` 用户（UID 978）监听 loopback 1056。[解析服务](../../system/config/network/dns/socket-guest.nix)
它复用不可变后缀过滤、增补及例外并返回真实地址，不使用明文租约、split DNS 或原生 MagicDNS。
未启用运行时切换时直接转交 1061 的基础设施 Unbound。

A 和 Rules 中，基础设施 DoT 不可用时，新鲜公网查询失败。`unboundDotInProxy=false` 在 Rules 中关闭该传输。
符合条件的缓存和本地数据继续返回。

声明的主 UID 和 subordinate UID 的 TCP/UDP 53 在核心 auto-redirect 前重定向至此。
NAT 后检查拒绝仍指向旧解析器的 conntrack 关联。
Root 可以直接查询 1056，启用受控来源时也允许专用来源下载账户 `clash-fetch` 查询。
私网目标守卫拒绝直接使用 fake-IP/已知转换目标及宿主代理入口。
独立 HTTPS/DoH 内容不受后缀过滤，但最终字面 socket 目标仍受账户策略约束。

C 同时切断这些账户已跟踪的回包及公网出站。
此路径属于[宿主 socket 访客授权](network_CN.md#访客与容器)，普通桌面解析器与桥接网关 DNS 仍有独立路径。

## 黑名单

[blocklist.nix](../../system/config/network/dns/blocklist.nix) 在构建时解压 Git 跟踪的[快照](../../local-data/dns-blocklist.txt.xz)，同一份不可变域名文件同时生成 dnsmasq 后缀 NXDOMAIN 规则并供核心加载内存过滤表。
构建和运行时均不下载。

`my.proxy.blockedDomains` 添加本机名字。
`blockedDomainExceptions` 同时生成 `server=/domain/#` 和核心例外。
两者均按标签边界、不区分大小写匹配后缀，最精确条目优先，同精度时例外优先。
因此可以在例外子树内再次声明更窄的阻断。
例外仅允许解析，不关闭路由或私网 IP 检查。

显式 split-server 也参与 [dnsmasq 域名优先级](https://dnsmasq.org/docs/dnsmasq-man.html)，批准的私网命名空间可以覆盖更宽泛的阻断。

核心的稳定路径 `/etc/netsec/dns-blocklist.txt`、附加名单和例外由 root 受管策略决定。
该路径指向构建时校验的不可变快照，内容校验独立于订阅策略身份。
GUI 配置在解析时继承它们，控制 API 不能替换。
名单缺失或无效时拒绝新配置并保留已有过滤状态。
应用拨号先过域名过滤，再判断内部后缀权限，不能用该权限绕过阻断。
内部解析器查询也共享过滤。
显式 hosts/IP 基础设施映射仍有独立用途。
命中名单的代理端点名同样返回阻断答案。
确认有意信任该端点时，用窄范围 `blockedDomainExceptions` 声明例外。

用 [refresh-dns-blocklist.py](../../scripts/refresh-dns-blocklist.py) 刷新；`--check` 校验已提交的快照、manifest 和头部而不写入，加 `--fetch` 再与上游来源比较。
校验不一致会让配置构建失败，而不是生成不完整的过滤名单。
重新构建并重启解析器/核心 generation 后生效，保留 DNS 或连接缓存的客户端也需重启。
既有连接不会被追溯检查。
来源和快照信息见模块头及 [local-data 手册](../../local-data/README.md)。

## Wi-Fi、split DNS 与 MagicDNS

NetworkManager 上行 DNS 改写将原有的六个 DNS 属性备份到 root 私有的 `/var/lib/netsec-dns-profiles/`。
Dispatcher 在首次改写 profile 前发布完整备份并同步所在目录。
运行 `sudo proxy-dns-profile plan <UUID>` 可查询备份路径、状态及是否可恢复。
`sudo proxy-dns-profile restore <UUID>` 恢复这些字段且不重连，并拒绝覆盖期间的人工修改。
如果计划显示 `backup_status: invalid`，保留该文件以供检查，并从已确认有效的 NetworkManager 备份核对原始字段。
备份无效期间，dispatcher 和恢复命令都拒绝改写 profile。
批准已有备份的 profile 使用 split DNS 时会恢复其原始 DNS 值。
备份不包含凭据、路由和身份字段。

[nm_dns.py](../../system/config/network/dns/nm_dns.py) 为 NetworkManager 受管上行的两种 IP 家族禁用自动 DNS，清除非预期显式服务器和搜索域，并以 `~.` 将 resolved 固定到本机网关。
明文回退读取原始 DHCP 数据，不读取已改写的有效 DNS 列表。
有线 dhcpcd 独立处理。

`my.proxy.splitDnsProfileUuids` 默认空。
批准 profile 可提供已由共享[直连路由策略](egress_CN.md)排除在 TUN 外的私网/ULA 条件解析器（含受限 B），dnsmasq 绑定 Wi-Fi 接口查询。
租约绑定 UUID、ActiveConnection、ifindex、carrier-down 计数和撤销代次，最迟在原观测后 120 秒到期，dispatcher 转换时撤销。

公网解析器、不可读或不支持的 profile 进入 `split-error`，不会打开默认转发。
本地监听身份失败进入 `resolver-error`，也不能开启明文回退。

[resolved_policy.py](../../system/config/network/dns/resolved_policy.py) 将受管 TUN 排除在 resolved 默认选择外。
Tailscale 自动 DNS/路由关闭。resolved 在尾网接口登记当前已认证的后缀，设 `DefaultRoute=no`。Rules B 的核心也用同一后缀，在查询公网 DNS 缓存或生成 fake-IP 之前选择 MagicDNS。其他 `.ts.net` 名称走普通受管 DNS。
核心返回 MagicDNS 记录时跳过公网 DNSSEC 验证。解析路由绑定已认证的账户、尾网和当前接口实例。确认切换或退出后撤销旧路由。短暂状态超时可用完现有 135 秒授权的剩余时间，计时包含休眠。唤醒后需取得新的观测。
防火墙按接口实例和到期时间约束发往 MagicDNS 解析器的流量。退出、到期或更换接口后，本次启动中曾认证的后缀仍只在本机处理。

字面 peer 地址和验证后的后缀保留有意的私网访问。
宿主网关绑定本地解析端点。
对 LAN/tailnet 客户端提供 DNS 服务需单独配置监听、客户端 ACL 与应答选路。
