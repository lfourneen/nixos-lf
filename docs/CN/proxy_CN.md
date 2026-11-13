# 代理核心与权限

[English](../proxy.md) | 简体中文 | [文档目录](README_CN.md)

## 受控订阅来源

本机选择 `controlledSources=true`、`subscriptionTrust="fixed"`、`profileRefreshPolicy="auto"` 和阻断式内容审计。可复用模块在关闭受控来源时保留旧 GUI 导入方式；下文 GUI 导入和可变 provider 的说明属于该兼容路径。

`clash-source` 是仅供 root 操作的 broker，可登记多个来源，同时选择一个。各来源分别保留订阅、provider、版本、语义批准及 settled 基线。`use <id>` 选择已登记来源；GUI 只能选择 root 批准的 `gui-profile` UID 映射，未知映射保持当前选择。GUI URL、YAML、脚本、缓存和 geodata 均不授予来源权限。固定的非特权 worker 获取选中的 HTTPS 订阅和单独登记的 HTTP provider，固定公网端点地址，确定性生成配置。不可变 Nix 策略提供监听、DNS 和 TUN，provider 载荷展开为内联节点，geodata 来自锁定 Nix 软件包。

Sops 的 `proxy_source` 字符串可用 `{"sources":[...]}` 保存所有来源定义。`review/register FILE --id ID` 选择一条，不指定 ID 的 review 列出清单，register 则拒绝含多条时的歧义选择；旧单来源 JSON 仍兼容。ID 和 GUI 映射必须唯一。登记摘要只覆盖所选条目，追加定义不会改变该摘要，也不会自动授权新来源；删除清单条目不撤销其已登记副本。

下载要求正常 TLS 主机名校验、固定公网 socket 地址、精确批准的 HTTPS 跳转，每响应最多 8 MiB、合计 32 MiB，并受 worker 时限约束。默认 User-Agent 为 `clash.meta`，每个端点可登记最长 256 字符、单行 ASCII 的 `user-agent`。跨 origin 后永久清除自定义 UA 和凭据。拒绝 YAML 别名及重复键。节点域名固定地址并保留 TLS 身份；解析失败最多丢弃 `max(1, node_count // 5)` 个节点，非公网答案及被规则引用的空组仍拒绝候选。

渲染器支持 `select`、`url-test`、`fallback`，成员须显式指定或引用已登记 provider。字段逐项校验：HTTPS `url`、1–86400 秒的整数 `interval`、0–60000 ms 的 `tolerance`、100–60000 ms 的 `timeout`、1–100 的 `max-failed-times`、布尔 `lazy` 及 HTTP `expected-status` 表达式。不支持的字段、`load-balance`、动态 filter、`rule-providers`、provider filter/override 和不支持的传输均拒绝。支持 SS、VMess、VLESS、Trojan、Hysteria2、TUIC、HTTP、SOCKS5 节点；远程 HTTP/SOCKS 明文认证仍拒绝，兼容性扩展不放宽内容审计。

私有 manifest 绑定来源 ID、版本、响应摘要、转换器、策略、生成 YAML、丢弃计数及 geodata，快照摘要覆盖 manifest。登记、候选及批准位于 root 私有 `/var/lib/clash-source/sources/<id>`，`selected` 是受公共 `source.lock` 保护的原子指针。旧单来源状态在下一次 broker 操作时复制到此布局，再发布选择。各来源 `approved/` 快照跨切换和重启保留。下载失败保留原选择；解析、审计或健康失败保留此前授权的运行快照。因此，验证完成前，请求的选择可能与 `proxy-status` 的 `Active source` 不同。应用候选会重启核心，可能短暂进入 C；旧回退不兼容时保持 C。

