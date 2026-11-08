# DNS

[English](../dns.md) | 简体中文 | [文档目录](README_CN.md)

## 解析链路

```text
应用 -> systemd-resolved -> dnsmasq 127.0.0.1:1054
                              | 本地后缀黑名单 / 例外
                              +-> B: Mihomo 127.0.0.1:1053（fake-IP）
                              +-> A/C: Unbound 127.0.0.1:1055（DoT）
                              +-> 批准的 split DNS / A 中有租约的明文回退
```

[network.nix](../../system/config/network.nix) 配置解析服务，[dns-upstream.nix](../../system/programs/systemd/dns-upstream.nix) 协调 dnsmasq 的 `/run/dns-upstream/servers.conf`，以 SIGHUP 热更新，失败会保留待应用状态并重试。内部 1053/1055 仅允许 root 诊断及 dnsmasq UID 查询，1054 面向客户端。[listeners.py](../../system/programs/systemd/netsec/listeners.py) 共用检查 TCP/UDP 监听地址、UID 和 cgroup。

上行口的明文 DNS 对两种 IP 家族均重定向到 dnsmasq，包括硬编码的私网/门户解析器。dnsmasq 自身上游和批准的 MagicDNS 走明确策略。早期 853 guard 允许 root、核心 UID 加 mark 及配置的 Unbound 路径，在 auto-redirect 隐藏原端口前阻断应用独立 DoT/DoQ；DoH 与 HTTPS 共用 443，不能据此区分。

## A：验证与恢复

Unbound 使用 360 DoT：`101.226.4.6`、`218.30.118.6`、`123.125.81.6`，认证名称 `dot.360.cn`。本地 `validator iterator` 验证 DNSSEC，忽略客户端 CD 请求。锁定的 `dns-root-data` 提供初始信任锚，RFC 5011 状态持久化在 `/var/lib/unbound`，启动不依赖未经认证的初始锚下载。未签名区域不会因此获得签名。

上游私网/CGNAT、访客网段、loopback、未指定、链路本地和 ULA 答案受到过滤，包含 IPv4-mapped 形式；明确本地数据和窄 private-domain 仍可用。上游故障期间可在 900 秒内服务已验证缓存，应答 TTL 为 30 秒。Split DNS 和明文回退不经过 Unbound。

[dnssec_probe.py](../../system/programs/systemd/netsec/dnssec_probe.py) 分离本地验证与传输诊断：根下全新随机名字要求认证否定证明，避免重复缓存答案；独立 TLS 查询确认加密上游能否回答及提供签名材料。只有传输正常时才推迟冷启动信任锚 priming 判定。结果、RCODE、EDE 和故障类别写入 `/run/dns-upstream/state.json` 及持久化的 `/var/lib/dns-upstream/history.log`。

加密路径无法提供可用判定时，A 可用原始 DHCP4 DNS 打开有界明文窗口；没有 DHCP DNS 时使用 `1.1.1.1`/`1.0.0.1`。这条恢复路径与 Unbound 上游分开。

| 控制 | 默认行为 |
|---|---|
| 租约 | 每次续租最多 120 秒，受剩余额度限制 |
| 会话额度 | 1800 秒；明文每使用一秒扣一秒 |
| 恢复额度 | 仅验证成功的加密路径每秒补一秒；恢复不重置额度 |
| 会话身份 | boot ID + ifindex，Wi-Fi 另加 ActiveConnection |
| Carrier / DHCP 变化 | 不产生新额度；服务重启保留记账 |
| 可选开机截止 | `my.proxy.plaintextBootBudgetSeconds`，默认 `null` |
| 显式 override | root 的 `/run/dns-upstream/force-plaintext`，删除或重启前有效 |

Override 可以超过额度，但仍记账并受单次租约续期约束；删除不会恢复已花费时间。C 的更早出站规则始终切断外部 DNS，不受回退选择影响。

