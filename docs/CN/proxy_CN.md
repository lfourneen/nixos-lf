# 代理核心与权限

[English](../proxy.md) | 简体中文 | [文档目录](README_CN.md)

## 组件与身份

| 组件 | 默认端点 / 状态 | 身份 |
|---|---|---|
| Mihomo | Mixed `127.0.0.1:7897`、DNS `:1053`、TUN `Mihomo` | `clash-core`，UID 982；`NET_ADMIN` 和 `NET_RAW` |
| Clash Verge helper | 按需启动的 `clash-verge.service` | root，能力限定为启动、文件及网络管理所需集合 |
| GUI 控制口前向器 | `/run/user/<桌面uid>/clash-verge-rev/verge-mihomo.sock` | 桌面 UID、core 组，无能力；socket 0600 |
| Gost | HTTP `127.0.0.1:33332`、透明 `127.0.0.1:33333` / `[::1]:33333` | UID 987，无能力 |
| dnsmasq / Unbound / dhcpcd | 本地 DNS / 有线 DHCP | UID 985 / 983 / 986 |

[identity.nix](../../system/programs/clash/identity.nix) 和 [runtime.nix](../../system/programs/clash/runtime.nix) 定义核心身份与服务约定。root 启动器经 `setpriv` 降权，核心的 permitted、effective、inheritable、ambient、bounding 集均只保留两项网络能力。真实控制口为 `/run/clash-verge-rev/core/mihomo.sock`，监督器直接访问；桌面 UID 的 `socat` 提供 GUI 前向路径。

Helper 保留身份切换、目录所有权和子进程管理所需权限，仅允许打包的 root 所有启动器作为可执行文件。受管日志固定在 `/var/lib/clash-verge-service/logs` 并固定轮转；客户端 IPC 路径不驱动 root chmod/unlink 或启动恢复。Systemd 管理子进程清理。Helper 私有状态与 `/var/lib/clash-core` 的可写缓存分开。

这些约束减少桌面可控制的 root 操作。核心仍有宿主 `NET_ADMIN`，不能视为与宿主网络隔离。同桌面 UID 程序可以调用允许的 GUI/API；helper 上游 IPC 协议不能代替执行路径和文件操作限制。

## 配置授权

`clash-on` 写入代理意图，进入 C 并开始新导入会话：清除旧授权，打开**一次性 300 秒**窗口。GUI 提供生效配置，打包启动器在共享授权锁下导入后再启动核心。失败也消费窗口，修正输入后需重新 `clash-on`。

[clash-verge-stage.py](../../system/programs/clash/clash-verge-stage.py) 只接受桌面用户所有、大小受限的普通输入文件，不跟随 symlink，复制 YAML/geodata 到 root 所有的快照并检测并发变化。它规范控制口位置，以无能力的 `clash-core` 执行核心 `-t` 验证。目录 0750、文件 0640，core 组可读；授权记录、验证/helper 日志仅 root 可读写。

摘要覆盖生效 YAML、geodata 字节及可选文件缺失状态。规范资源包括 `Country.mmdb`、`GeoIP.dat`、`GeoSite.dat`，以及可选 `ASN.mmdb`/`BundleMRS.7z`；支持的旧 country 数据库名字会规范化。`cache.db` 指向独立可写状态，不纳入摘要。

普通重启要求 GUI YAML 匹配且快照摘要完整，复用已授权数据库；GUI 更新数据库不会替换该授权。导入新 YAML/geodata 需要再次 `clash-on`。受管 geodata 下载禁用，缺失或损坏数据库也不能自动替换。快照跨 helper 重启保留，健康检查复核摘要；`clash-off` 清除单次开机授权并请求直连。

导入授权的是该会话选中的输入，不能使已失陷桌面账户变得可信。授权前应确认订阅和配置来源。

## 受管 profile 与控制 API

[proxy-profile.nix](../../system/config/proxy-profile.nix) 定义 root 策略，[cvr-merge.nix](../../home/config/cvr-merge.nix) 生成匹配的 GUI Merge。最终核心 parser 在 GUI/订阅处理后约束监听、mark、DNS、TUN 路由/排除项和私网目标设置。Rule providers 固定为空，geodata 自动更新关闭，无条件 `MATCH,DIRECT`/`FINAL,DIRECT` 被拒绝；其它订阅规则和组选择保留授权语义。

