# 代理核心与权限

[English](../proxy.md) | 简体中文 | [文档目录](README_CN.md)

## 受控订阅来源

本机启用了 `controlledSources=true`、`subscriptionTrust="fixed"`、`profileRefreshPolicy="auto"` 和阻断式内容审计。可复用模块在关闭受控来源时保留了旧的 GUI 导入路径——后文提到的 GUI 导入和可变 provider 说明就是针对那条兼容路径的。

`clash-source` 通过仅 root 可操作的 broker 登记来源，同一时间只选中一个来源，各自独立保存订阅、provider、版本、批准和已接受基线。`add <id>` 会审核并登记清单条目，接着下载、选中并验证它；`use <id>` 则对已登记来源直接执行下载和验证。GUI 只能请求映射到 root 批准 `gui-profile` UID 的来源，broker 在下载后还会重新核对映射和登记才提交选择。

实际下载 HTTPS 订阅和已登记 HTTP provider 的是一个无特权 worker，它会固定公网端点地址并渲染出配置——监听、DNS、TUN 设置和 geodata 由 Nix 提供，provider 也会展开成内联节点。GUI 里的 URL、YAML、脚本和缓存都无权授权新来源。

Sops 的 `proxy_source` 字符串可以用 `{"sources":[...]}` 存下所有来源定义（旧版单来源 JSON 依然兼容）。`review/register FILE --id ID` 用来挑出其中一条；不带 `--id` 的 `review` 会列出清单，而 `register` 在清单有多条时会拒绝歧义选择。ID 和 GUI 映射必须唯一，登记摘要只覆盖选中的那一条，往清单里追加新条目既不会改动已有摘要，也不会自动批准新来源；从清单删掉条目同样不会撤销本地已登记的副本。

下载环节强制正常 TLS 主机名校验、固定公网 socket 地址和精确批准的 HTTPS 跳转，限制单响应最多 8 MiB、合计 32 MiB 并受 worker 时限约束。默认 User-Agent 是 `clash.meta`，每个端点可以登记最长 256 字符、单行 ASCII 的 `user-agent`（跨 origin 跳转后会永久清除自定义 UA 和凭据）。YAML 别名和重复键一律拒绝。

节点域名会固定地址并保留 TLS 身份；解析失败最多丢掉 `max(1, node_count // 5)` 个节点，但如果出现非公网答案或被规则引用的空组，仍会拒绝整个候选。渲染器支持 `select`、`url-test` 和 `fallback` 分组，成员必须明确列出或引用已登记 provider：

| 字段 | 接受的值 |
|---|---|
| `url` | 延迟测试地址：HTTPS 或默认端口 80 的 HTTP，不带凭据和片段 |
| `interval` | 1–86400 秒的整数 |
| `tolerance` | 0–60000 ms |
| `timeout` | 100–60000 ms |
| `max-failed-times` | 1–100 |
| `lazy` | 布尔值 |
| `expected-status` | HTTP 状态表达式 |

不支持 `load-balance`、动态 filter、`rule-providers` 以及 provider filter/override。受控来源要求节点的数据通道必须受保护：HTTP 和 SOCKS5 须启用认证 TLS 且仅限 TCP（SOCKS5 的 TLS 保护 TCP 上的 association 协商，原生 UDP 数据报仍是明文）；Shadowsocks 须用允许的 AEAD 算法和共享密钥；VMess 须用认证 TLS 或 VMess AEAD（`alterId: 0`、允许的算法及有效 UUID）；VLESS 须在受支持传输上用认证 TLS；Trojan、Hysteria2、TUIC 和 AnyTLS 使用各自受支持的认证传输。渲染器会剔除不受支持的变体，若删完后没有可用节点或破坏了必要路由/分组就拒绝候选；受管核心也会独立复核实际应用的配置（包括恢复出来的快照）。AnyTLS 只参与 `Rule` 路由，不列入固定出口候选（单叶子校验器保持原有协议清单）。需要靠 DNS 查 ECH 配置的节点也会排除在 `lf-remote`、受管 DNS 组 `lf-dns` 和固定出口候选之外，免得 DNS 传输叶子拨号时循环解析——它们在订阅分组里仍可使用，内联了 `ech-opts.config` 的节点也可以进受管组。

私有 manifest 会记下来源 ID、版本、响应摘要、转换器、策略、生成的 YAML、丢弃节点数和 geodata，并一起算进快照摘要。登记、候选和批准保存在 root 私有的 `/var/lib/clash-source/sources/<id>` 下，由共享 `source.lock` 保护原子的 `selected` 指针（旧的单来源状态会在下次 broker 操作时自动迁过来）。

已批准的快照跨切换和重启保留。下载失败保留原选择，解析、审计或健康失败则保留此前授权的运行快照——所以在检查跑完之前，`proxy-status` 里的 `Active source` 可能会暂时和请求的选择不一致。

应用服务在打断就绪核心前会先检查下载好的候选。如果一次刷新只是改了原始响应的格式或注释——也就是渲染后的规则顺序、节点、凭据和资源都和已授权基线一致，且来源、策略、出口、核心进程与 **B** 就绪状态都正常——就会保留原核心和已有连接（候选被拒时也可以继续沿用符合条件的运行快照，一次瞬时 HTTP 观测失败也不会触发等价更新换核；不过监督器仍要拿到完整新证明才能延长内核租约，续租提交期间身份中继可以继续用同一代上次已确认租约的剩余时间）。真正改变配置的候选会先切进 **C** 再重启核心并重跑启动检查；健康探测前，协调器会按暂存快照摘要复核传输防火墙（在 `proxy-only` 下装入候选的固定端点，同时保持应用租约撤销）。没有兼容回退就停在 **C**；路由事务若中途打断，会保留代理意图并把未完成路由恢复到可验证状态，用 `clash-on` 重试即可。

