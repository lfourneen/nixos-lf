# DNS

[English](../dns.md) | 简体中文 | [文档目录](README_CN.md)

本机在 `host.nix` 选择 `dnsEncryption="strict"`；下文 opportunistic 租约及恢复额度属于模块兼容策略。strict 不授予公共明文恢复，也不接受运行时 force-plaintext。DoT 故障时保留缓存/本地回答；字面 IP 门户使用独立入口。备用旧 generation 必须显式选择，不能自动降低策略。

## 解析链路

```text
应用 -> systemd-resolved -> dnsmasq 127.0.0.1:1054
                              | 本地后缀黑名单 / 例外
                              +-> B: Mihomo 127.0.0.1:1053（fake-IP）
                              +-> A/C: Unbound 127.0.0.1:1055（DoT）
                              +-> 批准的 split DNS / A 中有租约的明文回退
```

[default.nix](../../system/config/network/network.nix) 配置解析服务，[upstream.nix](../../system/config/network/dns/upstream.nix) 协调 dnsmasq 的 `/run/dns-upstream/servers.conf`，以 SIGHUP 热更新，失败会保留待应用状态并重试。内部 1053/1055 仅允许 root 诊断及 dnsmasq UID 查询，1054 面向客户端。[listeners.py](../../system/config/network/proxy/listeners.py) 共用检查 TCP/UDP 监听地址、UID 和 cgroup。

上行口的明文 DNS 对两种 IP 家族均重定向到 dnsmasq，包括硬编码的私网/门户解析器。dnsmasq 自身上游和批准的 MagicDNS 走明确策略。早期 853 guard 允许 root、核心 UID 加 mark 及配置的 Unbound 路径，在 auto-redirect 隐藏原端口前阻断应用独立 DoT/DoQ；DoH 与 HTTPS 共用 443，不能据此区分。

## 各路径的责任

| 路径 | 域名过滤 | 传输与验证 |
|---|---|---|
| 系统查询，含 Gost 在 A 的直连代解析 | dnsmasq 在选定上游前执行后缀规则 | A 使用 Unbound DoT 和本地 DNSSEC；默认恢复可使用有租约的明文；B 转交核心 |
| 核心 DNS 监听与 TUN DNS hijack | 在 hosts、缓存和 fake-IP 分配前执行共享后缀规则 | 受管 DoH/DoT；B 不运行 A 的本地 DNSSEC 验证器 |
| B 中 HTTP/SOCKS 代理目标 | 应用 TCP/UDP 拨号前执行共享规则，私网域名豁免也要先过过滤 | 公网及已批准内部域名均验证并固定真实 IP；私网目标还需实际叶节点的出口授权 |
| 节点与加密解析器引导 | 核心解析器在缓存/上游访问前执行共享过滤 | 独立的字面地址加密引导解析器避免依赖已连通代理；内部基础设施不受应用目标 IP 和 `proxyOnly` 检查约束 |
| 已批准 split DNS / 原生 MagicDNS | split 转发遵守 dnsmasq 域名优先级；resolved 的原生 MagicDNS 条件路由绕过 dnsmasq | 明确的私网解析器 / Tailscale 隧道例外，不提供本地公网 DNSSEC 保证 |

后缀过滤器为阻断域返回本地 NXDOMAIN。A 由 Unbound 在本地验证 DNSSEC，B 使用核心的加密解析器；私网地址检查分别作用于 DNS 答案和应用目标。字面 IP、应用独立 DoH、受管核心之外的远端解析及原生 MagicDNS 不在共享后缀过滤范围内。

## A：验证与恢复

Unbound 使用 360 DoT：`101.226.4.6`、`218.30.118.6`、`123.125.81.6`，认证名称 `dot.360.cn`。本地 `validator iterator` 验证 DNSSEC，忽略客户端 CD 请求。锁定的 `dns-root-data` 提供初始信任锚，RFC 5011 状态持久化在 `/var/lib/unbound`，启动不依赖未经认证的初始锚下载。未签名区域不会因此获得签名。

