# 代理核心与权限

[English](../proxy.md) | 简体中文 | [文档目录](README_CN.md)

## 受控订阅来源

本机选择 `controlledSources=true`、`subscriptionTrust="fixed"`、`profileRefreshPolicy="auto"` 和阻断式内容审计。
可复用模块在关闭受控来源时保留旧 GUI 导入方式。
下文 GUI 导入和可变 provider 的说明属于该兼容路径。

`clash-source` 通过仅 root 可操作的 broker 登记来源，同时选择一个来源。
每个来源分别保存订阅、provider、版本、批准和已接受的基线。
`use <id>` 选择已登记来源，GUI 只能选择映射到 root 批准 `gui-profile` UID 的来源。

非特权 worker 下载 HTTPS 订阅和已登记的 HTTP provider，固定公网端点地址并生成配置。
Nix 提供监听、DNS、TUN 设置及 geodata，provider 展开为内联节点。
GUI 的 URL、YAML、脚本和缓存不能授权新来源。

Sops 的 `proxy_source` 字符串可用 `{"sources":[...]}` 保存所有来源定义。
`review/register FILE --id ID` 选择一条，不指定 ID 的 review 列出清单，register 则拒绝含多条时的歧义选择。
旧单来源 JSON 仍兼容。
ID 和 GUI 映射必须唯一。
登记摘要只覆盖所选条目，追加定义不会改变该摘要，也不会自动授权新来源。
删除清单条目不撤销其已登记副本。

下载要求正常 TLS 主机名校验、固定公网 socket 地址、精确批准的 HTTPS 跳转，每响应最多 8 MiB、合计 32 MiB，并受 worker 时限约束。
默认 User-Agent 为 `clash.meta`，每个端点可登记最长 256 字符、单行 ASCII 的 `user-agent`。

跨 origin 后永久清除自定义 UA 和凭据。
拒绝 YAML 别名及重复键。
节点域名固定地址并保留 TLS 身份。
解析失败最多丢弃 `max(1, node_count // 5)` 个节点，非公网答案及被规则引用的空组仍拒绝候选。

渲染器支持 `select`、`url-test` 和 `fallback` 组，成员必须明确列出或引用已登记 provider。

| 字段 | 接受的值 |
|---|---|
| `url` | HTTPS 地址 |
| `interval` | 1–86400 秒的整数 |
| `tolerance` | 0–60000 ms |
| `timeout` | 100–60000 ms |
| `max-failed-times` | 1–100 |
| `lazy` | 布尔值 |
| `expected-status` | HTTP 状态表达式 |

不支持 `load-balance`、动态 filter、`rule-providers` 和 provider filter/override。
节点支持 SS、VMess、VLESS、Trojan、Hysteria2、TUIC、HTTP 和 SOCKS5，拒绝远程 HTTP/SOCKS 明文认证。

私有 manifest 记录来源 ID、版本、响应摘要、转换器、策略、生成的 YAML、丢弃节点数及 geodata。
快照摘要包含这份 manifest。

登记、候选及批准保存在 root 私有的 `/var/lib/clash-source/sources/<id>`。
`selected` 是当前选择的原子指针，由共享 `source.lock` 保护。
旧单来源状态在下一次 broker 操作时迁入此布局。
各来源的 `approved/` 快照跨切换和重启保留。

下载失败保留原选择，解析、审计或健康失败保留此前授权的运行快照。
因此，请求的选择可能暂时与 `proxy-status` 中的 `Active source` 不同。

刷新服务在中断已就绪的核心前检查候选。
被拒或未变化的候选，只有在当前快照授权、完整性、完整健康检查及就绪检查仍有效时才保留该核心。
运行状态不健康或授权失效时，仍进入 C 恢复。
应用变化的候选先进入 C，再重启核心，并须重新通过启动检查。
没有兼容的回退快照时保持 C。

`clash-off` 撤销运行授权，但保留该来源已接受的基线。
自动刷新模式下，下次 `clash-on` 优先使用符合当前受管策略的基线，并记录 `restored`。
恢复时仍重复解析、内容审计和摘要检查，进入 B 仍需新鲜的实际健康证明。
共享黑名单使用稳定的策略路径，已校验的名单更新不会使已接受订阅基线失效。
启动更新请求在 C 中保持待处理。经验证的 B 在同一次启动、快照和策略下持续健康 90 秒后，后台服务消费请求并开始下载。
请求会合并，避免更新自身重启核心时反复触发下载。
定时任务在 C 中触发时延期，不报下载失败。
等待和下载都不阻塞前台核心启动，也无需等开机 timer。
尚无已接受基线的首次导入使用已有候选，并经过正常健康观察期。
停止再启动不会豁免结构检查。

