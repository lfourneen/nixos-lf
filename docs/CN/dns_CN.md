# DNS

[English](../dns.md) | 简体中文 | [文档目录](README_CN.md)

本机在 `host.nix` 里选用了 `dnsEncryption="strict"` 和 `localDnssec=true`：**A** 模式下由 Unbound 走 360 DoT 端点在本地验证 DNSSEC；**B** 模式下由核心验证器在本地验证，`geosite:cn` 经 360 DoH 直连解析，其余域名则通过远程代理走 Cloudflare/Google DoH。模块的 `localDnssec` 默认值是 `false`，下文提到的 opportunistic 租约和恢复额度也只在非严格模式下生效——`strict` 既不开放公网明文恢复，也不认运行时 `force-plaintext` override，DoT 挂了就只留缓存和本地回答（字面 IP 门户走独立入口，备用旧 generation 也必须显式切换，不会自动降级）。

## 解析链路

```text
应用 -> systemd-resolved -> dnsmasq 127.0.0.1:1054
                              | 本地后缀黑名单 / 例外
                              +-> B: Mihomo 127.0.0.1:1053（fake-IP）
                              +-> A: Unbound 127.0.0.1:1055（DoT）
                              +-> 批准的 split DNS / A 中有租约的明文回退
```

[default.nix](../../system/config/network/network.nix) 负责拉起解析服务，[upstream.nix](../../system/config/network/dns/upstream.nix) 维护 `dnsmasq` 的 `/run/dns-upstream/servers.conf` 并用 `SIGHUP` 热更新（失败会留着待应用状态继续重试）。内部 1053/1055 端口只允许 root 诊断和 `dnsmasq` UID 查询，1054 面向客户端；在运行时 `proxy-only` 下，外层边界还会一并切断宿主 DoT 和 split/MagicDNS 路径（**C** 下也不例外），只留下面提到的真实地址网关继续走核心验证器。共用的 TCP/UDP 监听地址、UID 和 cgroup 检查都放在 [listeners.py](../../system/config/network/proxy/listeners.py) 里。

两种 IP 家族的上行明文 DNS 都会被重定向进 `dnsmasq`（包括热插拔网卡以及应用硬编码的私网或门户解析器），只有 `dnsmasq` 自己的上游和已批准的 MagicDNS 走明确策略。在严格加密 DNS 模式下，启动防火墙在物理上行起来前就会先拦住外发的 TCP/UDP 53 和 853（只放行指定 Unbound UID 连向声明 DoT 地址的 TCP 查询），随后完整策略原子接管，按现行 DNS 租约拦住重定向后仍想直奔普通上行的明文 DNS。早期 853 guard 只放行 root、带 mark 的核心 UID 以及配置的 Unbound 路径，在 `auto-redirect` 掩盖原端口前就把应用私自走的 DoT/DoQ 拦下来（DoH 与 HTTPS 共用 443，没法靠这条规则区分）。

## 各路径的责任

| 路径 | 域名过滤 | 传输与验证 |
|---|---|---|
| 系统查询（含 Gost 在 **A** 的直连代解析） | `dnsmasq` 在选定上游前执行后缀规则 | **A** 使用 Unbound DoT 和本地 DNSSEC 验证（默认恢复可使用有租约的明文）；**B** 转交核心 |
| 核心 DNS 监听与 TUN DNS hijack | 在 hosts、缓存和 fake-IP 分配前执行共享后缀规则 | 启用 `localDnssec` 时先本地验证再合成地址，否则使用受管 DoH/DoT |
| **B** 中 HTTP/SOCKS 代理目标 | 应用 TCP/UDP 拨号前执行共享规则（私网域名豁免也要先过过滤） | 公网及已批准内部域名均检查并固定真实 IP（`localDnssec` 还在本地验证公网答案）；私网目标需实际叶节点的出口授权 |
| 节点与加密解析器引导 | 核心解析器在缓存/上游访问前执行共享过滤 | 启用 `localDnssec` 时使用独立验证器，否则使用字面地址加密引导解析器。`rules` 保留独立引导传输；运行时 `proxy-only` 从已接受的固定节点启动，两个验证器角色均经远程组传输。内部基础设施不受应用目标 IP 检查约束 |
| 已批准 split DNS / 原生 MagicDNS | split 转发遵守 `dnsmasq` 域名优先级；`resolved` 的原生 MagicDNS 条件路由绕过 `dnsmasq` | 明确的私网解析器 / Tailscale 隧道例外，不提供本地公网 DNSSEC 保证 |

