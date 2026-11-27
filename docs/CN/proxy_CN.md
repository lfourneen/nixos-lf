# 代理核心与权限

[English](../proxy.md) | 简体中文 | [文档目录](README_CN.md)

## 受控订阅来源

本机选择 `controlledSources=true`、`subscriptionTrust="fixed"`、`profileRefreshPolicy="auto"` 和阻断式内容审计。
可复用模块在关闭受控来源时保留旧 GUI 导入方式。
下文 GUI 导入和可变 provider 的说明属于该兼容路径。

`clash-source` 通过仅 root 可操作的 broker 登记来源，同时选择一个来源。
每个来源分别保存订阅、provider、版本、批准和已接受的基线。
`add <id>` 审核并登记清单条目，然后下载、选择并验证它。
`use <id>` 对已登记来源执行同样的即时下载和验证。
GUI 只能请求映射到 root 批准 `gui-profile` UID 的来源。Broker 下载后重新核对映射和登记，再提交选择。

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
| `url` | 延迟测试地址：HTTPS 或默认端口 80 的 HTTP，不带凭据和片段 |
| `interval` | 1–86400 秒的整数 |
| `tolerance` | 0–60000 ms |
| `timeout` | 100–60000 ms |
| `max-failed-times` | 1–100 |
| `lazy` | 布尔值 |
| `expected-status` | HTTP 状态表达式 |

不支持 `load-balance`、动态 filter、`rule-providers` 和 provider filter/override。
受控来源要求节点的数据通道受到保护。HTTP 和 SOCKS5 须启用认证 TLS，且仅可用于 TCP。SOCKS5 的 TLS 保护 TCP 上的 association 协商，原生 UDP 数据报仍是明文。
Shadowsocks 须使用允许的 AEAD 算法及共享密钥。VMess 须使用认证 TLS 或 VMess AEAD（`alterId: 0`、允许的算法及有效 UUID）。VLESS 须在受支持的传输上使用认证 TLS。Trojan、Hysteria2、TUIC 和 AnyTLS 使用各自受支持的认证传输。
来源渲染器剔除传输方式不受支持的节点。若过滤后没有可用叶子，或破坏必要路由或分组，会拒绝候选。受管核心独立检查实际应用的配置，包括恢复的快照。
AnyTLS 只参与 Rule 路由，不列入固定出口候选（单叶子校验器仍只接受上表之外的既有协议）。
需要通过 DNS 查询 ECH 配置的节点排除在 `lf-remote`、受管 DNS 组 `lf-dns` 和固定出口候选之外，避免 DNS 传输叶子拨号时发生循环解析。
这些节点仍可在订阅分组中使用，由其他叶子传输加密查询。内联 `ech-opts.config` 的节点仍可进入受管组。

私有 manifest 记录来源 ID、版本、响应摘要、转换器、策略、生成的 YAML、丢弃节点数及 geodata。
快照摘要包含这份 manifest。

登记、候选及批准保存在 root 私有的 `/var/lib/clash-source/sources/<id>`。
`selected` 是当前选择的原子指针，由共享 `source.lock` 保护。
旧单来源状态在下一次 broker 操作时迁入此布局。
各来源的 `approved/` 快照跨切换和重启保留。

下载失败保留原选择，解析、审计或健康失败保留此前授权的运行快照。
因此，请求的选择可能暂时与 `proxy-status` 中的 `Active source` 不同。

应用服务在中断已就绪的核心前检查已下载的候选。
来源、策略和出口仍获授权，且有效快照、核心进程与 B 就绪状态均通过检查时，原始响应只有格式或注释变化的刷新保留原核心和已有连接。渲染后的规则顺序、节点、凭据和资源均参与比较。候选被拒时仍可沿用符合这些条件的运行快照。
一次 HTTP 观测失败不触发等价更新换核。监督器继续独立检查健康；只有完整的新证明才能延长内核租约。正常续租提交期间，身份中继可使用同一代上次已确认租约的剩余时间。
应用变化的候选先进入 C，再重启核心，并须重新通过启动检查。
健康探测前，协调器按已暂存快照的摘要检查传输防火墙。在 `proxy-only` 下，这会装入候选的固定端点，同时保持应用授权租约撤销。
没有兼容的回退快照时保持 C。
路由事务中断时保留代理意图，并将未完成的路由恢复到可验证状态。可使用 `clash-on` 重试。