节点身份、认证和传输变化与该来源自己的已接受基线比较，切换来源或重启后也保留比较依据。
本机自动批准 `node_identity`、`node_authentication` 和 `node_transport` 变化，包括逻辑服务器名与 TLS 名称轮换。
重启后首次 `clash-on` 和 root 定时刷新使用相同策略，日常更新无需人工审阅。

可复用选项 `my.proxy.subscriptionAuditAutoApprove` 默认为空列表，即关闭自动批准。
自动批准要求启用受控来源、`profileRefreshPolicy="auto"`、`subscriptionAudit="enforce"`，且已有接受基线。
所有 deny 类别都必须属于配置列表，并且不能有未确定的审计结果。
私网或元数据地址、跳过证书验证、明文凭据、未经本地许可的 hosts 覆盖、受保护路由变化及格式错误仍阻断导入。
敏感路由两个方向的变化也不参与自动批准。

自动批准记录有效期五分钟，绑定候选、基线、来源版本、受管策略、验证器策略和审计策略。
实际导入重复执行解析、批准记录校验和阻断审计，再经过健康接受或回滚。
将选项设为 `[]` 即关闭自动批准，旧记录的校验要求保持不变。
见[来源登记](operations_CN.md#来源登记)。

来源下载和节点地址固定使用 `127.0.0.1:1056` 的过滤后真实地址解析器，不受 B 模式 fake-IP 答案影响。
只有专用 `clash-fetch` 账户和 root 可以直接查询该端口。
Worker 仍拒绝非公网答案，并验证 HTTPS 证书。
一个地址家族不可用时，可以使用另一个家族的有效答案。
下载先检查完整地址集合，再在同一截止时间内重试公网地址，保留 HTTPS 主机名和证书验证。
没有可用答案时刷新失败，不回退到系统 DNS。
因此，B 中的新查询也需要 Unbound DoT 路径，`unboundDotInProxy=false` 时只能使用符合条件的缓存答案。

## 可见信息与信任

| 参与方 | 能观察的信息 |
|---|---|
| 浏览器与目标网站 | 应用内容。网站还知道账号、Cookie 和出口地址 |
| 本机 HTTP 代理/核心 | CONNECT 名称、DNS 和路由信息。明文 HTTP 内容 |
| 接入网络/DNS 服务商 | 传输地址与时间特征。解析器知道自己处理的查询 |
| 入口/出口运营方 | 相邻地址与流量时间。出口知道目标信息及明文 HTTP |
| 串通的运营方 | 更多关联信息。本机健康探测不能证明其诚实 |

状态将 `controlled-fetch`、`legacy-unverified` 或未知来源与模式、路由和可用性分开。
面板显示最近刷新结果、审计类别与计数，以及上次自动批准涉及的去重节点数和时间。
HTTPS 证书检查认证下载连接。
当前配置没有接入发布者签名验证器，因此不验证订阅内容的发布者签名。
自动内容检查只能拒绝可判定的风险，无法确认轮换由谁发起，也不能认证发布者身份。
域名差异无法区分运营方轮换与恶意替换。
TLS 保护认证后的内容。
ECH 和独立 profile 不隐藏账号、目标 IP 或时间特征，也不能禁止运营方记录或串通。

## 组件与身份

| 组件 | 默认端点 / 状态 | 身份 |
|---|---|---|
| Mihomo | Mixed `127.0.0.1:7897`、DNS `:1053`、TUN `Mihomo` | `clash-core`，UID 982。核心网络命名空间内的 `NET_ADMIN` 和 `NET_RAW` |
| Clash Verge helper | 按需启动的 `clash-verge.service` | root，保留有限的启动/文件权限。网络能力限定于核心命名空间 |
| GUI 控制口前向器 | `/run/user/<桌面uid>/clash-verge-rev/verge-mihomo.sock` | 桌面 UID、core 组，无能力。socket 0600 |
| Gost | HTTP `127.0.0.1:33332`、透明 `127.0.0.1:33333` / `[::1]:33333` | UID 987，无能力 |
| dnsmasq / Unbound / dhcpcd | 本地 DNS / 有线 DHCP | UID 985 / 983 / 986 |

[identity.nix](../../system/programs/clash/service/identity.nix) 和 [runtime.nix](../../system/programs/clash/service/runtime.nix) 定义核心身份与服务约定。
root 启动器经 `setpriv` 降权，核心的 permitted、effective、inheritable、ambient、bounding 集均只保留两项网络能力。

真实控制口为 `/run/clash-verge-rev/core/mihomo.sock`，监督器直接访问。
宿主命名空间内无 capability 的桌面 UID `socat` 服务通过 Unix 字节流提供 GUI 前向路径，不传递文件描述符。

Helper 保留身份切换、目录所有权和子进程管理所需权限，仅允许打包的 root 所有启动器作为可执行文件。
受管日志固定在 `/var/lib/clash-verge-service/logs` 并固定轮转。
客户端 IPC 路径不驱动 root chmod/unlink 或启动恢复。
Systemd 管理子进程清理。
Helper 私有状态与 `/var/lib/clash-core` 的可写缓存分开。

Helper 在特权边界执行固定路径和文件操作约束。
Systemd 在执行前将 helper 和核心置于 `lf-clash-core`。
核心不能管理宿主接口、路由和防火墙表，宿主配置及策略变更由固定 root 服务负责。
同桌面 UID 程序可以调用允许的 GUI/API。
GUI 使用未修改的上游打包版本，不修改 Clash Verge GUI 源码；Nix 包复用上游 unwrapped 输出，只替换启动包装、helper 和核心。
首页、设置页、托盘菜单及其点击动作、控制快捷键仍可操作，并可写入 GUI 本地偏好。
这些操作在到达操作系统或受管核心前被静默丢弃：

- 启动包装用只丢弃写入的 helper shim 替换 System Proxy、PAC 和代理守护调用。
  私有挂载命名空间遮蔽 nixpkgs 写入二进制的绝对 gsettings 与 KDE helper 路径；`dconf` 和 KDE 5 helper 由 PATH shim 替换。
  私有命名空间不可用时，以 GSettings 内存后端和 KDE 5 helper 名称作为后备。
  因此启动、切换、PAC 和退出清理都返回成功，但不会改变 OS 代理设置。
  shim 可转发的读取仍使用真实 helper。
- 控制器对 `mode`、`tun` 的 `PATCH /configs` 返回 204 但不应用；`PUT /configs` 配置重载同样返回 204 但不应用。
  其它配置字段及其它写操作仍拒绝，因此被丢弃的重载不会触发 GUI 的重启兜底。
- 受管解析器在最终校验前将生成配置的核心模式固定为 `rule`，并用 root 策略覆盖整个 `tun` 段。
  GUI 偏好不能改变实际 TUN，也不能让重启因策略校验失败。

GUI 可以保存并显示不同的本地偏好，但它不是权威状态。
实际状态以控制器、`proxy-status` 或受管核心为准。
运行策略缺失或损坏不会授予控制写权限。
状态读取、受管代理组内选节点、关闭连接、延迟测试和日志仍然可用。
上游“切换模式时关闭连接”偏好仍可在点击模式控件时关闭连接。要保留会话可关闭该偏好，核心模式请求本身仍被丢弃。
helper 根据内核 Unix peer 身份判断生命周期请求。GUI 的停止请求和对已运行核心的启动请求返回成功但不执行。
受控来源的核心只能由 root 请求启动。关闭 GUI 保留受管核心运行，退出 B 使用 `clash-off`。

显式启用的 IPv4 访客 TPROXY 入口绑定 `0.0.0.0:7896`。
普通 mixed/DNS/controller 端点在 `allow-lan=false` 下仍仅本机。
打包监听器单独处理该绑定并选择 IPv4 socket，进入 B 前检查 TCP/UDP 的 UID 与服务 cgroup。

访客接口/源地址授权仍由防火墙负责。
核心后续 auto-redirect 保留自有访客 mark 对应的透明 socket。
见[访客网络](network_CN.md#访客与容器)。

## 兼容 GUI 配置导入

`proxy-control preflight` 检查桌面配置、必需资源和受管解析约束，不改变网络意图。
`clash-on` 以预检成功为前提，写入代理意图，进入 C 后启动 helper。
它清除刷新 pin 和待健康检查记录，保留完整的快照授权，并打开一次性 300 秒导入窗口。
失败也会消费该窗口。

[clash-verge-stage.py](../../system/programs/clash/service/clash-verge-stage.py) 接受桌面用户所有、大小受限的普通文件，拒绝符号链接并检测并发变化。
它复制 YAML 和 geodata 到 root 所有的快照，规范控制口位置，以独立、无 capabilities 的 `clash-validate` 身份执行 `-t`。
快照目录使用 0750，文件使用 0640。
授权记录和原始解析诊断仅 root 可读。

已授权的 `127.0.0.1` 和 `::1` TCP/UDP 目标通过独立、无 capability 的 `clash-loopback` broker 访问宿主回环。
Unix 通道核对 peer UID，拒绝保留的代理/DNS 端口。
UDP 截获保留原目标端口。
机会性 DNS 的 53 端口固定映射到宿主网关，严格 DNS 禁用此映射。
通道只传输字节和报文，不传递宿主 socket 描述符。
其它回环别名不转发。

快照摘要覆盖 YAML、geodata 字节和可选资源缺失状态。
资源包括 `Country.mmdb`、`GeoIP.dat`、`GeoSite.dat`、`ASN.mmdb` 和 `BundleMRS.7z`，旧 country 数据库名字会规范化。
`cache.db` 和 `proxies/` provider 缓存指向独立可写状态，不纳入该摘要。

### 刷新策略

`my.proxy.profileRefreshPolicy` 默认为 `strict`，GUI YAML 变化需要新的 `clash-on` 窗口。
设为 `auto` 后，只要代理意图仍然有效，变化的 YAML 可在通过相同的输入、解析、内容审计和摘要检查后自动导入。
本机配置使用 `auto`。

YAML 未变化时走普通重启流程，复用已授权 geodata。
GUI 单独更新数据库不会触发导入。
受管 geodata 下载关闭，缺失或损坏数据库也不能自行替换。
`clash-off` 撤销授权并请求直连。
直连意图下，GUI 更新不能重新启动核心。

通过校验的候选进入 `verifying`。
`profile-refresh-verify.timer` 每十秒检查，至少观察 60 秒后，才接受与该候选摘要对应的新鲜健康结果。
通过后记录 `accepted`，建立 settled 基线。
观察期内连续导入仍保留该基线。
失败则恢复基线及授权，通过 helper IPC 替换核心，并记录 `rolled_back`。
替换期间 helper 监听保持可用，只有 helper 传输不可用时才重启其单元。

启动导入保留完整的 60 秒观察期，不执行提前回滚。
后续导入存在 settled 回退快照，且同候选、同策略的完整健康检查连续至少两次新鲜失败时，经过 `my.proxy.profileRefreshFailureGraceSeconds` 后可在下一轮 verifier 提前回滚。
该选项默认 20 秒，范围为 20–60 秒。
缺少有效失败证明时仍等待 60 秒，成功接受始终需要完整观察期。

没有 settled 基线时记录 `refused`，健康门继续保护宿主。
服务恢复以 `proxy-status` 为准，helper unit active 本身不代表核心已就绪。
核心重启后，GUI 的测速历史可能为空，C 也会阻断 GUI 测速请求。
就绪判断使用新鲜的 mixed-port 与 TUN 请求，不依赖 GUI 延迟列表。

启动时，`auto` 下候选读取或校验失败会保留完整且仍获授权的旧快照，记录 `fallback`，并用该快照启动核心。
没有有效回退对象时以 exit 78 拒绝启动。
启动器始终运行 effective 快照。
被拒候选的摘要 pin 保留一小时，普通重启不会重复校验，也不续期。
受控来源启用自动批准时，审计拒绝的候选会按当前策略重新检查。
本次启动内，受控来源的健康失败 pin 在 `clash-on` 和 `clash-off` 后保留剩余 TTL，防止再次批准同一失败候选。
这两个命令均取消待健康检查，GUI 兼容导入还会清除自身 pin。

`/run/proxy-mode/profile-refresh.json` 以 0644 原子发布，包含策略、最近结果、候选与上一摘要、固定失败原因、快照时间、上次接受时间、内容审计分类、上次自动批准，以及最多 20 条历史。
显示的摘要截断为 12 位。
记录不包含订阅 URL、凭据、节点名、私有路径或原始解析输出。

### 内容审计

`my.proxy.subscriptionAudit` 支持 `off`、`warn` 和 `enforce`，模块默认 `warn`，本机配置使用 `enforce`。
worker 在 `-t` 与快照提交之间离线运行，身份为无 capabilities 的 `clash-validate`。
`off` 只关闭附加检查，受管解析器与摘要检查始终有效。

检查范围包括节点 server 地址、未获授权的 `skip-cert-verify`、空组、无效组引用、YAML 键结构、hosts 映射和 provider 配置变化。
历史比较使用最近 settled 的 YAML。
可变 provider 内容仍遵循下文的独立信任模型。
`my.proxy.insecureProxyExceptions` 中的具名 TLS 例外保留。

有意使用非公网节点端点时须声明 `my.proxy.subscriptionAuditServerCidrs`，该审计例外不授予应用访问权限，组播端点仍被拒绝。

`my.proxy.remoteOnlyCatchAll` 默认关闭，本机启用。
Merge 脚本沿最终兜底策略及其代理组引用，移除 DIRECT 等非代理成员，支持任意订阅分组名，拒绝空组和循环引用。
这些共享分组不再提供对应的手动选择。
显式 DIRECT 规则和无关分组保留。
动态 provider 路由在 `enforce` 下仍须得到确定的审计结论。

该约束在 GUI 生成 YAML 时应用。
修改 Merge 策略后，应重新应用当前 profile，生成对应 YAML，再请求新导入。

每次导入都检查 root 显式 hosts 白名单，首次导入也不例外。
候选提供的名称和值须匹配 `my.proxy.subscriptionAuditAllowedHosts`，允许省略白名单中的条目。
默认空白名单不允许订阅覆盖 hosts。

```nix
my.proxy.subscriptionAuditAllowedHosts = {
  "service.internal" = "192.168.1.10";
};
```

`my.proxy.sensitiveDomains` 为用户选定的域名后缀启用定向检查，默认为空。
检查器支持有序的 `DOMAIN`、`DOMAIN-SUFFIX`、`DOMAIN-KEYWORD`、`MATCH` 规则及可静态确定的代理组，识别可证明的 DIRECT 到代理或拒绝变化。
同一后缀下混合路由保守返回 `unknown`，GEOSITE、GEOIP、逻辑规则和动态组也可能无法确定。
检查器不等同完整的 Mihomo 路由求值。
基础设施目标不会自动加入此列表。

`my.proxy.subscriptionAuditProtectedRoutes` 为精确域名声明预期的 `direct`、`proxy` 或 `reject` 路由。
健康和延迟探测域名默认为 `proxy`。
订阅和更新域名须在此声明各自预期路由。
无法确定的规则或代理组不能证明候选符合要求。

`enforce` 阻断 deny，以及敏感域或受保护目标路由无法确定的候选，原因分别为 `blocked by audit` 或 `audit indeterminate`。
worker 自身失败也不能在 `enforce` 下通过。
`warn` 记录结果并继续导入。
结构性警告覆盖规则或节点数量大幅变化、单一 host 集中、新增明文凭据节点和大类地理路由变化。
公开结果只包含固定分类与计数。

## 受管 profile 与控制 API

[profile.nix](../../system/config/network/proxy/profile.nix) 定义 root 策略，[cvr-merge.nix](../../home/config/clash/cvr-merge.nix) 生成匹配的 GUI Merge。
最终核心 parser 在 GUI/订阅处理后约束监听、mark、DNS、TUN 路由/排除项和私网目标设置。

Rule providers 固定为空，geodata 自动更新关闭，无条件 `MATCH,DIRECT`/`FINAL,DIRECT` 被拒绝。
其它订阅规则和组选择保留授权语义。
核心向健康消费者报告 `managed-policy` 摘要，描述系统不变量。
独立快照摘要绑定已授权 YAML 和资源。

| 控制操作 | 受管行为 |
|---|---|
| GET / HEAD / OPTIONS | 允许，包括 GUI 延迟测试 |
| PUT / DELETE `/proxies/<name>` | 选择或释放代理选择 |
| DELETE `/connections` 或 `/connections/<id>` | 关闭连接 |
| PATCH `/configs` | 空补丁、`mode` 或 `tun` 返回 204 且不应用；补丁含其它字段时返回 403 |
| PUT `/configs` | 返回 204 但不应用，GUI 重载路径不能替换系统渲染配置 |
| 其它写操作 | 默认拒绝 |

禁用规则、provider/geodata/storage 更新、重启及升级 API 均拒绝。
核心模式由系统渲染配置固定，受管解析器也会将其固定为 `rule`。
GUI forwarder 和直接控制器 socket 均经过同一项无条件限制，与策略状态无关。
升级新增写端点时需明确决定权限。

重启时，若生成配置仅系统拥有的 `mode` 和 `tun` 字段不同，则复用已接受快照；其它变化仍需 `clash-on`。
Root 策略同时指定共享域名过滤文件、附加名单与例外。
核心在 hosts/缓存/fake-IP 前检查 DNS，在 TCP/UDP 拨号前检查应用目标，使 HTTP/SOCKS 代解析使用与系统 DNS 相同的名单。
内部引导解析查询也经过过滤，但其传输与目标验证范围独立。
split DNS、原生 MagicDNS 和字面 IP 边界见 [DNS](dns_CN.md)。

## 兼容模式的订阅信任

`my.proxy.subscriptionTrust="source"` 将 proxy provider 视为可信的可更新来源。
`"fixed"` 禁止 provider，使内联节点内容纳入授权 YAML 快照。
`proxy-control preflight` 显示内联节点数、provider 数、来源主机和申请的证书例外数量，不输出订阅 URL 或凭据。

全局 merge 脚本过滤内联节点中启用 `skip-cert-verify` 的节点，并移除分组中的对应引用。
`my.proxy.insecureProxyExceptions` 点名的节点保留。
没有 provider 成员的分组被清空时，配置生成终止。
核心在 per-profile 扩展和 provider override 处理后应用同一份例外名单。

证书例外按叶节点名称匹配，provider 用同名节点替换服务器时沿用该例外。
可变 provider 与 `remoteTargetGrants` 并用还需 `allowDynamicRemoteGrants=true`。
固定模式将内联节点内容绑定到每次授权导入。

预检检查生成的配置、资源和受管解析器，单独报告证书策略拒绝并记录脱敏的准备结果。
Provider 内容由核心在加载和刷新时校验。
快照中的 root 所有 `proxies/` 链接指向可写的 `/var/lib/clash-core/proxies/`，HTTP provider 默认使用此目录，显式 provider 路径也须位于相对 `proxies/` 目录内。

HTTP provider 优先读取缓存，缓存被拒绝后尝试远端。
刷新被拒绝时保留上次接受的节点和缓存。
初始化失败期间保留声明的 fallback payload。
更新间隔为正数的 provider 自动重试。

快照快速检查比较文件身份、权限、大小及 ctime/mtime 与完整验证记录。
授权变化、元数据变化或 60 秒间隔触发完整 SHA-256 校验，启动和导入始终执行完整校验，无法观察启动身份的沙箱也执行完整校验。
快照文件及验证记录仅由受信任 root 写入。

## 路由策略

本机启用 `my.proxy.runtimeRouting=true`，不重新构建即可选择两种 B 路由子模式：

```sh
clash-rules
clash-proxy-only
```

两条命令都请求代理访问，在 A 或 C 中也可使用。终端仅返回成功及目标模式，或失败及原因。
它们不打开 GUI、不发送桌面通知，也不自动显示网络概览。
概览使用 `proxy-status`；B 中显示请求/生效路由、事务 epoch、核心实际模式和协议覆盖范围。
底层入口为 `sudo proxy-control routing rules` 和 `sudo proxy-control routing proxy-only`。
两个路由子模式渲染的核心设置均为 `mode: rule`。
这个核心设置让 Mihomo 解释渲染后的规则列表，并不选择 `rules` 或 `proxy-only`。
GUI 的 Rule/Global/Direct 是核心模式请求，不是路由子模式选择器。
这些请求被丢弃，GUI 不能把核心切离 `rule`，也不能选择路由子模式。

`rules` 保留订阅条件，包括国内 DIRECT、profile REJECT 和受管探针规则。
`proxy-only` 将应用条件统一交给经过验证的远程兜底，并保留严格浏览器入口的远程约束。
其 DNS 组引用同一兜底。最终适配器检查仍拒绝 DIRECT/COMPATIBLE，覆盖嵌套组和改名叶子。
原始规则和已固定节点独立保存，返回 rules 时从已接受的本地数据重建。
这统一选择策略，不证明不同协议或地址族具有同一个远端 NAT 地址。

切换先安装 C 并撤销 B 授权，停止旧核心和 relay 流，安装选定的不可变策略，再重建核心命名空间。
Parser、内容审计、监听身份、路由、DNS/TUN 和完整健康检查通过后才能返回 B。
命令等待共用就绪与验证条件。失败保留所请求的子模式和 C，不自动选择 A 或 rules。
重复选择已经就绪的模式不会重启核心。

`clash-off` 选择 A 并保留本次启动的路由选择，`clash-on` 复用该选择。
重启后选择恢复 rules，开机请求 A 还是代理访问仍由独立的 `bootMode` 控制。

Proxy-only 要求已有节点地址固定为字面 IP 的本地已接受来源。
每个来源首次导入须先在 rules 完成正常健康接受，之后才能离线启动严格模式。
节点 TLS 身份继续校验，切换及严格启动不需要直连 DNS 或订阅下载。
应用和来源 worker 的 DNS 都通过远程加密传输获得本地验证结果，见 [DNS](dns_CN.md#b受管-mihomo-dns)。
Proxy-only 禁用 LAN、组播、时间同步、Tailscale 和入站服务回复，仅保留限定的 DHCP、NDP 和必要传输错误。
见[严格出口](egress_CN.md#proxy-only-出口)。

可复用模块的 `runtimeRouting` 默认为 false，启用时要求受控来源、隔离 TUN、本地 DNSSEC、严格加密 DNS 和内核授权租约。
原构建期 `proxyOnly` 选项保留给不使用运行时切换的配置，只控制核心路由与 DIRECT 拒绝，宿主例外仍独立。
使用运行时切换时保持它为 false。当前支持固定端点节点的 TCP/UDP，不提供端口跳跃或真实 ICMP 隧道。

## 域名目标

受管核心在 Rule 和统一代理模式均禁用嗅探，使用显式目标和 fake-IP 映射作为域名路由身份。
ECH 掩护 SNI 不能替代拨号目标，字面 IP 使用 IP 规则。
Parser 也将旧 GUI 配置规范到这一策略，见[浏览器 ECH 发现](browser_CN.md#ech-发现与代理兼容)。

[target_guard.go](../../system/programs/clash/mihomo/guard/target_guard.go) 在 TCP/UDP 出站前校验应用目标。
公网域名必须在核心本地解析得到非空、全部为公网的答案。
解析错误允许重试一次。
选定 IP 同时固定用于 DIRECT 和远程代理拨号，不交给节点再次解析。

`privateTargetDomains` 中的内部后缀及已认证 MagicDNS 后缀可以得到私网答案，但仍需本地解析、固定地址和出口授权。
后缀本身不建立解析路由，也不能绕过共享域名过滤。

TCP 在实际选定的叶节点交错尝试已验证的 IPv4/IPv6 候选，最多八个，单次上限两秒、总截止六秒。
每个候选均需通过该叶节点的出站授权。
节点只接收固定 IP，不执行目标域名解析。
UDP 为关联固定单一目标。

核心在拨号返回错误时尝试下一候选。
拨号成功后交付连接，应用数据沿该连接传输。
乐观确认或单向代理协议以隧道建立为拨号成功点，目标地址回退在该成功点结束。

### 本地与远程授权

授权在实际选中的叶节点执行，覆盖嵌套、fallback 和负载均衡组。
远程适配器即使命名为 `DIRECT`，也不会获得直连权限。
受管解析器将核心固定为 `rule`，GUI 不能启用 Global 或 Direct 路由。
`proxyOnly` 仍独立拒绝应用 DIRECT。

| 到达核心的目标 | 本地 DIRECT | 远程代理 |
|---|---|---|
| 公网字面 IP 或已验证公网域名 | 允许，但 `proxyOnly` 可拒绝 DIRECT | 允许 |
| 私网/CGNAT/loopback/ULA/链路本地字面地址，或已批准内部后缀的私网答案 | 必须匹配 `localTargetCidrs` 且来自宿主入口 | 必须匹配 `remoteTargetGrants` |
| 清单内元数据端点、未指定、组播、选定保留/过渡地址或原始 fake-IP | 拒绝 | 拒绝，授权条目也不能放行 |
| 已知 NAT64 翻译地址 | 内嵌 IPv4 必须通过公网地址检查 | 相同。不能授权内嵌私网目标 |

`localTargetCidrs` 默认采用共享本地/私网范围。
宿主入口对应核心 metadata 的 `HTTP`、`HTTPS`、`SOCKS4`、`SOCKS5`、`REDIR` 和 `TUN`。
访客 `TPROXY` 不继承默认本地权限。
设为 `[]` 可移除此项核心权限，但不改变原生宿主防火墙授权、访客转发或 A 的 Gost 直连透传。
远程授权默认为空。

需要访问节点网络内的服务时，明确叶节点名称、规范内部 CIDR、核心入口、传输协议和目标端口：

```nix
my.proxy.remoteTargetGrants = [{
  proxy = "Office node";
  cidr = "10.20.30.40/32";
  inlets = [ "SOCKS5" ];
  network = "tcp";
  ports = [ 443 ];
}];
```

此授权允许节点按其所在网络解释该 IP，不授予远端 DNS 解析，也不扩大到其它节点、入口、协议或端口。
使用域名时，还需声明内部后缀，并通过已授权 hosts 或核心解析器提供答案。
授权匹配配置中的叶节点名称，不认证远端服务器身份。
应审阅已授权配置/provider 来源，避免将已授权名称复用于其它节点。

入口以核心实际收到的流量为准：Gost 转发的 CONNECT 是 `HTTPS`，来自其透明入口的转发也一样。
它不认证应用身份，也不保留原应用 UID。

复用 UDP association 时，每个新目标都按该连接实际叶节点检查并固定。
缓存映射保留目标来源，每个包重新核对当前入口、目标端口和出口授权，换端口不能复用较窄授权。
已有 TCP 连接不会被追溯检查。
策略变化后应重建并有意导入/重启核心。

### 地址类别与模式

IPv4-mapped 地址在授权前规范为 IPv4。
不可授权的元数据清单包括 `169.254.169.254`、`169.254.170.2`、`100.100.100.200` 和 `fd00:ec2::254`。
带 zone 的 IPv6 字面地址被拒绝。

始终识别知名前缀 `64:ff9b::/96`。
`nat64Prefixes` 可声明其它 [RFC 6052](https://www.rfc-editor.org/rfc/rfc6052.html) 翻译前缀，长度限 32、40、48、56、64、96，重叠时采用更精确前缀。
内嵌公网 IPv4 可用，私网、元数据和合成目标拒绝连接。
本地用途 `64:ff9b:1::/48` 只有显式声明为翻译前缀后才按内嵌地址处理。

fake-IP 池只表示路由身份，不能成为实际出站目标。
必须先通过有效映射恢复域名，再执行过滤、解析和授权。
缺失映射、真实 DNS 答案携带合成目标均被拒绝，自定义地址池也受检查。

这些规则约束到达核心的应用流量，通常处于 B。
A 的 Gost 和原生 socket 使用 A 的防火墙策略，保留字面 HTTP 门户访问。
C 在核心之前阻断普通应用，本地及远程授权都不能重新放行。

内部引导、代理端点传输及 `#DIRECT` MagicDNS 有独立的基础设施权限，按规则路由的 DNS 不继承这些例外。
原生 LAN 和访客转发也有各自策略。
核心只能管理私有网络命名空间，宿主网络由 root 服务管理。

内部引导、代理端点传输及 `#DIRECT` MagicDNS 有独立的基础设施权限，按规则路由的 DNS 不继承这些例外。
原生 LAN 和访客转发也有各自策略。
核心只能管理私有网络命名空间，宿主网络由 root 服务管理。

## Gost 生命周期

[default.nix](../../system/programs/gost/default.nix) 遵循模式原子状态，绑定前回收旧进程，不使用 `SO_REUSEPORT`，每端点要求唯一监听，并限制文件描述符、内存和任务数。
B 信任监督器健康结论并检查 mixed 监听身份。
以被动 socket 观测代替连接透明入口，自连接检测作为后备。

Relay 兼容带有效端口的裸数字 IPv6 CONNECT，保留 Go HTTP framing 和缓冲中的隧道数据。
建议客户端使用标准 `[IPv6]:port`。
Gost 通过 store 绝对路径运行，不放进系统 PATH 或 initrd。
源码及打包自定义见[维护手册](maintenance_CN.md)。