同名身份、传输和认证变化与该来源自己的 settled 基线比较，切走再切回也保留比较依据。逻辑服务器身份不变的已验证 DNS 轮换可自动处理；敏感路由比较覆盖两个方向。精确批准绑定候选、基线、版本、策略及一小时有效期，不豁免证书、hosts、私网目标或格式错误。见[来源登记](operations_CN.md#来源登记)。

## 可见信息与信任

| 参与方 | 能观察的信息 |
|---|---|
| 浏览器与目标网站 | 应用内容；网站还知道账号、Cookie 和出口地址 |
| 本机 HTTP 代理/核心 | CONNECT 名称、DNS 和路由信息；明文 HTTP 内容 |
| 接入网络/DNS 服务商 | 传输地址与时间特征；解析器知道自己处理的查询 |
| 入口/出口运营方 | 相邻地址与流量时间；出口知道目标信息及明文 HTTP |
| 串通的运营方 | 更多关联信息；本机健康探测不能证明其诚实 |

状态将 `controlled-fetch`、`legacy-unverified` 或未知来源与模式、路由和可用性分开，上游签名仍未验证。TLS 保护认证后的内容；ECH 和独立 profile 不隐藏账号、目标 IP 或时间特征，也不能禁止运营方记录或串通。


## 组件与身份

| 组件 | 默认端点 / 状态 | 身份 |
|---|---|---|
| Mihomo | Mixed `127.0.0.1:7897`、DNS `:1053`、TUN `Mihomo` | `clash-core`，UID 982；`NET_ADMIN` 和 `NET_RAW` |
| Clash Verge helper | 按需启动的 `clash-verge.service` | root，能力限定为启动、文件及网络管理所需集合 |
| GUI 控制口前向器 | `/run/user/<桌面uid>/clash-verge-rev/verge-mihomo.sock` | 桌面 UID、core 组，无能力；socket 0600 |
| Gost | HTTP `127.0.0.1:33332`、透明 `127.0.0.1:33333` / `[::1]:33333` | UID 987，无能力 |
| dnsmasq / Unbound / dhcpcd | 本地 DNS / 有线 DHCP | UID 985 / 983 / 986 |

[identity.nix](../../system/programs/clash/service/identity.nix) 和 [runtime.nix](../../system/programs/clash/service/runtime.nix) 定义核心身份与服务约定。root 启动器经 `setpriv` 降权，核心的 permitted、effective、inheritable、ambient、bounding 集均只保留两项网络能力。真实控制口为 `/run/clash-verge-rev/core/mihomo.sock`，监督器直接访问；桌面 UID 的 `socat` 提供 GUI 前向路径。

Helper 保留身份切换、目录所有权和子进程管理所需权限，仅允许打包的 root 所有启动器作为可执行文件。受管日志固定在 `/var/lib/clash-verge-service/logs` 并固定轮转；客户端 IPC 路径不驱动 root chmod/unlink 或启动恢复。Systemd 管理子进程清理。Helper 私有状态与 `/var/lib/clash-core` 的可写缓存分开。

Helper 在特权边界执行固定路径和文件操作约束。核心持有宿主 `NET_ADMIN`，属于可信网络管理层；同桌面 UID 程序可以调用允许的 GUI/API。

显式启用的 IPv4 访客 TPROXY 入口绑定 `0.0.0.0:7896`；普通 mixed/DNS/controller 端点在 `allow-lan=false` 下仍仅本机。打包监听器单独处理该绑定并选择 IPv4 socket，进入 B 前检查 TCP/UDP 的 UID 与服务 cgroup；访客接口/源地址授权仍由防火墙负责。核心后续 auto-redirect 保留自有访客 mark 对应的透明 socket。见[访客网络](network_CN.md#访客与容器)。

## 兼容 GUI 配置导入

`proxy-control preflight` 检查桌面配置、必需资源和受管解析约束，不改变网络意图。`clash-on` 以预检成功为前提，写入代理意图，进入 C 后启动 helper。它清除刷新 pin 和待健康检查记录，保留完整的快照授权，并打开一次性 300 秒导入窗口。失败也会消费该窗口。

[clash-verge-stage.py](../../system/programs/clash/service/clash-verge-stage.py) 接受桌面用户所有、大小受限的普通文件，拒绝符号链接并检测并发变化。它复制 YAML 和 geodata 到 root 所有的快照，规范控制口位置，以无 capabilities 的 `clash-core` 执行 `-t`。快照目录使用 0750，文件使用 0640；授权记录和原始解析诊断仅 root 可读。

快照摘要覆盖 YAML、geodata 字节和可选资源缺失状态。资源包括 `Country.mmdb`、`GeoIP.dat`、`GeoSite.dat`、`ASN.mmdb` 和 `BundleMRS.7z`，旧 country 数据库名字会规范化。`cache.db` 和 `proxies/` provider 缓存指向独立可写状态，不纳入该摘要。

### 刷新策略

`my.proxy.profileRefreshPolicy` 默认为 `strict`，GUI YAML 变化需要新的 `clash-on` 窗口。设为 `auto` 后，只要代理意图仍然有效，变化的 YAML 可在通过相同的输入、解析、内容审计和摘要检查后自动导入。本机配置使用 `auto`。

YAML 未变化时走普通重启流程，复用已授权 geodata。GUI 单独更新数据库不会触发导入。受管 geodata 下载关闭，缺失或损坏数据库也不能自行替换。`clash-off` 撤销授权并请求直连；直连意图下，GUI 更新不能重新启动核心。

通过校验的候选进入 `verifying`。`profile-refresh-verify.timer` 每十秒检查，至少观察 60 秒后，才接受与该候选摘要对应的新鲜健康结果。通过后记录 `accepted`，建立 settled 基线；观察期内连续导入仍保留该基线。失败则恢复基线及授权，重启 helper，并记录 `rolled_back`。没有 settled 基线时记录 `refused`，健康门继续保护宿主。服务恢复以 `proxy-status` 为准，helper unit active 本身不代表核心已就绪。

`auto` 下候选读取或校验失败时，保留完整且仍获授权的旧快照，记录 `fallback`，并用该快照启动核心。没有有效回退对象时以 exit 78 拒绝启动。启动器始终运行 effective 快照。被拒候选的摘要 pin 保留一小时，普通重启不会重复校验，也不续期；`clash-on` 和 `clash-off` 清除 pin 及待健康检查记录。

`/run/proxy-mode/profile-refresh.json` 以 0644 原子发布，包含策略、最近结果、候选与上一摘要、固定失败原因、快照时间、上次接受时间、内容审计分类，以及最多 20 条历史。显示的摘要截断为 12 位。记录不包含订阅 URL、凭据、节点名、私有路径或原始解析输出。

### 内容审计

`my.proxy.subscriptionAudit` 支持 `off`、`warn` 和 `enforce`，模块默认 `warn`，本机配置使用 `enforce`。worker 在 `-t` 与快照提交之间离线运行，身份为无 capabilities 的 `clash-core`。`off` 只关闭附加检查，受管解析器与摘要检查始终有效。

检查范围包括节点 server 地址、未获授权的 `skip-cert-verify`、空组、无效组引用、YAML 键结构、hosts 映射和 provider 配置变化。历史比较使用最近 settled 的 YAML；可变 provider 内容仍遵循下文的独立信任模型。`my.proxy.insecureProxyExceptions` 中的具名 TLS 例外保留。有意使用非公网节点端点时须声明 `my.proxy.subscriptionAuditServerCidrs`，该审计例外不授予应用访问权限，组播端点仍被拒绝。

`my.proxy.remoteOnlyCatchAll` 默认关闭，本机启用。Merge 脚本沿最终兜底策略及其代理组引用，移除 DIRECT 等非代理成员，支持任意订阅分组名，拒绝空组和循环引用。这些共享分组不再提供对应的手动选择；显式 DIRECT 规则和无关分组保留。动态 provider 路由在 `enforce` 下仍须得到确定的审计结论。

该约束在 GUI 生成 YAML 时应用。修改 Merge 策略后，应重新应用当前 profile，生成对应 YAML，再请求新导入。

每次导入都检查 root 显式 hosts 白名单，首次导入也不例外。候选提供的名称和值须匹配 `my.proxy.subscriptionAuditAllowedHosts`，允许省略白名单中的条目。默认空白名单不允许订阅覆盖 hosts。

```nix
my.proxy.subscriptionAuditAllowedHosts = {
  "service.internal" = "192.168.1.10";
};
```

`my.proxy.sensitiveDomains` 为用户选定的域名后缀启用定向检查，默认为空。检查器支持有序的 `DOMAIN`、`DOMAIN-SUFFIX`、`DOMAIN-KEYWORD`、`MATCH` 规则及可静态确定的代理组，识别可证明的 DIRECT 到代理或拒绝变化。同一后缀下混合路由保守返回 `unknown`，GEOSITE、GEOIP、逻辑规则和动态组也可能无法确定；检查器不等同完整的 Mihomo 路由求值。基础设施目标不会自动加入此列表。

`my.proxy.subscriptionAuditProtectedRoutes` 为精确域名声明预期的 `direct`、`proxy` 或 `reject` 路由。健康和延迟探测域名默认为 `proxy`；订阅和更新域名须在此声明各自预期路由。无法确定的规则或代理组不能证明候选符合要求。

`enforce` 阻断 deny，以及敏感域或受保护目标路由无法确定的候选，原因分别为 `blocked by audit` 或 `audit indeterminate`。worker 自身失败也不能在 `enforce` 下通过。`warn` 记录结果并继续导入。结构性警告覆盖规则或节点数量大幅变化、单一 host 集中、新增明文凭据节点和大类地理路由变化。公开结果只包含固定分类与计数。

## 受管 profile 与控制 API

[profile.nix](../../system/config/network/proxy/profile.nix) 定义 root 策略，[cvr-merge.nix](../../home/config/clash/cvr-merge.nix) 生成匹配的 GUI Merge。最终核心 parser 在 GUI/订阅处理后约束监听、mark、DNS、TUN 路由/排除项和私网目标设置。Rule providers 固定为空，geodata 自动更新关闭，无条件 `MATCH,DIRECT`/`FINAL,DIRECT` 被拒绝；其它订阅规则和组选择保留授权语义。

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

## 兼容模式的订阅信任

`my.proxy.subscriptionTrust="source"` 将 proxy provider 视为可信的可更新来源；`"fixed"` 禁止 provider，使内联节点内容纳入授权 YAML 快照。`proxy-control preflight` 显示内联节点数、provider 数、来源主机和申请的证书例外数量，不输出订阅 URL 或凭据。

全局 merge 脚本过滤内联节点中启用 `skip-cert-verify` 的节点，并移除分组中的对应引用。`my.proxy.insecureProxyExceptions` 点名的节点保留；没有 provider 成员的分组被清空时，配置生成终止。核心在 per-profile 扩展和 provider override 处理后应用同一份例外名单。

证书例外按叶节点名称匹配，provider 用同名节点替换服务器时沿用该例外。可变 provider 与 `remoteTargetGrants` 并用还需 `allowDynamicRemoteGrants=true`。固定模式将内联节点内容绑定到每次授权导入。

预检检查生成的配置、资源和受管解析器，单独报告证书策略拒绝并记录脱敏的准备结果。Provider 内容由核心在加载和刷新时校验。快照中的 root 所有 `proxies/` 链接指向可写的 `/var/lib/clash-core/proxies/`，HTTP provider 默认使用此目录，显式 provider 路径也须位于相对 `proxies/` 目录内。HTTP provider 优先读取缓存，缓存被拒绝后尝试远端；刷新被拒绝时保留上次接受的节点和缓存。初始化失败期间保留声明的 fallback payload；更新间隔为正数的 provider 自动重试。

快照快速检查比较文件身份、权限、大小及 ctime/mtime 与完整验证记录。授权变化、元数据变化或 60 秒间隔触发完整 SHA-256 校验，启动和导入始终执行完整校验，无法观察启动身份的沙箱也执行完整校验。快照文件及验证记录仅由受信任 root 写入。

## 路由策略

```nix
my.proxy.proxyOnly = false; # 默认保留订阅 Rule 分流。
# my.proxy.proxyOnly = true; # 使用订阅最终代理 MATCH。
```

默认保留国内 DIRECT，并为配置的探针/下载域以及登记的 IP 探测/分流检测域名（`my.proxy.probeDomains`）前置代理规则，避免检测站看到直连地址；其它目标按订阅规则选路。健康探测使用配置的样本目标。

`true` 将进入核心的应用流量统一交给订阅最终代理 MATCH，要求存在可用代理兜底，覆盖地理/进程分流及 profile REJECT。核心同时拒绝应用 DIRECT/COMPATIBLE TCP/UDP，包括组内 direct 叶子和改名适配器。按规则路由的 DNS 也受约束；显式引导/端点解析和 `#DIRECT` MagicDNS 仍豁免。

部署 Nix generation 并导入/重启受管核心后生效，会增加包括国内流量在内的订阅消耗。`bootMode`、A 的门户访问、root UDP、组播及核心外宿主私网路径独立控制。

`proxy-control-vm` 保留默认 Rule 覆盖，`proxy-uniform-vm` 使用生产 helper/核心和 `proxyOnly=true`，检查全新 TCP/UDP 的 DIRECT 拒绝与真实双栈路径。UDP 在 association 内固定叶子，因此 DIRECT 拒绝测试使用新 association；复用远程叶子仍属于代理路径。浏览器检查同样覆盖两种路由策略。修改上游源码或补丁前，参照[源码兼容性检查](maintenance_CN.md#源码兼容性检查)。

## 域名目标

受管核心在 Rule 和统一代理模式均禁用嗅探，使用显式目标和 fake-IP 映射作为域名路由身份；ECH 掩护 SNI 不能替代拨号目标，字面 IP 使用 IP 规则。Parser 也将旧 GUI 配置规范到这一策略，见[浏览器 ECH 发现](browser_CN.md#ech-发现与代理兼容)。

[target_guard.go](../../system/programs/clash/mihomo/guard/target_guard.go) 在 TCP/UDP 出站前校验应用目标。公网域名必须在核心本地解析得到非空、全部为公网的答案；解析错误允许重试一次。选定 IP 同时固定用于 DIRECT 和远程代理拨号，不交给节点再次解析。`privateTargetDomains` 中的内部后缀及已认证 MagicDNS 后缀可以得到私网答案，但仍需本地解析、固定地址和出口授权；后缀本身不建立解析路由，也不能绕过共享域名过滤。

TCP 在实际选定的叶节点交错尝试已验证的 IPv4/IPv6 候选，最多八个，单次上限两秒、总截止六秒。每个候选均需通过该叶节点的出站授权；节点只接收固定 IP，不执行目标域名解析。UDP 为关联固定单一目标。

核心在拨号返回错误时尝试下一候选；拨号成功后交付连接，应用数据沿该连接传输。乐观确认或单向代理协议以隧道建立为拨号成功点，目标地址回退在该成功点结束。

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

IPv4-mapped 地址在授权前规范为 IPv4。不可授权的元数据清单包括 `169.254.169.254`、`169.254.170.2`、`100.100.100.200` 和 `fd00:ec2::254`；带 zone 的 IPv6 字面地址被拒绝。

始终识别知名前缀 `64:ff9b::/96`；`nat64Prefixes` 可声明其它 [RFC 6052](https://www.rfc-editor.org/rfc/rfc6052.html) 翻译前缀，长度限 32、40、48、56、64、96，重叠时采用更精确前缀。内嵌公网 IPv4 可用，私网、元数据和合成目标拒绝连接。本地用途 `64:ff9b:1::/48` 只有显式声明为翻译前缀后才按内嵌地址处理。

fake-IP 池只表示路由身份，不能成为实际出站目标。必须先通过有效映射恢复域名，再执行过滤、解析和授权。缺失映射、真实 DNS 答案携带合成目标均被拒绝，自定义地址池也受检查。

这些规则约束到达核心的应用流量，通常处于 B。A 的 Gost/原生 socket 按 A 防火墙策略保留字面 HTTP 门户；C 在核心之前阻断普通应用，本地和远程授权都不能重新放行。显式内部引导、代理端点传输及 `#DIRECT` MagicDNS 保留受信基础设施范围，按规则 DNS 不继承该豁免。原生 LAN 和访客转发有独立策略；Root、持有 `NET_ADMIN` 的核心及已授权基础设施属于可信网络管理层。

## Gost 生命周期

[default.nix](../../system/programs/gost/default.nix) 遵循模式原子状态，绑定前回收旧进程，不使用 `SO_REUSEPORT`，每端点要求唯一监听，并限制文件描述符、内存和任务数。B 信任监督器健康结论并检查 mixed 监听身份；以被动 socket 观测代替连接透明入口，自连接检测作为后备。

Relay 兼容带有效端口的裸数字 IPv6 CONNECT，保留 Go HTTP framing 和缓冲中的隧道数据。建议客户端使用标准 `[IPv6]:port`。Gost 通过 store 绝对路径运行，不放进系统 PATH 或 initrd。源码及打包自定义见[维护手册](maintenance_CN.md)。