后缀过滤器命中阻断域名时直接返回本地 NXDOMAIN。**A** 由 Unbound 在本地验证 DNSSEC，**B** 开启 `localDnssec` 时走本地验证器、否则走核心加密解析器；私网地址检查分别作用于 DNS 答案和应用目标。至于字面 IP、应用独立 DoH、受管核心之外的远端解析以及原生 MagicDNS，都不在共享后缀过滤的管辖范围内。

## A：验证与恢复

### 加密上游与健康检查

Unbound 使用 360 DoT 端点（`101.226.4.6`、`218.30.118.6`、`123.125.81.6`，认证名 `dot.360.cn`）。本地 `validator iterator` 会验证 DNSSEC 并忽略客户端的 CD 请求，初始信任锚由锁定的 `dns-root-data` 写入 `/var/lib/unbound` 下的持久 RFC 5011 状态，启动时不依赖未经认证的初始锚下载（未签名区域仍然保持未签名）。

上游返回的私网/CGNAT、访客网段、loopback、未指定、链路本地和 ULA 答案（含 IPv4-mapped 形式）都会被过滤掉，明确的本地数据和窄范围 `private-domain` 仍可正常使用。上游挂掉时，已验证缓存最长可继续服务 900 秒（应答 TTL 设为 30 秒）；split DNS 和明文回退不经过 Unbound。

[dnssec_probe.py](../../system/config/network/dns/dnssec_probe.py) 同时检查本地 DNSSEC 验证（限时 8 秒）和一次与上游新建、经过证书认证的 TLS/DNS 交换（每次共享 5 秒截止）——光有缓存命中不算上游可用。两项都过才报 `secure`，本地过而传输挂了报 `unavailable`，两个结果分别写进 `/run/dns-upstream/state.json` 和 `/var/lib/dns-upstream/history.log` 的 `dnssec_probe.local` 与 `dnssec_probe.transport`。在 **A** 下，如果认证 DoT 连续两次可达而本地验证器超时或只回无 EDE 的 `SERVFAIL`，后台会重启宿主 Unbound 一次（两次至少间隔一分钟），健康状态仍按当前 DNS 策略判定；手工调用但不指定传输端点时报告 `scope=validation-only`。

### 明文恢复与额度

在默认的 `my.proxy.dnsEncryption="opportunistic"` 下，如果加密路径给不出可用判定，**A** 可以用原始 DHCP4 DNS（没有就用 `1.1.1.1`/`1.0.0.1`）开一个有界明文窗口，走的是与 Unbound 分开的独立恢复路径。注意这意味着网络如果屏蔽了加密上游，就能在额度内诱发明文恢复——不能接受这个取舍的话，就用下面的严格策略。

| 控制 | 默认行为 |
|---|---|
| 租约 | 每次续租最多 120 秒，受剩余额度限制 |
| 会话额度 | 1800 秒，明文每用一秒扣一秒 |
| 恢复额度 | 同一协调器、策略及网络会话下，连续验证与传输均成功的轮次最多每秒补一秒（恢复不重置额度） |
| 会话身份 | boot ID + ifindex，Wi-Fi 另加 ActiveConnection |
| Carrier / DHCP 变化 | 不产生新额度，服务重启保留记账 |
| 可选开机截止 | `my.proxy.plaintextBootBudgetSeconds`，默认 `null` |
| 显式 override | root 的 `/run/dns-upstream/force-plaintext`，删除或重启前有效 |