在 `rules` 模式下，如果 mixed HTTP 失败，健康检查只会临时测试或切换 DNS 叶——`lf-remote` 应用出口始终保持用户选的节点，失败时 fail-closed，绝不静默换节点或改出口 IP。临时 DNS 选点只有在实际 DNS 和路由检查都通过后才提交，失败或超时会退回原选择，期间的手动选点优先。TUN 检查会先验证最早完成 DNS 的候选，成功后取消其余查询。root 控制器还可以通过配置里的字面 IP DoH 端点抽查节点 TLS 会话：在 `rules` 健康且探测时间充足时，每分钟最多复查一次 DNS 选点，当前 DNS 叶会话失败时就切到另一个能连上 DoH 端点且试选能拿到验证应答的 DNS 叶（不换应用出口）。就绪依然由 DNS 应答、实际 mixed HTTP、路由证明和其余检查共同决定。`proxy-status` 会用短标识标出观测到的 DNS 叶子：明确认证拒绝显示为 `node-auth`，DoH 端点不可达显示为 `session-unavailable`，会话证据不明确或样本里有可用会话时解析探测失败显示为 `validator-dns`（DoH 超时仍允许该叶参加 HTTP 试测，`rules` 恢复也会跨轮抽查授权列表的不同位置）。**B** 下还能通过非特权桌面 UID 诊断系统解析器。远端出故障时，有效的 `rules` **B** 租约还能继续承载规则允许的流量（`proxy-status` 会标出远端降级以及拿到的国内解析和 `DIRECT` 路由结果），沿用原截止时间直到下一轮完整通过才续租。

平时日常 Firefox 要是报“找不到服务器”，可以在桌面终端先跑 `getent ahosts www.bilibili.com` 看看系统解析，再用 `curl --noproxy '*' -IsS --max-time 6 https://www.bilibili.com/` 测一下普通 HTTPS，并在 Verge 连接记录里确认它命中了 `DIRECT` 还是代理链。想单独测身份浏览器的 Unix 中继、CONNECT 和证书验证，可以跑 `curl --unix-socket /run/clash-strict/proxy.sock --noproxy '' -x http://localhost:80 -IsS --max-time 6 https://search.nixos.org/`（这些命令只在手动执行时才会发请求）。