Rules 下 mixed HTTP 失败时，健康检查只临时测试或切换 DNS 叶。`lf-remote` 应用出口保持用户选择，失败时 fail-closed，不静默换节点或改变出口 IP。DNS 临时选点只有在实际 DNS 和路由检查通过后才提交，失败及超时会恢复原选择。期间的手动选点优先。TUN 先验证最早完成 DNS 的候选，验证成功后取消其余查询。
root 控制器可通过配置中的字面 IP DoH 端点抽查节点的 TLS 会话。Rules 健康且当轮探测时间充足时，每分钟最多复查一次 DNS 选点。当前 DNS 叶的会话失败时，可切换到能连接 DoH 端点且试选后取得验证应答的 DNS 叶；应用出口不随之切换。就绪仍由 DNS 应答、实际 mixed HTTP、路由证明及其余检查共同决定。
`proxy-status` 以短标识显示观测到的 DNS 叶子。明确的认证拒绝显示为 `node-auth`，DoH 端点不可达显示为 `session-unavailable`。会话证据不明确或样本中存在可用会话时，解析探测失败显示为 `validator-dns`。DoH 超时的叶仍可参加 HTTP 试测。Rules 恢复会跨轮抽查授权列表的不同位置。
B 中也可通过非特权桌面 UID 诊断系统解析器。
远端故障时，有效的 Rules B 租约可继续承载规则已授权的流量，`proxy-status` 会显示远端降级及已取得的国内解析和 DIRECT 路由结果。降级期间沿用原截止时间，下一轮仍须完整通过才可续租。

普通 Firefox 报“找不到服务器”时，在桌面终端运行 `getent ahosts www.bilibili.com` 检查系统解析，再用 `curl --noproxy '*' -IsS --max-time 6 https://www.bilibili.com/` 检查普通 HTTPS。在 Verge 连接记录中查看该域名本次实际命中的 DIRECT 或代理链。要单独验证身份入口，经同一个 Unix 中继完成 CONNECT 与证书验证：`curl --unix-socket /run/clash-strict/proxy.sock --noproxy '' -x http://localhost:80 -IsS --max-time 6 https://search.nixos.org/`。这些访问仅在手动执行时发生。