Override 可以超出额度，但照样记账并受单次租约续期约束，删掉文件也不会退回已花掉的时间；而且在 **C** 下，更早的出站规则无论如何都会切断外部 DNS。

[dns_budget.py](../../system/config/network/dns/dns_budget.py) 在 `/run/dns-upstream/budgets/` 里维护本次启动的会话额度——跨越租约到期或协调器重启的授权时间都算消耗，账本坏了就禁止续租，持久历史只作诊断不拿来回血。只有在同一协调器、策略和网络会话内、两次成功观测相隔不超过 90 秒时才会补额度（重启、应用失败、睡眠和没观测到的时间都不算，设了 `plaintextBootBudgetSeconds` 也会受它封顶）。特权内核租约 broker [dns_policy.py](../../system/config/network/dns/dns_policy.py) 则通过带超时的 `nft` set 只放行 `dnsmasq` UID 连向精确上游，哪怕协调器卡死或挂掉也拖不过到期时间，休眠和连接变化也会先撤权再决定要不要续租。

### 上游配置与时间

`my.machine.dotEndpoints` 声明字面 IPv4/IPv6 端点和证书名 `authName`（默认由 `dotUpstreams` 与 `dotAuthName` 派生），宿主 Unbound、A 模式探针和宿主防火墙共用这一份列表，记得选目标网络里能连上的端点。`my.proxy.dnsStaleWaitMilliseconds`（默认 300 ms）限制返回符合条件的陈旧缓存前愿意等新数据多久，核心 DNS 的 IPv6 支持跟随宿主设置。

DoT 和 NTS 都指望准确时钟：如果 RTC 错了，先找独立可信来源核对时间、在本地校好表，再重启加密服务。证书时间校验始终开启，明文 DNS 策略代替不了时间认证。

## 可选严格加密

```nix
my.proxy.dnsEncryption = "strict";
```

这个选项只管受管的**公网 DNS 传输**，和 A/B/C、`bootMode`、浏览器隐私以及 `proxyOnly` 相互独立。**A** 保留认证 DoT，**B** 保留受管加密解析和节点引导端点，而协调器不再开明文恢复窗口且直接无视 `force-plaintext`，broker 拒绝明文授权，防火墙也不装后备集合的放行规则（旧预算、override 或残留集合元素都撬不开这条路）。早期 DNS guard 还会把核心外部 TCP/UDP 53 端口一并封死，只留经 tailnet 接口去往指定 MagicDNS 地址的例外。