上游私网/CGNAT、访客网段、loopback、未指定、链路本地和 ULA 答案受到过滤，包含 IPv4-mapped 形式；明确本地数据和窄 private-domain 仍可用。上游故障期间可在 900 秒内服务已验证缓存，应答 TTL 为 30 秒。Split DNS 和明文回退不经过 Unbound。

[dnssec_probe.py](../../system/config/network/dns/dnssec_probe.py) 分别报告本地验证和加密传输。每轮健康检查既向本地查询根下随机新名字，也向配置的上游新建经过证书认证的 TLS/DNS 连接。正缓存及符合条件的过期缓存提供本地答案，aggressive NSEC 缓存还会合成认证否定答案。探针分别记录本地验证结果和新建 TLS 交换结果。整体 `secure` 必须同时满足本地验证成功和传输可用。验证错误保留独立分类；本地缓存验证成功但传输失败时，整体为 `unavailable`。每个上游交互共用五秒截止，覆盖握手和完整响应读取；本地查询限八秒，首个可用上游结束传输阶段。

`/run/dns-upstream/state.json` 与 `/var/lib/dns-upstream/history.log` 的 `dnssec_probe.local`、`dnssec_probe.transport` 保留两层观测。手工调用未指定传输端点时明确标为 `scope=validation-only`；生产协调器始终传入端点。只有传输正常时才推迟冷启动信任锚 priming 判定。

加密路径无法提供可用判定时，A 可用原始 DHCP4 DNS 打开有界明文窗口；没有 DHCP DNS 时使用 `1.1.1.1`/`1.0.0.1`。这条恢复路径与 Unbound 上游分开。

该恢复行为属于默认 `my.proxy.dnsEncryption="opportunistic"`。网络阻断加密上游可以诱发额度内的明文恢复；不接受这一取舍时，可独立选择下方严格策略。

| 控制 | 默认行为 |
|---|---|
| 租约 | 每次续租最多 120 秒，受剩余额度限制 |
| 会话额度 | 1800 秒；明文每使用一秒扣一秒 |
| 恢复额度 | 同一协调器、策略及网络会话下，连续验证与传输均成功的轮次最多每秒补一秒；恢复不重置额度 |
| 会话身份 | boot ID + ifindex，Wi-Fi 另加 ActiveConnection |
| Carrier / DHCP 变化 | 不产生新额度；服务重启保留记账 |
| 可选开机截止 | `my.proxy.plaintextBootBudgetSeconds`，默认 `null` |
| 显式 override | root 的 `/run/dns-upstream/force-plaintext`，删除或重启前有效 |

Override 可以超过额度，但仍记账并受单次租约续期约束；删除不会恢复已花费时间。C 的更早出站规则始终切断外部 DNS，不受回退选择影响。

[dns_budget.py](../../system/config/network/dns/dns_budget.py) 在 `/run/dns-upstream/budgets/` 维护会话账本。租约发放以余额、结算时刻和授权截止时间原子落盘为前提。扣费取未结算时间与租约区间的交集，覆盖跨租约到期和协调器重启的时间。策略应用失败或结果不明时保留账本中的截止时间，确认撤权才截短。账本无效时禁止续租。

补充额度要求同一协调器、策略和网络会话内的两次成功观测相隔不超过 90 秒。重启、应用失败、长期未观测和睡眠都不赚取额度；启动时钟与清醒时钟的差值用于识别睡眠。可选启动截止也约束补充额度。账本位于 `/run`，只覆盖本次启动；持久历史用于诊断，不作为余额账本。

[dns_policy.py](../../system/config/network/dns/dns_policy.py) 是特权内核租约 broker，通过带超时的 nft set 仅允许 dnsmasq UID 到精确上游。协调器停止或卡死不能延长过期租约；休眠和连接变化先撤销权限，再决定续租。

`my.machine.dotEndpoints` 分别声明 IPv4/IPv6 字面地址及证书名称 `authName`，Unbound、独立探针和防火墙使用同一列表；默认由 `dotUpstreams` 与 `dotAuthName` 派生。独立运营商的端点应按目标接入网络的测量选择。`my.proxy.dnsStaleWaitMilliseconds` 限制等待新数据再返回有效陈旧缓存的时间，默认 300 ms。核心 DNS 的 IPv6 设置跟随宿主 IPv6 支持。