[dns_policy.py](../../system/programs/systemd/netsec/dns_policy.py) 是特权内核租约 broker，通过带超时的 nft set 仅允许 dnsmasq UID 到精确上游。协调器停止或卡死不能延长过期租约；休眠和连接变化先撤销权限，再决定续租。

## B：受管 Mihomo DNS

[proxy-profile.nix](../../system/config/proxy-profile.nix) 定义受管 DNS。TUN 模式使用 fake-IP（默认 `198.18.0.1/16`）、固定 blacklist 过滤模式和 DNS hijack。本地域及部分连通性/STUN 名称返回真实地址；fake-IP 过滤仅改变解析，不改变路由。HTTP-only（`tunMode=false`）采用 redir-host。

公共解析使用 Cloudflare/Google DoH，国内域与代理端点引导使用配置的国内解析器。启用 `respect-rules`，禁用额外 direct 解析器和 proxy-server resolver-policy map；核心 parser/controller 防止运行时削弱受管字段。B 与 A 的本地 Unbound 验证器分开。

核心还会在路由/嗅探后检查域名拨号目标，并为 DIRECT 和代理 TCP/UDP 固定已验证 IP。新 UDP 目的映射重新检查，后续包复用映射。[目标守卫及其豁免](proxy_CN.md#域名目标)与 DNS 答案过滤是不同层。

## 黑名单

[dns-blocklist.nix](../../system/config/dns-blocklist.nix) 将 Git 跟踪的[压缩快照](../../local-data/dns-blocklist.txt.xz) 渲染为 dnsmasq 后缀 NXDOMAIN 规则。名单位于公共入口，覆盖 A/B 和使用网关 DNS 的客户端，不因模式切换而变化。构建和运行时均不下载。

`my.proxy.blockedDomains` 添加本机名字；`blockedDomainExceptions` 生成 `server=/domain/#`，更精确的例外使用当前模式的标准上游。用 [refresh-dns-blocklist.py](../../scripts/refresh-dns-blocklist.py) 刷新，`--check` 仅报告漂移。来源和快照信息见模块头及 [local-data 手册](../../local-data/README.md)。

这是本地 DNS 过滤。显式代理可在 Gost/Mihomo 内部解析目标而不经过 dnsmasq；独立 DoH 和远程代理解析不在黑名单保证范围内。

## Wi-Fi、split DNS 与 MagicDNS

[nm_dns.py](../../system/programs/systemd/netsec/nm_dns.py) 为受管 Wi-Fi 的两种 IP 家族禁用自动 DNS，清除非预期显式服务器/搜索域，并以 `~.` 将 resolved 固定到本机网关。明文回退读取原始 DHCP 数据，不读取已改写的有效 DNS 列表；有线 dhcpcd 独立处理。

`my.proxy.splitDnsProfileUuids` 默认空。批准 profile 可提供已由共享[直连路由策略](egress_CN.md)排除在 TUN 外的私网/ULA 条件解析器（含受限 B），dnsmasq 绑定 Wi-Fi 接口查询。租约绑定 UUID、ActiveConnection、ifindex、carrier-down 计数和撤销代次，最迟在原观测后 120 秒到期，dispatcher 转换时撤销。公网解析器、不可读或不支持的 profile 进入 `split-error`，不会打开默认转发。本地监听身份失败进入 `resolver-error`，也不能开启明文回退。

[resolved_policy.py](../../system/programs/systemd/netsec/resolved_policy.py) 将受管 TUN 排除在 resolved 默认选择外。Tailscale 自动 DNS/路由关闭，仅已认证 tailnet 的当前 MagicDNS 后缀条件路由到其解析器，设 `DefaultRoute=no`。字面 peer 地址和验证后的后缀保留有意的私网访问。对外提供 LAN/tailnet DNS 需独立监听、客户端 ACL 与应答选路策略，宿主网关不是 wildcard resolver。