核心向健康消费者报告 `managed-policy` 摘要，描述系统不变量；独立快照摘要绑定已授权 YAML 和资源。

| 控制操作 | 受管行为 |
|---|---|
| GET / HEAD / OPTIONS | 允许，包括 GUI 延迟测试 |
| PUT / DELETE `/proxies/<name>` | 选择或释放代理选择 |
| DELETE `/connections` 或 `/connections/<id>` | 关闭连接 |
| PATCH `/configs` | 仅 `mode=rule`、`mode=global` 或不改变设置 |
| 其它写操作 | 默认拒绝 |

配置加载、禁用规则、provider/geodata/storage 更新、重启及升级 API 均拒绝。PATCH 在应用前检查规范字段名和重复键，验证与应用使用同一份已解码上游 schema。升级新增写端点时需明确决定权限。

Root 策略同时指定共享域名过滤文件、附加名单与例外。核心在 hosts/缓存/fake-IP 前检查 DNS，在 TCP/UDP 拨号前检查应用目标，使 HTTP/SOCKS 代解析使用与系统 DNS 相同的名单。内部引导解析查询也经过过滤，但其传输与目标验证范围独立；split DNS、原生 MagicDNS 和字面 IP 边界见 [DNS](dns_CN.md)。

## 路由策略

```nix
my.proxy.proxyOnly = false; # 默认保留订阅 Rule 分流。
# my.proxy.proxyOnly = true; # 使用订阅最终代理 MATCH。
```

默认保留国内 DIRECT，并前置已知探针/下载域代理规则。未列出的 IP 回显资源仍可能命中 DIRECT，看到真实出口。健康检查只证明其配置的样本路径。

`true` 将进入核心的应用流量统一交给订阅最终代理 MATCH，要求存在可用代理兜底，覆盖地理/进程分流及 profile REJECT。核心同时拒绝应用 DIRECT/COMPATIBLE TCP/UDP，包括组内 direct 叶子和改名适配器。按规则路由的 DNS 也受约束；显式引导/端点解析和 `#DIRECT` MagicDNS 仍豁免。

部署 Nix generation 并导入/重启受管核心后生效，会增加包括国内流量在内的订阅消耗。`bootMode`、A 的门户访问、root UDP、组播及核心外宿主私网路径独立控制。

## 域名目标