DoT 与 NTS 依赖准确时钟。RTC 错误时，先通过独立可信来源核对时间，在本地校时并重启加密服务。证书时间检查保持开启，明文 DNS 策略不替代时间认证。

## 可选严格加密

```nix
my.proxy.dnsEncryption = "strict";
```

此选项控制受管的**公网 DNS 传输**，独立于 A/B/C、`bootMode`、浏览器隐私和 `proxyOnly`。A 保留认证 DoT，B 保留受管加密解析及节点引导端点。协调器不开明文恢复窗口并忽略 `force-plaintext`，broker 拒绝明文授权，防火墙不安装后备集合的放行规则。旧预算、override 或残留集合元素都不能重开这条路径。早期 DNS guard 也阻断核心的外部 TCP/UDP 53，仅保留经 tailnet 接口到指定 MagicDNS 地址的例外。

已批准私网 split DNS 和 MagicDNS 仍是有意例外，loopback DNS 是本机 IPC。本选项不加密所有应用协议、不识别任意 HTTPS/DoH 载荷、不为未签名区域提供认证，也不约束特权 root 诊断。[Mihomo 的解析器角色](https://wiki.metacubex.one/config/dns/)区分应用 DNS、代理端点和解析器引导。

加密 DNS 故障时，新的公网域名会失败；本地和符合条件的缓存答案继续返回。状态报告 `encryption_policy=strict`、`degraded-dot-only`、零窗口且无有效明文 override，就绪状态需等待加密路径恢复。可更换上行、在 A 使用字面 HTTP 门户地址，或修复声明的认证上游。门户若依赖不可用的公网 DNS，需要有意选择预先构建的 `opportunistic` generation；`clash-off` 和运行时 override 都不会放宽严格策略。参见[离线恢复](operations_CN.md#离线恢复)。

## B：受管 Mihomo DNS

[profile.nix](../../system/config/network/proxy/profile.nix) 定义受管 DNS。TUN 模式使用 fake-IP（默认 `198.18.0.1/16`）、固定 blacklist 过滤模式和 DNS hijack。本地域及部分连通性/STUN 名称返回真实地址；fake-IP 过滤仅改变解析，不改变路由。HTTP-only（`tunMode=false`）采用 redir-host。

公共解析使用 Cloudflare/Google DoH，国内域与代理端点引导使用配置的国内解析器。启用 `respect-rules`，禁用额外 direct 解析器和 proxy-server resolver-policy map；核心 parser/controller 防止运行时削弱受管字段。B 与 A 的本地 Unbound 验证器分开。

核心在路由后检查并固定域名拨号目标，包含已批准内部后缀。字面及解析后的私网目标还需本地 DIRECT 或限定远程叶节点的授权。新 UDP 映射重新验证，缓存映射不重解析，但每包重新检查出口和端口授权。[目标授权策略](proxy_CN.md#域名目标)与 DNS 答案过滤是不同层；DNS 或后缀例外不授予远程内部访问。

受管 fake-IP 模式保留地址记录的合成行为，同时让 HTTPS/SVCB 查询使用声明的解析器，保留服务参数；两者均先经过共享过滤。Firefox 如何使用这些记录、CONNECT 限制和可选原生路径见[浏览器手册](browser_CN.md#ech-发现与代理兼容)。

## 受限 socket 访客 DNS

`my.hardening.socketGuestUsers` 非空时，[socket-guest.nix](../../system/config/network/dns/socket-guest.nix) 以 UID 985 在 loopback 1056 启动额外 dnsmasq。它复用不可变后缀过滤、增补及例外，但始终转交 Unbound 1055 并返回真实地址，不使用明文租约、split DNS 或原生 MagicDNS。DoT 不可用（包括 B 中 `unboundDotInProxy=false`）时，新鲜公网名称失败；符合条件的缓存和本地数据继续返回。

声明的主 UID 和 subordinate UID 的 TCP/UDP 53 在核心 auto-redirect 前重定向至此。NAT 后检查拒绝仍指向旧解析器的 conntrack 关联，直接查询 1056 仍仅限 root。私网目标守卫拒绝直接使用 fake-IP/已知转换目标及宿主代理入口。独立 HTTPS/DoH 内容不受后缀过滤，但最终字面 socket 目标仍受账户策略约束。C 同时切断这些账户已跟踪的回包及公网出站。此路径属于[宿主 socket 访客授权](network_CN.md#访客与容器)，普通桌面解析器与桥接网关 DNS 仍有独立路径。

## 黑名单

[blocklist.nix](../../system/config/network/dns/blocklist.nix) 在构建时解压 Git 跟踪的[快照](../../local-data/dns-blocklist.txt.xz)，同一份不可变域名文件同时生成 dnsmasq 后缀 NXDOMAIN 规则并供核心加载内存过滤表。构建和运行时均不下载。

`my.proxy.blockedDomains` 添加本机名字；`blockedDomainExceptions` 同时生成 `server=/domain/#` 和核心例外。两者均按标签边界、不区分大小写匹配后缀，最精确条目优先，同精度时例外优先。因此可以在例外子树内再次声明更窄的阻断。例外仅允许解析，不关闭路由或私网 IP 检查。显式 split-server 也参与 [dnsmasq 域名优先级](https://dnsmasq.org/docs/dnsmasq-man.html)，批准的私网命名空间可以覆盖更宽泛的阻断。

核心的文件路径、附加名单和例外由 root 受管策略决定，GUI 配置在解析时继承它们，控制 API 不能替换。名单缺失或无效时拒绝新配置并保留已有过滤状态。应用拨号先过域名过滤，再判断内部后缀权限，不能用该权限绕过阻断。内部解析器查询也共享过滤；显式 hosts/IP 基础设施映射仍有独立用途。命中名单的代理端点名同样返回阻断答案；确认有意信任该端点时，用窄范围 `blockedDomainExceptions` 声明例外。

用 [refresh-dns-blocklist.py](../../scripts/refresh-dns-blocklist.py) 刷新，`--check` 仅报告漂移。重新构建并重启解析器/核心 generation 后生效，保留 DNS 或连接缓存的客户端也需重启；既有连接不会被追溯检查。来源和快照信息见模块头及 [local-data 手册](../../local-data/README.md)。

## Wi-Fi、split DNS 与 MagicDNS

Wi-Fi DNS 改写仅将六个 DNS 属性备份到 root 私有的 `/var/lib/netsec-dns-profiles/`。`sudo proxy-dns-profile plan <UUID>` 查看备份及可恢复状态，`restore <UUID>` 恢复这些字段且不重连。恢复拒绝覆盖期间的人工修改；批准已有备份的 profile 使用 split DNS 时会恢复其原始 DNS 值。备份不包含凭据、路由和身份字段。

[nm_dns.py](../../system/config/network/dns/nm_dns.py) 为受管 Wi-Fi 的两种 IP 家族禁用自动 DNS，清除非预期显式服务器/搜索域，并以 `~.` 将 resolved 固定到本机网关。明文回退读取原始 DHCP 数据，不读取已改写的有效 DNS 列表；有线 dhcpcd 独立处理。

`my.proxy.splitDnsProfileUuids` 默认空。批准 profile 可提供已由共享[直连路由策略](egress_CN.md)排除在 TUN 外的私网/ULA 条件解析器（含受限 B），dnsmasq 绑定 Wi-Fi 接口查询。租约绑定 UUID、ActiveConnection、ifindex、carrier-down 计数和撤销代次，最迟在原观测后 120 秒到期，dispatcher 转换时撤销。公网解析器、不可读或不支持的 profile 进入 `split-error`，不会打开默认转发。本地监听身份失败进入 `resolver-error`，也不能开启明文回退。

[resolved_policy.py](../../system/config/network/dns/resolved_policy.py) 将受管 TUN 排除在 resolved 默认选择外。Tailscale 自动 DNS/路由关闭，仅已认证 tailnet 的当前 MagicDNS 后缀条件路由到其解析器，设 `DefaultRoute=no`。字面 peer 地址和验证后的后缀保留有意的私网访问。宿主网关绑定本地解析端点；对 LAN/tailnet 客户端提供 DNS 服务需单独配置监听、客户端 ACL 与应答选路。