已批准的私网 split DNS 和 MagicDNS 仍是有意留下的例外，loopback DNS 也仍是本机 IPC。注意这并不负责加密所有应用协议、识别任意 HTTPS/DoH 载荷、给未签名区域做认证，也不限制特权 root 诊断（[Mihomo 的解析器角色](https://wiki.metacubex.one/config/dns/)把应用 DNS、代理端点和解析器引导分得很清楚）。

加密 DNS 挂掉时，新的公网域名解析会失败，但本地和符合条件的缓存答案仍会继续返回；状态会报 `encryption_policy=strict`、`degraded-dot-only`、零窗口且无有效明文 override，直到加密路径恢复才算就绪。真遇到这种情况，可以换上行、在 **A** 里用字面 HTTP 门户地址，或者修好声明的认证上游；要是门户非得依赖不可用的公网 DNS，那就只能主动重启进预先构建好的 `opportunistic` generation（`clash-off` 和运行时 override 都放宽不了严格策略，见[离线恢复](operations_CN.md#离线恢复)）。

## B：受管 Mihomo DNS

受管 DNS 定义在 [profile.nix](../../system/config/network/proxy/profile.nix)。TUN 模式用 fake-IP（默认 `198.18.0.1/16`）、固定 blacklist 过滤模式和 DNS hijack，本地域及部分连通性/STUN 名称返回真实地址（fake-IP 过滤只改解析不改路由）；HTTP-only 模式（`tunMode=false`）则用 `redir-host`。

启用 `localDnssec=true` 后，应用 DNS 转交给 `127.0.0.1:1055` 上的私有 Unbound，节点和解析器引导则走 `127.0.0.1:1056` 上的独立验证器——两者都住在核心命名空间里，和宿主机相同端口的服务井水不犯河水，各自维护从锁定 `dns-root-data` 初始化的持久 RFC 5011 信任锚并忽略客户端 CD 请求。它们再通过 TCP loopback 分别把查询转给私有 Mihomo 传输监听（应用用 1057，引导用 1058），原样传递 DNSSEC 材料，不做 hosts 替换、fake-IP 合成或普通缓存复用。

应用验证器在确认一个名字之前，还得拿到父区和目标区的 `DS`、`DNSKEY`、`NSEC`/`NSEC3` 和 RFC 5011 key-tag 应答。这些名字匹配不到 `geosite:cn`，光按域名策略走就会全落到代理 DoH 上，万一选中的 DNS 叶不可用，连国内 `DIRECT` 域名都会跟着验证失败。因此受管传输会先试当前叶，1 秒内拿不到可用材料（含 `SERVFAIL`、`REFUSED` 或截断）就切到 360 字面 DoH 的 `#DIRECT` 验证路径，并在 30 秒内跳过那个坏叶，而应用自己的 A/AAAA/HTTPS 记录仍维持原有的国内/代理分流（这条回退会把被查询区的名字暴露给国内验证解析器，但答案是否有效依然由 DNSSEC 说了算；运行时 `proxy-only` 会关掉这条回退，所有 DNS 角色统一绑死在固定远程叶上）。

在 `rules` 下，应用传输保留国内分流：`geosite:cn` 经 360 DoH 直连解析，其余走 Cloudflare/Google DoH。启用受控来源时，海外查询改走独立的 `lf-dns` 选择组（成员来自授权的 `lf-remote`，每个协议、固定服务器地址和端口保留一个叶子，优先排不同服务器地址和协议的候选）；默认上游 1.5 秒没回就通过 `lf-remote` 重试同一批 DoH 端点，国内查询照常走策略解析器（固定出口模式下所有上游都走已授权出口）。应用和 DNS 各自选叶子，`profile.store-selected` 会在核心重启后恢复仍符合成员资格的选择；受管解析器健康检查失败时，监督器会分批抽查获授权的 DNS 叶子，给选中的可用会话留出时间预热 DNSSEC 验证（没开受控来源的配置保留 `respect-rules` 选路）。核心命名空间 1056 端口上的独立引导验证器只管节点和解析器引导，应用域名和公网拨号目标校验归应用验证器。国内 DoH 端点取自 `my.machine.mihomoDomesticValidatingDnsUpstreams`，必须回传完整 DNSSEC 材料（默认的 360 端点满足，不验证的 AliDNS 不满足）。海外冷查询有时要来回好几趟才能完成验证，国内查询则只走国内往返。**A** 用的 `my.machine.dotEndpoints` 与 **B** 的两类角色互不相干；Mihomo 始终校验证书，上游挂了也不回退明文。此外，DNS 叶子运营方能看到 DoH 端点和时序，认证的 DoH 解析器则会收到海外查询并看到 DNS 叶子的出口地址。

运行时 `proxy-only` 保留两个本地验证器和各自独立预算，但两类查询都改走认证的字面地址 DoH 端点，绑死在与普通应用相同的固定远程叶节点上；如果配了 [WireGuard 出口](proxy_CN.md#包含真实-ping-的-wireguard-出口)，系统 DNS 也会接入应用验证器的真实地址网关，让 `ping 域名` 能走原生 ICMP 出去。这个模式会去掉国内解析策略、直连引导和 MagicDNS 传输，核心直接用已接受并固定的节点地址启动（保留 TLS 服务名），切换时不用联网拉来源或直连解析节点；节点或验证器不可用就一直停在 **C**。

在 **B** 等待 DNS 确认（`proxy-priming`）或探测失败（`degraded-proxy`）期间，普通解析器会继续把公网查询送往选定的核心路径（受管路径不可用时新查询可能失败），切进 **B** 时也会切断 **A** 的应用 Unbound（1055）还没跑完的 DoT 和 loopback 回包。

负责订阅和 provider 主机名、节点端点固定以及受限 socket 后端查询的真实地址过滤解析器监听在 1056 端口（开启运行时切换时走 1062 的基础设施网关）。`rules` 和 **A** 用 1061 上的专用宿主 Unbound（自带独立 DoT 连接和缓存）；当代理意图为 `proxy-only` 时，网关通过 root 所有的 Unix socket 接进核心应用验证器。1059 端口专供 WireGuard 出口下的普通真实地址查询，在 **A** 和 `rules` 下直接拒绝；宿主 DoT、split DNS 和原生 MagicDNS 也穿不过 `proxy-only` 的外层防火墙。切换时会清空宿主解析缓存并重建核心验证器，但受管 fake-IP 映射会持久保留，让应用缓存的地址继续对应原域名。

如果把 `localDnssec` 设为 `false`，公网解析走 Cloudflare/Google DoH，国内域名和代理端点引导走配置的国内解析器——这条兼容路径在 **B** 下不做本地 DNSSEC 验证。两种配置都会开启 `respect-rules`、禁用额外 direct 解析器和 proxy-server resolver-policy map，并由 parser/controller 防止运行时削弱受管字段。

核心在路由之后会检查并固定域名拨号目标（包括已批准的内部后缀）。启用本地验证后，公网拨号目标必须拿本地验证器确认过的答案再去固定真实 IP（订阅 hosts 和系统 hosts 没法用未验证公网答案顶替），字面或解析出来的私网目标则还要看本地 `DIRECT` 或限定远程叶节点的授权。新 UDP 映射会重新验证，缓存映射不重解析但每包都重查出口和端口授权。记住[目标授权策略](proxy_CN.md#域名目标)和 DNS 答案过滤是两层东西——DNS 或后缀例外不会顺带授予远程内部访问权限。

公网拨号目标检查会合并同名并发查询，在共用的 3 秒截止内并行查 A/AAAA，只要有一个地址族拿到了可用验证结果就立刻返回，不会傻等另一个族超时。成功结果最多缓存 10 秒（不超过验证器返回的 TTL 及 CNAME TTL），零 TTL 和失败结果不进缓存，而且缓存和在途工作都归各自策略所有，换了策略绝不复用旧答案或未完成查询。

在受管 fake-IP 模式下，地址记录保持合成行为，同时 HTTPS/SVCB 查询走声明的解析器并保留服务参数（两者都要先过共享过滤）。本地验证在公网 fake-IP 分配之前执行，保留 bogus 失败、已签名 NXDOMAIN 和 NODATA（包括末端缺少所请求地址类型的 CNAME 链）；合成地址会清除 AD，因为 fake 映射本身没有签名。未签名答案依然未经认证，明确的私网域名例外保持独立范围，受管 DNS 也会拒绝包含多个 question 的查询。Firefox 怎么用这些记录、`CONNECT` 的限制以及可选原生路径见[浏览器手册](browser_CN.md#ech-发现与代理兼容)。

### 本地验证与角色预算

```nix
my.proxy.dnsEncryption = "strict";
my.proxy.localDnssec = true;
my.proxy.dnsQueryBudget = {
  application = 256;
  infrastructure = 64;
};
```

本地验证要求同时开启严格加密和 `coreNetns=true`，上面写的查询预算就是模块默认值（允许范围分别是 32–4096 和 8–1024）。两个验证器各有各的查询表、缓存、文件描述符和内存上限，应用那边打满也不会挤占引导验证器的预算。应用验证器给 DNS 消息和 RRset 分别分了 16 MiB 和 32 MiB，会在热门答案快过期时预取，并可在过期后 300 秒内返回符合条件的答案（等新答案的时间由 `my.proxy.dnsStaleWaitMilliseconds` 控制，默认 300 ms；过期应答 TTL 最多 30 秒，新记录仍遵循上游 TTL 上限）。引导验证器则分了 4 MiB 和 8 MiB，只认未过期的答案。超限请求可能被拒，等在途工作跑完容量就释放出来；**A** 的宿主验证器和受限 socket 访客 DNS 仍各自独立，每个应用的 conntrack、UDP association 和代理队列配额也不归这组预算管。

另外，拨号目标守卫有自己固定的上限：最多缓存 256 个名称、同时处理 256 个不同名称的在途查询，不随验证器预算变动。只有验证器 UID 被排除在私有 TUN 之外，它的命名空间防火墙也只允许查私有 TCP 传输监听和收本地回包——验证器自己没法往外发 DNS 流量，应用 `DIRECT` 目标也借不到验证器或传输端口，一旦验证器挂掉，新的公网解析就会直接失败，不回退明文或应用直连。

开启本地验证需要重建 generation 并重启受管服务（已知旧版受管 DNS 和 TUN 值会自动迁移到新 root 策略，外来值和削弱策略的 controller 写入仍会被拒）；要退回兼容路径，只能在重建的 generation 里显式设 `localDnssec=false`。

## 受限 socket 访客 DNS

只要 `my.hardening.socketGuestUsers` 非空或开启了受控来源，系统就会以专用 `socket-guest-dns` 用户（UID 978）在 loopback 1056 端口多跑一个 `dnsmasq`（见[解析服务](../../system/config/network/dns/socket-guest.nix)）。它共用不可变后缀过滤、增补和例外并返回真实地址（没开运行时切换时直连 1061 的基础设施 Unbound），没有明文租约、split DNS 或原生 MagicDNS。在 **A** 和 `rules` 下，基础设施 DoT 不可用时新鲜公网查询会失败（`unboundDotInProxy=false` 会在 `rules` 中关掉该传输），符合条件的缓存和本地数据照常返回。

声明的主 UID 和 subordinate UID 的 TCP/UDP 53 都会在核心 `auto-redirect` 前重定向到这里，NAT 后检查还会拒掉仍指向旧解析器的 conntrack 关联；Root 以及开启受控来源后的 `clash-fetch` 账户可以直接查询 1056。私网目标守卫会拒绝直接使用 fake-IP/已知转换目标和宿主代理入口；独立 HTTPS/DoH 内容虽然不归后缀过滤管，但最终字面 socket 目标仍受账户策略约束。**C** 会一并切断这些账户的已跟踪回包和公网出站，这条路径属于[宿主 socket 访客授权](network_CN.md#访客与容器)，普通桌面解析器和桥接网关 DNS 仍走各自的路径。

## 黑名单

[blocklist.nix](../../system/config/network/dns/blocklist.nix) 在构建时解压 Git 跟踪的[快照](../../local-data/dns-blocklist.txt.xz)（构建和运行时都不联网下载），同一份不可变域名文件既用来生成 `dnsmasq` 的后缀 NXDOMAIN 规则，也直接交给核心加载进内存过滤表。

`my.proxy.blockedDomains` 用来加本机黑名单，`blockedDomainExceptions` 则同时生成 `server=/domain/#` 和核心例外。两者都按标签边界、不区分大小写地匹配后缀，最精确的条目优先，同精度时例外优先——所以你完全可以在放行的子树底下再单独拦住更窄的子域名。注意例外只放行解析，不关闭路由或私网 IP 检查；显式 split-server 条目也参与 [dnsmasq 域名优先级](https://dnsmasq.org/docs/dnsmasq-man.html)，让已批准的私网命名空间能盖过更宽泛的阻断。

核心的稳定路径 `/etc/netsec/dns-blocklist.txt`、附加名单和例外由 root 受管策略持有，指向构建时校验的不可变快照，内容校验与订阅策略身份无关；GUI 配置解析时继承它们，控制 API 也换不掉（名单缺失或无效时会拒绝新配置并保留现有过滤状态）。应用拨号先过域名过滤再看内部后缀权限，没法拿内部后缀绕过阻断；内部解析器查询也共用过滤（显式 hosts/IP 基础设施映射保留各自用途），连代理端点名自己要是进了名单也会被拦——确认信任该端点时，用窄范围 `blockedDomainExceptions` 放行即可。

用 [refresh-dns-blocklist.py](../../scripts/refresh-dns-blocklist.py) 更新数据：`--check` 只校验已提交的快照、manifest 和头部不写文件，加上 `--fetch` 则会和上游来源比对（校验不一致会让配置构建直接失败，不会产出一份残缺名单）。改完重建并重启解析器/核心 generation 即可生效，留着 DNS 或连接缓存的客户端也得重启一下（已有连接不会追溯检查）。来源和快照元数据记在模块头和 [local-data 手册](../../local-data/README.md)。

## Wi-Fi、split DNS 与 MagicDNS

NetworkManager 改写上行 DNS 前，dispatcher 会先把原有的六个 DNS 属性完整备份到 root 私有的 `/var/lib/netsec-dns-profiles/` 并同步目录。用 `sudo proxy-dns-profile plan <UUID>` 可以查看备份路径、状态和能否恢复，`sudo proxy-dns-profile restore <UUID>` 能在不重连的情况下恢复这些字段（会拒绝覆盖期间的人工修改）。如果 `plan` 报 `backup_status: invalid`，留着文件排查并对照确认有效的 NetworkManager 备份核对原字段——备份无效期间，dispatcher 和 restore 都会拒绝改写 profile。把已有备份的 profile 批准用于 split DNS 时，会自动恢复它原来的 DNS 值（凭据、路由和身份字段都不在备份范围内）。

[nm_dns.py](../../system/config/network/dns/nm_dns.py) 会关掉 NetworkManager 受管上行双栈的自动 DNS，清掉多余的显式服务器和搜索域，再用 `~.` 把 `resolved` 钉到本机网关；明文回退读的是原始 DHCP 数据而非改写后的有效 DNS 列表，有线 `dhcpcd` 独立处理。

`my.proxy.splitDnsProfileUuids` 默认为空。获批的 profile 可以提供已由共享[直连路由策略](egress_CN.md)排除在 TUN 外的私网/ULA 条件解析器（包括受限 **B**），由 `dnsmasq` 绑定 Wi-Fi 接口去查；租约绑定 UUID、`ActiveConnection`、`ifindex`、carrier-down 计数和撤销代次，最迟在观测后 120 秒到期，dispatcher 转换时撤销。填了公网解析器、不可读或不支持的 profile 会进 `split-error` 而不会打开默认转发，本地监听身份失败则进 `resolver-error` 且同样不会开启明文回退。

[resolved_policy.py](../../system/config/network/dns/resolved_policy.py) 把受管 TUN 排除在 `resolved` 默认选择之外，Tailscale 自动 DNS/路由保持关闭。`resolved` 会在尾网接口上登记当前已认证的后缀并设 `DefaultRoute=no`，Rules **B** 下的核心也用同一后缀，在查公网 DNS 缓存或合成 fake-IP 之前优先走 MagicDNS（其他 `.ts.net` 名字按普通受管 DNS 走）。核心返回 MagicDNS 记录时跳过公网 DNSSEC 验证，路由绑定已认证账户、尾网和当前接口实例——确认切换或退出会撤销旧路由，短暂状态超时则可用完当前 135 秒授权的剩余时间（按包含休眠的时钟算，唤醒后需重新观测）。防火墙按接口实例和到期时间限制发往 MagicDNS 解析器的流量，退出、到期或换接口后，本次启动内曾认证过的后缀仍只在本机处理。字面 peer 地址和验证后的后缀保留了有意的私网访问，宿主网关绑定本地解析端点；如果要给 LAN/tailnet 客户端提供 DNS 服务，得另行配置监听、客户端 ACL 和应答选路。