`clash-off` 会撤销运行授权并结束当前路由代次，**A** 下路由选择显示为 `inactive`，已接受的基线留给下次启动用。开启自动刷新时，下次 `clash-on` 会优先复用符合当前来源修订、受管策略和渲染参数的已接受快照（记录为 `restored`），恢复时照样重跑解析、内容审计和摘要检查，并等新鲜健康证明过了才开 **B**（共享黑名单路径稳定，已校验的名单更新不会让订阅基线失效）。协调器会合并启动和定时更新请求（在 **C** 中先攒着），在 **B** 下于同一次启动、快照、策略和路由选择连续健康 90 秒后触发下载服务；已映射的 GUI 请求走同一道门控，而在 **A** 中则会直接下载备给下次代理启动用。**B** 下下载成功后会按候选摘要排队应用（如果正忙着导入或回滚，应用服务会先等它结束、重新核对选择和摘要再试），等待和下载期间前台核心可以继续启动，`clash-off` 则会清空排队的下载和应用请求。开启运行时路由时，也可以用显式命令在 **C** 中直接应用已有下载，或在短期内核租约下抓一份新候选（见[恢复已登记来源](operations_CN.md#恢复已登记来源)）。还没有已接受基线的首次导入使用已有候选并走正常健康观察期，停掉再重启也不会豁免结构检查。

节点身份、认证和传输变化都会拿来源自己的已接受基线来比对（换来源或重启后也一样）。本机默认自动批准 `node_identity`、`node_authentication` 和 `node_transport` 这三类变化（包括逻辑服务器名和 TLS 名称轮换），重启后的首次 `clash-on` 和 root 定时刷新用同一套策略，日常更新不用人工审阅。

可复用模块里的 `my.proxy.subscriptionAuditAutoApprove` 默认为 `[]`（关闭自动批准）。要开启它，必须同时启用受控来源、`profileRefreshPolicy="auto"`、`subscriptionAudit="enforce"` 且已有接受基线：所有 deny 类别都得落在配置列表里，且不能有未确定的审计结果。审计会跨节点名称比对权限——只是改名而权限没变的节点沿用原判定，没有等价基线的新节点则必须同时满足三类自动批准条件；至于私网/元数据地址、跳过证书验证、明文凭据、未经许可的 `hosts` 覆盖、受保护路由变化、敏感路由双向变化以及格式错误，照样一律阻断导入。自动批准记录有效期五分钟，绑定候选、基线、来源版本、受管策略、验证器策略和审计策略；实际导入时还会再跑一遍解析、批准校验和阻断审计，之后才进入健康接受或回滚。把选项改回 `[]` 就能关掉自动批准，旧记录的校验要求不会松动（见[来源登记](operations_CN.md#来源登记)）。

来源下载和节点地址固定走 `127.0.0.1:1056` 上的过滤真实地址解析器，不受 **B** 的 fake-IP 影响（只有 `clash-fetch` 和 root 能直连这个端口）。worker 仍会拒绝非公网答案并校验 HTTPS 证书；订阅下载、固定节点端点和 **C** 下恢复都要求原生地址，拒绝 `nat64Prefixes` 下的合成地址，不过一个地址族不通时可以改用另一族的有效答案。下载前会先校验整组地址，再在同一截止时间内重试公网地址并保留主机名和证书校验。受控来源还能为 TCP 节点保留最多两个备用公网地址：首次拨号失败或超过 250 ms 就试下一个（同一次拨号最多并发两个），TLS 保留原节点身份和证书策略，固定出口在显式选中包含这些地址的节点身份后也能用上（既有单地址授权、WireGuard relay 和原生 UDP/QUIC 继续用固定端点）。网站 TCP 拨号也按这套节奏试经本地策略验证的地址，只往最终赢出的连接发应用数据。如果没有可用答案，刷新直接失败而不回退系统 DNS。`rules` 下的新查询走基础设施 Unbound 的 DoT 路径（`unboundDotInProxy=false` 时只返回符合条件的缓存或本地答案），运行时 `proxy-only` 则走核心应用验证器（见[解析路径](dns_CN.md#受限-socket-访客-dns)）。

## 可见信息与信任

| 参与方 | 能观察的信息 |
|---|---|
| 浏览器与目标网站 | 应用内容；网站还知道账号、Cookie 和出口地址 |
| 本机 HTTP 代理/核心 | CONNECT 名称、DNS 和路由信息、明文 HTTP 内容 |
| 接入网络/DNS 服务商 | 传输地址与时间特征；解析器知道自己处理的查询 |
| 入口/出口运营方 | 相邻地址与流量时间；出口知道目标信息及明文 HTTP |
| 串通的运营方 | 更多关联信息；本机健康探测不能证明其诚实 |

状态面板会把 `controlled-fetch`、`legacy-unverified` 或未知来源与模式、路由、可用性分开展示，并列出最近刷新结果、审计分类与计数，以及上次自动批准涉及的去重节点数和时间。HTTPS 证书校验只认证下载连接本身，由于没有接入发布者签名验证器，订阅内容的发布者签名是不校验的——自动内容检查只能拦下可判定的风险，没法确认轮换是谁发起的、也不能认证发布者身份（单看域名差异分不出运营方正常轮换还是恶意替换）。TLS 保护认证后的内容，而 ECH 和独立 profile 藏不住账号、目标 IP 或时间特征，运营方也依然可以记录或串通。

桌面控制器能看到核心的实时日志和连接元数据（包括目标名称和代理链）。root helper 把核心日志写进仅 root 可进的 `/var/lib/clash-verge-service/logs`，按单文件 10 MiB、保留 8 个文件的规格轮转，系统 journal 则有独立的保留策略。

## 组件与身份

| 组件 | 默认端点 / 状态 | 身份 |
|---|---|---|
| Mihomo | Mixed `127.0.0.1:7897`、DNS `:1053`、TUN `Mihomo` | `clash-core`，UID 982；核心网络命名空间内的 `NET_ADMIN` 和 `NET_RAW` |
| Clash Verge helper | 按需启动的 `clash-verge.service` | root，保留有限的启动/文件权限；网络能力限定于核心命名空间 |
| GUI 控制口前向器 | `/run/user/<桌面uid>/clash-verge-rev/verge-mihomo.sock` | 桌面 UID、`core` 组，无能力；socket 0600 |
| Gost | HTTP `127.0.0.1:33332`、透明 `127.0.0.1:33333` / `[::1]:33333` | UID 987，无能力 |
| dnsmasq / Unbound / dhcpcd | 本地 DNS / 有线 DHCP | UID 985 / 983 / 986 |

[identity.nix](../../system/programs/clash/service/identity.nix) 和 [runtime.nix](../../system/programs/clash/service/runtime.nix) 定义了核心身份和服务约定。root 启动器通过 `setpriv` 降权，在 permitted、effective、inheritable、ambient 和 bounding 五个集合里都只留那两项网络能力。真实控制口在 `/run/clash-verge-rev/core/mihomo.sock`（监督器直接连它），宿主命名空间里一个无 capability 的桌面 UID `socat` 服务则通过 Unix 字节流把路径转给 GUI，不传递文件描述符。

Helper 只保留身份切换、目录属主和子进程管理所需权限，可执行文件也只认打包的 root 所有启动器；受管日志固定写在 `/var/lib/clash-verge-service/logs` 并按固定策略轮转，客户端传来的 IPC 路径绝不会触发 root `chmod`/`unlink` 或启动恢复，子进程清理由 systemd 兜底，helper 私有状态也和 `/var/lib/clash-core` 里的可写缓存分开。

在特权边界上，helper 强制执行固定路径和文件操作约束，systemd 也会在启动前把 helper 和核心都放进 `lf-clash-core`。核心碰不到宿主接口、路由和防火墙表（这些归固定 root 服务管），同桌面 UID 的程序可以调用允许的 GUI/API。GUI 直接复用未改源码的上游包（Nix 保留上游 `.unwrapped` 输出，只替换启动包装、helper 和核心），所以首页、设置页、托盘菜单和快捷键照常可点、也能写本地偏好，但在碰到底层系统或核心前会被静默丢弃：

- **启动包装 shim**：私有挂载命名空间遮蔽了 Nixpkgs 写进二进制的 `gsettings` 和 KDE helper 绝对路径，`dconf` 和 KDE 5 helper 由 PATH shim 接管（私有命名空间不可用时退回 GSettings 内存后端和 KDE 5 helper 名称）。这样 System Proxy、PAC 和代理守护写入都会返回成功但不会改系统设置，能转发的读取仍走真实 helper。
- **控制器写入策略**：对 `mode`、`tun` 的 `PATCH /configs` 和 `PUT /configs` 重载都返回 `204` 但不应用，其余字段和写操作一律拒绝（返回 `403`），避免被丢弃的重载触发 GUI 的重启兜底。
- **受管解析器**：最终校验前把生成配置的核心模式钉死为 `rule`，并用 root 策略覆盖整段 `tun`，GUI 偏好既改不了实际 TUN，也不会让重启因校验失败而卡住。

GUI 里保存和显示的本地偏好不是权威状态，实际状态以控制器、`proxy-status` 或受管核心为准（就算运行策略缺失或损坏也不会开放控制写权限）。状态读取、受管组内选节点、关连接、测速和看日志都正常可用；注意上游那个“切换模式时关闭连接”的偏好在点击模式控件时依然会清连接，想保住会话可以关掉它（核心模式请求本身照样被丢弃）。helper 根据内核 Unix peer 身份判别生命周期请求：GUI 发的停止请求和重复启动请求都会静默返回成功，受控来源只能由 root 启动，关掉 GUI 不会停核心，退出 **B** 请用 `clash-off`。

显式开启的 IPv4 访客 TPROXY 入口绑定 `0.0.0.0:7896`，普通 mixed/DNS/controller 端点在 `allow-lan=false` 下依然只绑本机。打包监听器单独处理该绑定并选 IPv4 socket，进 **B** 前核对 TCP/UDP 的 UID 和服务 cgroup（访客接口/源地址授权仍在防火墙里），核心后续的 `auto-redirect` 也会保留自有访客 mark 而不顶掉它的透明 socket（见[访客网络](network_CN.md#访客与容器)）。

## 兼容 GUI 配置导入

`proxy-control preflight` 检查桌面配置、必需资源和受管解析约束，不改网络意图。`clash-on` 以预检成功为前提，写入代理意图并在起 helper 前先切进 **C**，清掉刷新 pin 和待健康检查记录，保留完整的快照授权，并打开一次性的 300 秒导入窗口（失败也会消耗该窗口）。

[clash-verge-stage.py](../../system/programs/clash/service/clash-verge-stage.py) 只收桌面用户拥有的有限大小普通文件，拒绝符号链接并检测并发修改，把 YAML 和 geodata 复制进 root 所有的快照（目录 `0750`、文件 `0640`），规范控制口位置，再以无 capabilities 的 `clash-validate` 身份跑 `-t`（授权记录和原始诊断仅 root 可读）。已授权的 `127.0.0.1` 和 `::1` TCP/UDP 目标经无 capability 的 `clash-loopback` broker 访问宿主回环：它核对 peer UID、拒绝保留代理/DNS 端口、在 UDP 截获时保留原目标端口，并在机会性 DNS 下把 53 端口固定映射到宿主网关（严格 DNS 禁用该映射）；通道只传字节和报文，不传宿主 socket 描述符，也不转发其他回环别名。快照摘要覆盖 YAML、geodata 字节（`Country.mmdb`、`GeoIP.dat`、`GeoSite.dat`、`ASN.mmdb` 和 `BundleMRS.7z`，旧 country 库名会规范化）以及可选资源缺失状态，`cache.db` 和 `proxies/` provider 缓存则指向独立可写状态、不进摘要。

### 刷新策略

`my.proxy.profileRefreshPolicy` 默认为 `strict`（GUI YAML 变了就得重新开 `clash-on` 窗口），本机设成了 `auto`，只要代理意图还在，改过的 YAML 过完输入、解析、内容审计和摘要检查就能自动导入。YAML 没变就走普通重启并复用已授权 geodata；GUI 单独更新数据库不会触发导入，受管 geodata 下载也始终关闭（缺失或损坏的数据库不能自己替换），`clash-off` 会撤销授权并阻止直连意图下的 GUI 更新重启核心。

通过校验的候选进入 `verifying`，由 `profile-refresh-verify.timer` 每 10 秒查一次，至少观察 60 秒后才接受对应摘要的新鲜健康结果（`accepted` 建立 settled 基线，观察期内连续导入保留该基线）。失败则恢复基线及授权、通过 helper IPC 换核并记为 `rolled_back`，期间 helper 监听保持可用（只有 helper 传输坏了才重启其单元）。启动导入始终走满 60 秒观察期不提前回滚；后续导入如果有 settled 回退快照，在同一候选和策略的完整健康检查连续至少两次新鲜失败时，可在 `my.proxy.profileRefreshFailureGraceSeconds`（默认 20 秒，范围 20–60 秒）后于下一轮 verifier 提前回滚。首次导入遇到 DNS 或控制器观测失败时候选留在验证期等恢复，明确拒绝且无回退基线时则清掉生效快照指针和授权。服务是否恢复请看 `proxy-status`（helper unit active 不代表核心就绪，核心重启后 GUI 测速历史也可能空着、**C** 也会拦 GUI 测速，就绪看的是新鲜 mixed-port 和 TUN 探测）。

在 `auto` 下开机遇到候选读取或校验失败，会留住完整且仍获授权的旧快照并记为 `fallback`（没有有效回退就以 exit 78 拒绝启动），启动器始终运行 effective 快照。被拒候选的摘要 pin 留一小时（普通重启复用拒绝但不续期，受控来源开了自动批准时会按当前策略重查审计拒绝）；本次启动内，受控来源的健康失败 pin 在 `clash-on` 和 `clash-off` 后保留剩余 TTL 以免再次批准同一坏候选（两个命令都会取消待健康检查，GUI 兼容导入还会清掉自身 pin）。`/run/proxy-mode/profile-refresh.json`（`0644`）原子发布策略、最近结果、截断为 12 位的摘要、固定失败原因、时间戳、审计分类、上次自动批准以及最多 20 条历史，不包含订阅 URL、凭据、节点名、私有路径或原始解析输出。

### 内容审计

`my.proxy.subscriptionAudit` 支持 `off`、`warn`（模块默认）和 `enforce`（本机启用），由无 capabilities 的 `clash-validate` worker 在 `-t` 与快照提交之间离线执行（`off` 只关这层附加检查，受管解析器和摘要检查始终生效）。

审计检查节点 server 地址、`skip-cert-verify`、空组、无效组引用、YAML 键结构、`hosts` 映射和 provider 配置变化，历史比对基准是最近 settled 的 YAML（`subscriptionAuditProtectedRoutes` 声明精确域名的预期路由，首次导入也查；`sensitiveDomains` 对照已接受配置比对后缀路由；可变 provider 沿用下面的独立信任模型）。Sops 加密的 `insecure_sources` 白名单（见 [Sops 白名单](../../system/secrets/whitelist.yaml)）决定是否在解析节点地址前提前删掉启用 `skip-cert-verify` 的节点——解析失败与回环 IP 占位节点合计最多丢掉 `floor(剩余节点数 / 5)` 个（至少 1 个）。白名单每行写一个已登记来源 ID（空行和重复 ID 没关系，但文件缺失/不可读、UTF-8 无效或任一 ID 格式不对会让所有来源的清洗授权失效，把节点留给后续检查）。有意使用非公网节点端点必须声明 `my.proxy.subscriptionAuditServerCidrs`（只放行审计，不授应用访问权，组播端点始终拒绝）。

本机启用的 `my.proxy.remoteOnlyCatchAll` 会让 Merge 脚本顺着最终兜底策略及其代理组引用把 `DIRECT` 等非代理成员清掉，支持任意订阅分组名并拒绝空组和循环引用（共享分组不再提供相应手动选择，显式 `DIRECT` 规则和无关分组保留，动态 provider 路由在 `enforce` 下仍须得到确定结论）。这套约束在 GUI 生成 YAML 时生效，改了 Merge 策略后记得重新应用当前 profile 再请求新导入。

每次导入（含首次）都会核对 root 显式 `hosts` 白名单 `my.proxy.subscriptionAuditAllowedHosts`（默认 `{}`，不允许订阅覆盖任何 hosts）：

```nix
my.proxy.subscriptionAuditAllowedHosts = {
  "service.internal" = "192.168.1.10";
};
```

`my.proxy.sensitiveDomains`（默认 `[]`）对选定后缀沿有序的 `DOMAIN`、`DOMAIN-SUFFIX`、`DOMAIN-KEYWORD` 和 `MATCH` 规则及可静态确定的代理组做定向检查，捕捉可证明的 `DIRECT` 转代理或拒绝变化（同一后缀下混合路由、`GEOSITE`、`GEOIP`、逻辑规则和动态组保守返回 `unknown`；它不等同完整 Mihomo 路由求值，基础设施目标也不会自动加进来）。`my.proxy.subscriptionAuditProtectedRoutes` 为精确域名声明预期 `direct`、`proxy` 或 `reject` 路由（健康和延迟探测域名默认 `proxy`，订阅和更新域名须在此声明）——候选必须独立证明每个受保护路由，规则或组无法确定就过不了合规。

在 `enforce` 下，任何 deny、worker 失败或敏感/受保护路由无法确定都会阻断导入（`blocked by audit` 或 `audit indeterminate`）；`warn` 则只记录不拦。结构性警告覆盖规则或节点数大幅波动、单一 host 集中、新增明文凭据节点和大类地理路由变化，公开结果只留固定分类与计数。

## 受管 profile 与控制 API

[profile.nix](../../system/config/network/proxy/profile.nix) 定义 root 策略，[cvr-merge.nix](../../home/config/clash/cvr-merge.nix) 生成匹配的 GUI Merge。最终核心 parser 会在 GUI/订阅处理完之后强行约束监听、mark、DNS、TUN 路由/排除项和私网目标设置（Mihomo 出站 TCP 会并发竞速可用地址，并在核心命名空间内把出站传输绑到通往宿主的 `lf-host` veth），对缓存 profile 也一样生效，GUI 改不动。Rule providers 固定为空，geodata 自动更新关闭，无条件 `MATCH,DIRECT`/`FINAL,DIRECT` 直接拒绝，其余规则和选组保留原有语义。核心还会向健康消费者上报描述系统不变量的 `managed-policy` 摘要，与绑定 YAML 和资源的快照摘要分开。

重启时，如果生成配置只有系统拥有的 `mode` 和 `tun` 字段不同，就直接复用已接受快照；其他改动仍需 `clash-on`。Root 策略还把共享域名过滤文件、附加名单和例外塞进来：核心在 hosts/缓存/fake-IP 前查 DNS，在 TCP/UDP 拨号前查应用目标，让 HTTP/SOCKS 代解析用上和系统 DNS 完全一样的名单（内部引导查询也过过滤，但传输和目标验证范围独立；split DNS、原生 MagicDNS 和字面 IP 边界见 [DNS](dns_CN.md)）。

| 控制操作 | 受管行为 |
|---|---|
| `GET` / `HEAD` / `OPTIONS` | 可读取；会发起网络流量的桌面 DNS 查询和测速需要 **B** 授权 |
| `PUT` / `DELETE /proxies/<name>` | `rules` 中选择或释放代理选择；运行时 `proxy-only` 返回 `403`，出口由 root 控制 |
| `DELETE /connections` 或 `/connections/<id>` | 关闭连接 |
| `PATCH /configs` | 空补丁、`mode` 或 `tun` 返回 `204` 且不应用；含其它字段返回 `403` |
| `PUT /configs` | 返回 `204` 但不应用（GUI 重载路径不能替换系统渲染配置） |
| 其它写操作 | 默认拒绝 |

禁用规则、provider/geodata/storage 更新、重启和升级 API 全部拒绝。核心模式固定为 `rule`，两个控制器 socket 共用同一套写入策略（直接 Unix socket 靠对端凭据认出 root 恢复探针），核心升级新增写端点时必须显式拍板权限。

桌面节点、分组和 provider 测速都打向 `my.proxy.delayTestUrl` 指定的精确 HTTPS 端点（控制器把 Verge 内置回退 URL 映射到那里，单次拨号最多等 10 秒、批量调度最多 30 秒，只算 HTTP 成功响应，分组和 provider 批量请求跳过内置直连适配器）。桌面主动查 DNS 和测速需要当前 **B** 授权，授权到期或撤回时控制器会取消在途请求（**C** 中仍可看状态和既有测速，root 经私有 Unix 入口发的恢复查询只用配置的探针域名）。受管核心用 Verge 默认的统一延迟（能复用连接跑第二次请求时就报第二次耗时，恢复探针拿到首个成功响应就结束，新建应用连接的首包仍受冷 DNS、拨号和首次 TLS 握手影响）。本机测速用 `https://www.gstatic.com/generate_204`，若同一轮监督器探测证明主站挂了而 `my.proxy.delayTestFallbackUrl` 经预期代理能通，桌面测速可在该证明的 60 秒窗口内改用备用站（GUI 会把被拒绝的测速也显示成 0，遇到 0 去 `proxy-status` 看桌面测速授权，等租约恢复再重测）。桌面测速共用 64 个拨号槽位（每秒 64 次、突发 128 次，最多四个分组/provider 并行、每批最多 8 个拨号），自动测速另用 10 个槽位（每秒 10 次），root 直连 Unix 控制器的健康探针有独立配额，固定出口测速期间保留已选节点。

## 兼容模式的订阅信任

`my.proxy.subscriptionTrust="source"` 把 proxy provider 当作可信的可更新来源，`"fixed"` 则禁用 provider 让内联节点全部进授权 YAML 快照。预检会统计内联节点数、provider 数、来源主机和证书校验选项而不暴露订阅 URL 和凭据（启用受控来源时跑 `sudo proxy-control preflight` 读取 root 所有的来源状态）。

受控来源之外的兼容导入由全局 merge 脚本过滤掉启用 `skip-cert-verify` 的内联节点及分组引用（没了 provider 成员的空组直接终止生成），核心在跑完 per-profile 扩展和 provider override 后也会拒绝绕过证书校验——保持 `my.proxy.insecureProxyExceptions` 为空、给节点配好可信证书或 CA 即可（可变 provider 配 `remoteTargetGrants` 还要开 `allowDynamicRemoteGrants=true`）。预检核对配置、资源和受管解析器，provider 内容则由核心在加载和刷新时校验，快照里的 `proxies/` 链接指向可写的 `/var/lib/clash-core/proxies/`（HTTP provider 先读缓存、被拒再拉远端，刷新被拒就留住上次节点和缓存，初始化失败保留 fallback payload，正间隔自动重试）。快照快速检查会比对文件身份、权限、大小和 `ctime`/`mtime`，授权变化、元数据变化、每 60 秒间隔、启动/导入以及看不到启动身份的沙箱都会触发完整 SHA-256 校验，快照文件和验证记录只有 root 能写。

## 路由策略

本机启用了 `my.proxy.runtimeRouting=true`，不用重建系统就能在两个 **B** 路由子模式间切换：

```sh
clash-rules
clash-proxy-only
```

两条命令都会请求代理访问（在 **A** 或 **C** 里也能直接跑），只在终端打印成功模式或失败原因，不弹 GUI、不发桌面通知、也不打印概览；**B** 和 **C** 中可用 `proxy-status` 看路由选择、事务 epoch、观测核心模式和协议覆盖（底层入口是 `sudo proxy-control routing rules` 和 `sudo proxy-control routing proxy-only`）。两者渲染的核心设置都是 `mode: rule`——意思是让 Mihomo 去跑规则表，并不是选 `rules` 还是 `proxy-only`；GUI 上的 Rule/Global/Direct 只是核心模式请求，会被直接丢弃，既切不走 `rule` 也选不了路由子模式。

`rules` 保留订阅分流（国内 `DIRECT`、profile `REJECT` 和受管探针规则），而运行时 `proxy-only` 会把所有受支持的应用流量——原生 TCP/UDP、显式代理、严格 Firefox 和两个 DNS 验证器传输——全部绑死到已接受来源远程兜底组里的唯一一个固定叶节点（自动测速、fallback 和 GUI 选组都换不掉它，最终适配器仍拒 `DIRECT`/`COMPATIBLE`，原规则、分组和所有固定节点另行保存以便切回 `rules` 时恢复）。

用 `clash-egress` 列出候选，再用列表里的完整 ID 执行 `clash-egress APPROVED_NODE_ID`（底层是 `sudo proxy-control egress list` 和 `sudo proxy-control egress APPROVED_NODE_ID`）。本次启动若还没选过出口，首次进严格模式默认挑兜底图里按顺序可达的第一个远程叶子；换出口会经过 **C**、关掉旧 TCP 流和 UDP association 并要求新的完整健康与授权证明（重选已就绪的同一 ID 幂等，节点挂掉则停在 **C** 而不自动换节点）。出口 ID 绑定完整的固定节点定义（连同 `egress_requested` 和 `egress_epoch`），在本次启动的 off/on 和切回 `rules` 后都会保留，重启清除。来源改了所选节点的地址、凭据或传输后，得先在 `rules` 里接受更新再选新 ID；同样地，如果系统重建换掉了当前策略或健康产物导致旧代次选择滞留，跑一下 `clash-rules` 开启新事务，再用 `clash-egress` 重选即可。

`proxy-status --json` 在严格选择下报 `capabilities.egress="fixed-leaf"`，`capabilities.udp_declared` 显示所选适配器的 UDP 声明（不支持为 `unsupported`，否则为 `not-probed`；IPv6 仍看节点，不支持的请求不会转到别的叶子或 `DIRECT`）。需要主动测 UDP 的话，可以把 `my.proxy.udpHealth.address` 和 `my.proxy.udpHealth.port` 设成已批准的公网字面 UDP 回显端点：透明 `proxy-only` 就绪后每两分钟以普通桌面进程身份发一个随机数据报，在 `capabilities.udp` 里报 `verified`、`no-reply` 或 `error`（150 秒过期，只供诊断，不影响租约、自动刷新和选点）。`capabilities.remote_nat="not-measured"` 提醒你同一节点对 TCP/UDP 或不同目标仍可能用不同的 NAT 地址。

每次切换都会先装 **C** 并撤销 **B** 授权，停掉旧核心和 relay 流，装上选定的不可变策略再重建核心命名空间，等 parser、内容审计、监听、路由、DNS/TUN 和完整健康检查全过了才放回 **B**（失败就停在所求模式和 **C**，绝不自动退回 **A** 或 `rules`；重复选已就绪的模式只要快照还匹配就保留现核心）。`clash-off` 切 **A** 时会留着本次启动的路由选择给下次 `clash-on` 用，重启则回到 `rules`（开机选 **A** 还是代理归 `bootMode` 管）。

`proxy-only` 要求当前来源修订已有本地接受快照且节点地址已固定为字面 IP，新修订得先在 `rules` 跑通健康接受再离线进严格模式。切换和严格启动都不需要直连 DNS 或拉订阅（应用和 worker DNS 都经远程加密传输拿本地验证结果，见 [DNS](dns_CN.md#b受管-mihomo-dns)），同时禁用 LAN、组播、时间同步、Tailscale 和入站服务回复，只留限定的 DHCP、NDP 和必要传输错误（见[严格出口](egress_CN.md#proxy-only-出口)）。可复用模块里 `runtimeRouting` 默认为 `false`（启用它要求受控来源、隔离 TUN、本地 DNSSEC、严格加密 DNS 和内核授权租约，且使用时保持旧构建期 `proxyOnly=false`）。

### 包含真实 ping 的 WireGuard 出口

配好 `my.proxy.ipTunnel` 后，运行时 `proxy-only` 会切到受控的 WireGuard 出口：系统 DNS 返回本地验证过的真实地址，`ping 域名` 和 `ping IP` 都能发原生 ICMP echo，核心和严格 Firefox 则共用同一 WireGuard 对端上的固定 SOCKS5 relay（由对端在 IP 层转 ICMP 并发起代理 TCP/UDP）。切换前先把两端配好：

1. 在远端为客户端配好 IPv4 和 IPv6 两条隧道主机路由、开启双栈转发并放行相关 ICMP 错误（包括 IPv4 需要分片和 IPv6 Packet Too Big），让远端转发的 echo 和 SOCKS5 relay 在每个地址族用同一个公网源地址。
2. SOCKS5 relay 绑在隧道内可达的公网字面地址上（可以和 WireGuard 外层端点共用地址），远端防火墙只放行 WireGuard 接口和客户端地址访问它，SOCKS5 UDP association 须返回配置里的 relay 地址和固定端口；把该节点以 `udp: true` 登记进来源的远程兜底组（仅作 WireGuard 出口固定叶子用，兜底组里还得留另一个可达节点用来做首次来源接受和 `rules` 模式）。
3. 在客户端声明外层端点、对端公钥、客户端地址、relay 以及远端之后的两个受控公网 echo 目标，客户端私钥放在仅 root 可读的运行时文件（比如 sops 密钥）里：

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

应用配置后跑 `clash-proxy-only`（`clash-egress` 会列出该 relay 上兼容的叶子）。内核路由、双栈 echo 以及原有应用和 DNS 检查全过后，`proxy-status --json` 会报 `capabilities.icmp="verified-dual-stack"`（隧道挂了就停在 **C**，`clash-rules` 和 `clash-off` 会删掉 WireGuard 接口和路由）。状态里会按地址族显示当前的 WireGuard 健康阶段（双栈都挂时还会报近期是否有握手），每个地址族在同一预算内可重试一次，双栈全过才发或续租；如果旧策略还处于选中状态，先跑 `clash-rules` 恢复事务再选新出口。

## 域名目标

受管核心在 `Rule` 和统一代理模式下都关闭嗅探：域名路由看的是显式目标和 fake-IP 映射（ECH 掩护 SNI 不能替代拨号目标），字面 IP 走 IP 规则，parser 对缓存的 GUI 配置也照此规范（见[浏览器 ECH 发现](browser_CN.md#ech-发现与代理兼容)）。

[target_guard.go](../../system/programs/clash/mihomo/guard/target_guard.go) 在 TCP/UDP 出站前校验应用目标。公网域名必须在核心本地解析出非空、全部为公网的答案（解析报错可重试一次）。TCP 会在选中的叶节点交错试最多 8 个已验证 IPv4/IPv6 候选（单次上限 2 秒、总截止 6 秒），每个候选都得过该叶子的出站授权——节点只收固定 IP、自己不解析域名，UDP 则为整个 association 固定单一目标。`privateTargetDomains` 里的内部后缀和已认证 MagicDNS 后缀允许拿私网答案，但照样得走本地解析、固定地址和出口授权（后缀本身既不建解析路由也不绕过共享过滤）。拨号报错就试下一候选，直到连上交付连接（乐观确认或单向代理协议以隧道建立为成功点，回退到此结束）。

### 本地与远程授权

授权在实际选中的叶节点执行（覆盖嵌套、fallback 和负载均衡组）。就算把远程适配器起名叫 `DIRECT`，也不会拿到直连权限；受管解析器把核心钉在 `rule`（GUI 开不了 Global 或 Direct），`proxyOnly` 也会独立拒绝应用 `DIRECT`。

| 到达核心的目标 | 本地 DIRECT | 远程代理 |
|---|---|---|
| 公网字面 IP 或已验证公网域名 | 允许（但 `proxyOnly` 可拒绝 `DIRECT`） | 允许 |
| 私网/CGNAT/loopback/ULA/链路本地字面地址，或已批准内部后缀的私网答案 | 必须匹配 `localTargetCidrs` 且来自宿主入口 | 必须匹配 `remoteTargetGrants` |
| 清单内元数据端点、未指定、组播、选定保留/过渡地址或原始 fake-IP | 拒绝 | 拒绝（授权条目也不能放行） |
| 已知 NAT64 翻译地址 | 内嵌 IPv4 必须通过公网地址检查 | 同左（不能授权内嵌私网目标） |

`localTargetCidrs` 默认包含共享本地/私网范围，对应宿主入口（`HTTP`、`HTTPS`、`SOCKS4`、`SOCKS5`、`REDIR` 和 `TUN`；访客 `TPROXY` 没有默认本地权限）。设为 `[]` 可以去掉这项核心权限，但不影响原生宿主防火墙、访客转发或 **A** 的 Gost 直连透传。

远程授权默认为空。确实要访问节点所在网络里的服务时，写明精确叶节点名、规范 CIDR、核心入口、传输协议和端口：

```nix
my.proxy.remoteTargetGrants = [{
  proxy = "Office node";
  cidr = "10.20.30.40/32";
  inlets = [ "SOCKS5" ];
  network = "tcp";
  ports = [ 443 ];
}];
```

这只授权该节点按自身网络去连这个 IP，不授权远端 DNS 解析，也不扩到其他节点、入口、协议或端口（要用域名的话还得声明内部后缀，并通过已授权 hosts 或核心解析器提供答案）。授权认的是配置里的叶节点名而不认证远端服务器身份，所以要审阅好来源、别把授权过的名字复用到别的节点上。注意入口以核心实际收到的流量为准（Gost 转过来的 CONNECT——哪怕来自透明入口——都是 `HTTPS`，它不认证应用也不保留原 UID）。复用 UDP association 时每个新目标都会按实际叶子检查并固定，缓存映射每包重查入口、目标端口和出口授权（换端口混不过窄授权），已有 TCP 连接不追溯检查，改完策略记得重建并重启核心。

### 地址类别与模式

IPv4-mapped 地址在授权前会规范成 IPv4，带 zone 的 IPv6 字面地址直接拒绝，不可授权的元数据清单包括 `169.254.169.254`、`169.254.170.2`、`100.100.100.200` 和 `fd00:ec2::254`。系统始终识别 `64:ff9b::/96`，`nat64Prefixes` 可声明额外的 [RFC 6052](https://www.rfc-editor.org/rfc/rfc6052.html) 翻译前缀（长度限 32、40、48、56、64、96，重叠取更精确前缀）——内嵌公网 IPv4 可用，私网、元数据和合成目标直接拒绝，本地用途 `64:ff9b:1::/48` 除非显式声明否则也拒绝。fake-IP 池只是路由身份，绝不能成为实际出站目标：必须先靠有效映射还原出域名，再走过滤、解析和授权（缺失映射或真实 DNS 答案里混进合成目标一律拒绝，自定义池也受此约束）。

这些规则管的是到达核心的应用流量（通常在 **B** 下）。**A** 下的 Gost 和原生 socket 走 **A** 的防火墙策略并保留字面 HTTP 门户访问；**C** 则在核心前面就把普通应用拦下了，本地和远程授权都放行不了。内部引导、代理端点传输和已认证 MagicDNS 各有独立的基础设施权限，按规则路由的 DNS 不继承它们，原生 LAN 和访客转发也各有各的策略；核心只管自己的私有网络命名空间，宿主机归 root 服务管。

## Gost 生命周期

[relay.nix](../../system/programs/gost/relay.nix) 跟随模式的原子状态，绑定前回收旧进程、不用 `SO_REUSEPORT`、要求每端点唯一监听并限制文件描述符、内存和任务数。**B** 下它信任监督器健康结论并用被动 socket 观测（加自连接检测兜底）核对 mixed 监听身份，而不去主动连透明入口。relay 还兼容带有效端口的裸数字 IPv6 CONNECT，同时保留 Go HTTP framing 和缓冲中的隧道数据（客户端尽量还是用标准 `[IPv6]:port`）。Gost 仅通过 store 绝对路径调用，不放进系统 `PATH` 或 `initrd`，源码和打包定制见[维护手册](maintenance_CN.md)。