`clash-off` 撤销运行授权并结束当前路由代次。A 中路由选择显示为 `inactive`，来源已接受的基线保留供下次启动使用。
自动刷新模式下，下次 `clash-on` 优先使用符合当前来源修订、受管策略及渲染参数的已接受快照。复用该快照时记录 `restored`。
恢复时仍重复解析、内容审计和摘要检查，进入 B 仍需新鲜的实际健康证明。
共享黑名单使用稳定的策略路径，已校验的名单更新不会使已接受订阅基线失效。
协调器合并启动和定时更新请求，并在 C 中保留这些请求。
B 在同一次启动、快照、策略和路由选择下持续健康 90 秒后，协调器启动下载服务。
已映射的 GUI 请求遵循同一下载条件。A 中直接下载，供下次代理启动使用。
B 中下载成功会按候选摘要排队应用。如果导入或回滚正在进行，应用服务会等待，重新核对选择、登记修订和摘要后再处理同一候选。
等待和下载期间，前台核心可继续启动。`clash-off` 会取消队列中的下载和应用请求。
启用运行时路由时，可在 C 中显式应用已有下载，或在短期内核租约下下载新候选。见[恢复已登记来源](operations_CN.md#恢复已登记来源)。
尚无已接受基线的首次导入使用已有候选，并经过正常健康观察期。
停止再启动不会豁免结构检查。

节点身份、认证和传输变化与该来源自己的已接受基线比较，切换来源或重启后也保留比较依据。
本机自动批准 `node_identity`、`node_authentication` 和 `node_transport` 变化，包括逻辑服务器名与 TLS 名称轮换。
重启后首次 `clash-on` 和 root 定时刷新使用相同策略，日常更新无需人工审阅。

可复用选项 `my.proxy.subscriptionAuditAutoApprove` 默认为空列表，即关闭自动批准。
自动批准要求启用受控来源、`profileRefreshPolicy="auto"`、`subscriptionAudit="enforce"`，且已有接受基线。
所有 deny 类别都必须属于配置列表，并且不能有未确定的审计结果。
审计跨节点名称比较权限。仅改名且权限相同的节点沿用原有判定。没有等价权限基线的新节点必须同时满足三类自动批准条件。
私网或元数据地址、跳过证书验证、明文凭据、未经本地许可的 hosts 覆盖、受保护路由变化及格式错误仍阻断导入。
敏感路由两个方向的变化也不参与自动批准。

自动批准记录有效期五分钟，绑定候选、基线、来源版本、受管策略、验证器策略和审计策略。
实际导入重复执行解析、批准记录校验和阻断审计，再经过健康接受或回滚。
将选项设为 `[]` 即关闭自动批准，旧记录的校验要求保持不变。
见[来源登记](operations_CN.md#来源登记)。

来源下载和节点地址固定使用 `127.0.0.1:1056` 的过滤后真实地址解析器，不受 B 模式 fake-IP 答案影响。
只有专用 `clash-fetch` 账户和 root 可以直接查询该端口。
Worker 仍拒绝非公网答案，并验证 HTTPS 证书。
订阅下载、固定节点端点和 C 中的恢复均要求原生地址，并拒绝配置在 `nat64Prefixes` 下的合成地址。
一个地址家族不可用时，可以使用另一个家族的有效答案。
下载先检查完整地址集合，再在同一截止时间内重试公网地址，保留 HTTPS 主机名和证书验证。
受控来源可为使用 TCP 传输的节点保留最多两个备用公网地址。首次拨号失败或超过 250 毫秒时，可以尝试下一地址，同一拨号最多同时尝试两个。TLS 仍按原节点名称和策略验证。已登记的固定出口必须显式选择包含这些地址的节点身份。既有单地址授权、WireGuard relay 和使用原生 UDP 或 QUIC 的连接继续使用固定端点。
网站 TCP 的地址也按此方式尝试，但只使用该目标经本地策略验证的地址。只向最终选中的连接转发应用数据。
没有可用答案时刷新失败，不回退到系统 DNS。
Rules 中的新查询使用基础设施 Unbound 的 DoT 路径。`unboundDotInProxy=false` 时，该路径只返回符合条件的缓存或本地答案。运行时 `proxy-only` 使用核心应用验证器。[解析路径](dns_CN.md#受限-socket-访客-dns)

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

桌面控制器可读取核心的实时日志和连接元数据，包括目标名称与代理链。root helper 将核心日志文件写入仅 root 可访问的状态目录 `/var/lib/clash-verge-service/logs`，按 10 MiB 上限和八个文件的设置轮转。系统 journal 采用独立的保留策略。

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

首次导入遇到 DNS 或控制器观测失败时，候选留在验证期，恢复后可由健康检查接受。明确拒绝且没有可回退基线时，系统清除生效快照指针及其授权。
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

检查范围包括节点 server 地址、`skip-cert-verify`、空组、无效组引用、YAML 键结构、hosts 映射和 provider 配置变化。
历史比较使用最近 settled 的 YAML。
用 `subscriptionAuditProtectedRoutes` 声明指定域名所需的路由类别，首次导入也会检查。用 `sensitiveDomains` 比较后缀相对于已接受配置的路由类别。
可变 provider 内容仍遵循下文的独立信任模型。
Sops 加密的 `insecure_sources` 清单控制是否在解析节点地址前提前删除启用了 `skip-cert-verify` 的节点。
解析失败与回环 IP 字面量占位节点共用过滤后节点数五分之一的丢弃上限，向下取整，上限至少为一个。
每行写一个已登记的来源 ID，允许空行和重复 ID。
文件缺失、无法读取、UTF-8 无效或任一 ID 格式错误时，所有来源的清洗授权均失效。
未授权时，worker 保留这些节点交给后续检查。[Sops 白名单](../../system/secrets/whitelist.yaml)

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
当前每个受保护精确域的路由由候选独立证明。已有 settled 基线时，仍用它比较敏感订阅后缀的路由变化。
无法确定的规则或代理组不能证明候选符合要求。

`enforce` 阻断 deny，以及敏感域或受保护目标路由无法确定的候选，原因分别为 `blocked by audit` 或 `audit indeterminate`。
worker 自身失败也不能在 `enforce` 下通过。
`warn` 记录结果并继续导入。
结构性警告覆盖规则或节点数量大幅变化、单一 host 集中、新增明文凭据节点和大类地理路由变化。
公开结果只包含固定分类与计数。

## 受管 profile 与控制 API

[profile.nix](../../system/config/network/proxy/profile.nix) 定义 root 策略，[cvr-merge.nix](../../home/config/clash/cvr-merge.nix) 生成匹配的 GUI Merge。
最终核心 parser 在 GUI/订阅处理后约束监听、mark、DNS、TUN 路由/排除项和私网目标设置。
Mihomo 建立出站 TCP 连接时会并发尝试可用地址。在核心命名空间内，出站传输绑定到通往宿主的 veth 接口 `lf-host`。
受管解析器对缓存 profile 也应用这两项设置，GUI 无法通过控制器修改它们。

Rule providers 固定为空，geodata 自动更新关闭，无条件 `MATCH,DIRECT`/`FINAL,DIRECT` 被拒绝。
其它订阅规则和组选择保留授权语义。
核心向健康消费者报告 `managed-policy` 摘要，描述系统不变量。
独立快照摘要绑定已授权 YAML 和资源。

| 控制操作 | 受管行为 |
|---|---|
| GET / HEAD / OPTIONS | 可读取。会发起网络流量的桌面 DNS 查询和测速需要 B 授权 |
| PUT / DELETE `/proxies/<name>` | rules 中选择或释放代理选择。运行时 proxy-only 返回 403，出口由 root 控制 |
| DELETE `/connections` 或 `/connections/<id>` | 关闭连接 |
| PATCH `/configs` | 空补丁、`mode` 或 `tun` 返回 204 且不应用；补丁含其它字段时返回 403 |
| PUT `/configs` | 返回 204 但不应用，GUI 重载路径不能替换系统渲染配置 |
| 其它写操作 | 默认拒绝 |

禁用规则、provider/geodata/storage 更新、重启及升级 API 均拒绝。
核心模式由系统渲染配置固定，受管解析器也会将其固定为 `rule`。
两个控制器 socket 采用同一套写入策略。直接 Unix socket 通过对端凭据识别 root 恢复探针。
升级新增写端点时需明确决定权限。

桌面节点、分组和 provider 测速使用 `my.proxy.delayTestUrl` 指定的 HTTPS 端点，匹配完整路径和查询参数。控制器将 Verge 的内置回退 URL 映射到该端点，每次拨号最多等待 10 秒，批量请求另有最多 30 秒的调度时间。只有 HTTP 成功响应计入测速，分组和 provider 批量请求跳过内置直连适配器。
桌面主动查询 DNS 和测速需要当前 B 授权。授权到期或撤回时，控制器取消仍在进行的请求。C 中仍可读取状态和现有测速结果。通过私有 Unix 入口发起的 root 恢复查询仅使用已配置的探针域名。

受管核心采用 Verge 默认的统一延迟。能复用连接并成功完成第二次请求时，显示该次请求的耗时。恢复探针取得首次成功响应即结束。新建应用连接的首包仍受冷 DNS、拨号和首次 TLS 握手影响。

本机通过 `my.proxy.delayTestUrl`（本机为 `https://www.gstatic.com/generate_204`）测量桌面延迟。同一轮监督器探测确认主站失败、备用站经预期代理成功并通过完整健康检查时，桌面测速可在该证明的 60 秒有效期内使用 `my.proxy.delayTestFallbackUrl`。GUI 将控制器拒绝的测速也显示为 0；出现 0 时，可用 `proxy-status` 查看桌面测速的授权状态，等租约恢复后重测。监督器继续通过 mixed HTTP 和 TUN 验证配置的域名目标。

桌面测速共用 64 个拨号槽位，每秒可启动 64 次，允许突发 128 次。不同分组或 provider 最多并行执行四批，每批最多 8 个拨号。自动测速独立使用 10 个槽位，每秒启动 10 次。监督器以 root 身份直连 Unix 控制器发起的健康测试有独立配额。固定出口测速期间保留已选择的节点。

重启时，若生成配置仅系统拥有的 `mode` 和 `tun` 字段不同，则复用已接受快照；其它变化仍需 `clash-on`。
Root 策略同时指定共享域名过滤文件、附加名单与例外。
核心在 hosts/缓存/fake-IP 前检查 DNS，在 TCP/UDP 拨号前检查应用目标，使 HTTP/SOCKS 代解析使用与系统 DNS 相同的名单。
内部引导解析查询也经过过滤，但其传输与目标验证范围独立。
split DNS、原生 MagicDNS 和字面 IP 边界见 [DNS](dns_CN.md)。

## 兼容模式的订阅信任

`my.proxy.subscriptionTrust="source"` 将 proxy provider 视为可信的可更新来源。
`"fixed"` 禁止 provider，使内联节点内容纳入授权 YAML 快照。
预检显示内联节点数、provider 数、来源主机和证书校验选项统计。订阅 URL 和凭据保持私有。
启用受控来源时，使用 `sudo proxy-control preflight` 读取 root 所有的来源状态。

对于受控来源之外的兼容导入，全局 merge 脚本过滤内联节点中启用 `skip-cert-verify` 的节点，并移除分组中的对应引用。
没有 provider 成员的分组被清空时，配置生成终止。
核心在处理 per-profile 扩展和 provider override 后拒绝绕过证书校验。将 `my.proxy.insecureProxyExceptions` 保持为空，为节点配置可信证书或 CA。
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
B 和 C 中，`proxy-status` 显示路由选择、事务 epoch、观测到的核心模式和协议覆盖范围。
底层入口为 `sudo proxy-control routing rules` 和 `sudo proxy-control routing proxy-only`。
两个路由子模式渲染的核心设置均为 `mode: rule`。
这个核心设置让 Mihomo 解释渲染后的规则列表，并不选择 `rules` 或 `proxy-only`。
GUI 的 Rule/Global/Direct 是核心模式请求，不是路由子模式选择器。
这些请求被丢弃，GUI 不能把核心切离 `rule`，也不能选择路由子模式。

`rules` 保留订阅条件，包括国内 DIRECT、profile REJECT 和受管探针规则。
运行时 `proxy-only` 将支持的应用流量固定到已接受来源的远程兜底中的一个叶节点。
有效快照只包含该节点及单成员的 `lf-remote` 选择组。原生 TCP/UDP、显式代理、严格 Firefox 和两个 DNS 验证器的传输均使用该叶子。
自动测速、fallback 和 GUI 选组不能更换它。最终适配器仍拒绝 DIRECT/COMPATIBLE。
原始规则、分组和全部已固定来源节点独立保存，返回 rules 时恢复订阅分流。

使用 `clash-egress` 列出候选，再用列表返回的完整 ID 替换 `APPROVED_NODE_ID`，执行 `clash-egress APPROVED_NODE_ID`。
底层入口为 `sudo proxy-control egress list` 和 `sudo proxy-control egress APPROVED_NODE_ID`。
本次启动尚未选择出口时，首次进入严格模式使用兜底图中按顺序可达的第一个远程叶子。
出口变更复用 C 事务，关闭旧 TCP 流和 UDP association，并要求新的完整健康及授权证明。
重复选择已就绪的相同 ID 保持幂等。节点故障进入 C，不另选节点。

出口 ID 绑定完整的固定节点定义。健康、租约、刷新和回滚除路由 epoch 与快照外，还绑定 `egress_requested` 和 `egress_epoch`。
Off/on 及返回 rules 保留本次启动的严格出口选择，重启清除它。
来源更改所选节点的地址、凭据或传输后，旧 ID 不授权替代节点。先在 rules 接受更新后的来源，再选择新 ID。

系统重建可能替换当前路由策略或其健康检查产物。如果运行时选择仍属于旧一代系统，代理保持阻断。运行 `clash-rules` 撤销旧选择并启动新的 rules 事务，然后用 `clash-egress` 为新策略选择严格出口。

`proxy-status --json` 为严格选择报告 `capabilities.egress="fixed-leaf"`。
`capabilities.udp_declared` 显示所选适配器的 UDP 声明，缺少该信息时留空。默认在适配器不支持 UDP 时显示 `unsupported`，其余情况显示 `not-probed`。IPv6 目标能力取决于节点，不支持的请求不能转到另一叶子或 DIRECT。

需要验证 UDP 的主机可将 `my.proxy.udpHealth.address` 和 `my.proxy.udpHealth.port` 设为已批准的公网 UDP 回显端点，地址使用字面 IP。透明 Proxy-only 就绪后，每两分钟以普通桌面进程的身份发送一个随机数据报。`capabilities.udp` 报告当前出口的 `verified`、`no-reply` 或 `error`，结果在 150 秒后到期。这个结果只用于诊断，应用授权租约、自动刷新和选点均独立运行。
`capabilities.remote_nat="not-measured"` 明确远端边界：同一节点仍可能为 TCP/UDP 或不同目标使用不同 NAT 地址。
单一 NAT 保证需要独立的目标观测。IPv4 和 IPv6 可以分别声明出口地址。

切换先安装 C 并撤销 B 授权，停止旧核心和 relay 流，安装选定的不可变策略，再重建核心命名空间。
Parser、内容审计、监听身份、路由、DNS/TUN 和完整健康检查通过后才能返回 B。
命令等待共用就绪与验证条件。失败保留所请求的子模式和 C，不自动选择 A 或 rules。
重复选择已经就绪的模式时，如果运行快照仍符合当前来源修订和策略，就保留现有核心。

`clash-off` 选择 A 并保留本次启动的路由选择，`clash-on` 复用该选择。
重启后选择恢复 rules，开机请求 A 还是代理访问仍由独立的 `bootMode` 控制。

Proxy-only 要求当前来源修订已有本地接受快照，且节点地址固定为字面 IP。
新修订先在 rules 完成健康接受，再离线启动严格模式。
节点 TLS 身份继续校验，切换及严格启动不需要直连 DNS 或订阅下载。
应用和来源 worker 的 DNS 都通过远程加密传输获得本地验证结果，见 [DNS](dns_CN.md#b受管-mihomo-dns)。
Proxy-only 禁用 LAN、组播、时间同步、Tailscale 和入站服务回复，仅保留限定的 DHCP、NDP 和必要传输错误。
见[严格出口](egress_CN.md#proxy-only-出口)。

可复用模块的 `runtimeRouting` 默认为 false，启用时要求受控来源、隔离 TUN、本地 DNSSEC、严格加密 DNS 和内核授权租约。
原构建期 `proxyOnly` 选项保留给不使用运行时切换的配置，只控制核心路由与 DIRECT 拒绝，宿主例外仍独立。
使用运行时切换时保持它为 false。应用代理传输支持固定端口的 TCP/UDP 节点。

### 包含真实 ping 的 WireGuard 出口

配置 `my.proxy.ipTunnel` 后，运行时 `proxy-only` 使用受控的 WireGuard 出口。系统 DNS 返回本地验证过的真实地址，`ping 域名` 和 `ping IP` 都可发送原生 echo 请求。核心和严格 Firefox 使用同一个 WireGuard 对端上的固定 SOCKS5 relay。对端在 IP 层转发 ICMP，并发起代理的 TCP/UDP 连接。

切换前先配置两端。

1. 在远端为客户端配置 IPv4 和 IPv6 两条隧道主机路由，开启双栈转发，并允许相关 ICMP 错误，包括 IPv4 需要分片和 IPv6 Packet Too Big。让远端转发的 echo 与 SOCKS5 relay 在每个地址族使用相同的公网源地址。
2. SOCKS5 relay 使用隧道内可达的公网字面地址，可以与 WireGuard 外层端点共用地址。远端防火墙将 relay 入站访问限制于 WireGuard 接口和客户端地址。SOCKS5 UDP association 须返回配置中的 relay 地址和固定端口。将此节点登记到来源的远程兜底组，设置 `udp: true`。该 relay 仅作为所配 WireGuard 出口的固定叶子使用。兜底组还需保留一个可达且符合传输要求的节点，用于首次接受来源和 Rules 模式。
3. 在客户端声明外层端点、对端公钥、客户端地址、relay，以及位于远端之后的两个受控公网 echo 目标。客户端私钥放在仅 root 可读的运行时文件中，可由 sops 提供。

```nix
my.proxy.ipTunnel = {
  endpointAddress = "<对端公网地址>";
  endpointPort = 51820;
  listenPort = 51821;
  publicKey = "<对端 WireGuard 公钥>";
  privateKeyFile = config.sops.secrets."wireguard-exit-key".path;
  clientIpv4 = "172.31.77.2";
  clientIpv6 = "fd42:77::2";
  relayAddress = "<relay 公网地址>";
  relayPort = 1080;
  probeIpv4 = "<受控 IPv4 echo 目标>";
  probeIpv6 = "<受控 IPv6 echo 目标>";
  mtu = 1380;
};
```

应用配置后运行 `clash-proxy-only`。`clash-egress` 列出声明的 relay 上兼容的叶子。内核路由、双栈 echo 及现有应用和 DNS 检查全部通过后，`proxy-status --json` 报告 `capabilities.icmp="verified-dual-stack"`。隧道故障时保持 C。`clash-rules` 和 `clash-off` 会移除 WireGuard 接口及其路由。

状态会显示当前 WireGuard 健康阶段，按地址族标明路由或 echo 探测失败。双栈 echo 均失败时，还会显示是否有近期握手。未收到 echo 回包时，每个地址族在同一健康预算内可重试一次，双栈都通过才发放或更新租约。旧策略仍处于选中状态时，使用 `clash-rules` 恢复事务，再选择配置更新后的出口。

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

内部引导、代理端点传输及已认证 MagicDNS 有独立的基础设施权限，按规则路由的 DNS 不继承这些例外。
原生 LAN 和访客转发也有各自策略。
核心只能管理私有网络命名空间，宿主网络由 root 服务管理。

## Gost 生命周期

[relay.nix](../../system/programs/gost/relay.nix) 遵循模式原子状态，绑定前回收旧进程，不使用 `SO_REUSEPORT`，每端点要求唯一监听，并限制文件描述符、内存和任务数。
B 信任监督器健康结论并检查 mixed 监听身份。
以被动 socket 观测代替连接透明入口，自连接检测作为后备。

Relay 兼容带有效端口的裸数字 IPv6 CONNECT，保留 Go HTTP framing 和缓冲中的隧道数据。
建议客户端使用标准 `[IPv6]:port`。
Gost 通过 store 绝对路径运行，不放进系统 PATH 或 initrd。
源码及打包自定义见[维护手册](maintenance_CN.md)。