受管核心在 Rule 和统一代理模式均禁用嗅探，使用显式目标和 fake-IP 映射作为域名路由身份；ECH 掩护 SNI 不能替代拨号目标，字面 IP 使用 IP 规则。Parser 也将旧 GUI 配置规范到这一策略，见[浏览器 ECH 发现](browser_CN.md#ech-发现与代理兼容)。

[target_guard.go](../../system/programs/systemd/netsec/target_guard.go) 在 TCP/UDP 出站前校验应用目标。公网域名必须在核心本地解析得到非空、全部为公网的答案；解析错误允许重试一次。选定 IP 同时固定用于 DIRECT 和远程代理拨号，不交给节点再次解析。`privateTargetDomains` 中的内部后缀及已认证 MagicDNS 后缀可以得到私网答案，但仍需本地解析、固定地址和出口授权；后缀本身不建立解析路由，也不能绕过共享域名过滤。

### 本地与远程授权

授权在实际选中的叶节点执行，覆盖嵌套、fallback 和负载均衡组。远程适配器即使命名为 `DIRECT`，也不会获得直连权限。Rule 与 Global 使用相同检查，`proxyOnly` 仍独立拒绝应用 DIRECT。

| 到达核心的目标 | 本地 DIRECT | 远程代理 |
|---|---|---|
| 公网字面 IP 或已验证公网域名 | 允许，但 `proxyOnly` 可拒绝 DIRECT | 允许 |
| 私网/CGNAT/loopback/ULA/链路本地字面地址，或已批准内部后缀的私网答案 | 必须匹配 `localTargetCidrs` 且来自宿主入口 | 必须匹配 `remoteTargetGrants` |
| 清单内元数据端点、未指定、组播、选定保留/过渡地址或原始 fake-IP | 拒绝 | 拒绝，授权条目也不能放行 |
| 已知 NAT64 翻译地址 | 内嵌 IPv4 必须通过公网地址检查 | 相同；不能授权内嵌私网目标 |

`localTargetCidrs` 默认采用共享本地/私网范围。宿主入口对应核心 metadata 的 `HTTP`、`HTTPS`、`SOCKS4`、`SOCKS5`、`REDIR` 和 `TUN`；访客 `TPROXY` 不继承默认本地权限。设为 `[]` 可移除此项核心权限，但不改变原生宿主防火墙授权、访客转发或 A 的 Gost 直连透传。

远程授权默认为空。需要访问节点网络内的服务时，明确叶节点名称、规范内部 CIDR、核心入口、传输协议和目标端口：

```nix
my.proxy.remoteTargetGrants = [{
  proxy = "Office node";
  cidr = "10.20.30.40/32";
  inlets = [ "SOCKS5" ];
  network = "tcp";
  ports = [ 443 ];
}];
```

此授权允许节点按其所在网络解释该 IP，不授予远端 DNS 解析，也不扩大到其它节点、入口、协议或端口。使用域名时，还需声明内部后缀，并通过已授权 hosts 或核心解析器提供答案。授权匹配配置中的叶节点名称，不认证远端服务器身份；应审阅已授权配置/provider 来源，避免将已授权名称复用于其它节点。入口以核心实际收到的流量为准：Gost 转发的 CONNECT 是 `HTTPS`，来自其透明入口的转发也一样；它不认证应用身份，也不保留原应用 UID。

复用 UDP association 时，每个新目标都按该连接实际叶节点检查并固定。缓存映射保留目标来源，每个包重新核对当前入口、目标端口和出口授权，换端口不能复用较窄授权。已有 TCP 连接不会被追溯检查。策略变化后应重建并有意导入/重启核心。

### 地址类别与模式

IPv4-mapped 地址在授权前规范为 IPv4。不可授权的元数据清单包括 `169.254.169.254`、`169.254.170.2`、`100.100.100.200` 和 `fd00:ec2::254`；带 zone 的 IPv6 字面地址被拒绝。保留/过渡地址清单是针对性的，不代表覆盖整个 IANA 特殊用途表或所有供应商的元数据服务。

始终识别知名前缀 `64:ff9b::/96`；`nat64Prefixes` 可声明其它 [RFC 6052](https://www.rfc-editor.org/rfc/rfc6052.html) 翻译前缀，长度限 32、40、48、56、64、96，重叠时采用更精确前缀。内嵌公网 IPv4 可用，私网、元数据和合成目标拒绝连接。本地用途 `64:ff9b:1::/48` 只有显式声明为翻译前缀后才按内嵌地址处理。仅凭地址不能识别未知翻译或网关行为。

fake-IP 池只表示路由身份，不能成为实际出站目标。必须先通过有效映射恢复域名，再执行过滤、解析和授权。缺失映射、真实 DNS 答案携带合成目标均被拒绝，自定义地址池也受检查。

这些规则约束到达核心的应用流量，通常处于 B。A 的 Gost/原生 socket 按 A 防火墙策略保留字面 HTTP 门户；C 在核心之前阻断普通应用，本地和远程授权都不能重新放行。显式内部引导、代理端点传输及 `#DIRECT` MagicDNS 保留受信基础设施范围，按规则 DNS 不继承该豁免。原生 LAN 和访客转发有独立策略；root、持有 `NET_ADMIN` 的核心及已授权基础设施仍受信任，因此不能宣称通用内部网络隔离。

## Gost 生命周期

[gost-relay.nix](../../system/programs/systemd/gost-relay.nix) 遵循模式原子状态，绑定前回收旧进程，不使用 `SO_REUSEPORT`，每端点要求唯一监听，并限制文件描述符、内存和任务数。B 信任监督器健康结论并复核 mixed 监听身份；以被动 socket 观测代替连接透明入口，自连接检测作为后备。

Relay 兼容带有效端口的裸数字 IPv6 CONNECT，保留 Go HTTP framing 和缓冲中的隧道数据。建议客户端使用标准 `[IPv6]:port`。Gost 通过 store 绝对路径运行，不放进系统 PATH 或 initrd。源码及打包自定义见[维护手册](maintenance_CN.md)。
