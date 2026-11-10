# 网络架构

[English](../network.md) | 简体中文 | [文档目录](README_CN.md)

## 流量路径

宿主通过 nftables 选择直连、受管代理或阻断。代理健康时，三类应用路径汇入 Mihomo：

```text
读取代理设置的应用          不读取代理设置的公网 TCP         TUN / fake-IP
        |                            |                           |
        v                            v                           v
 gost HTTP :33332          nft REDIRECT -> gost :33333      TUN "Mihomo"
        |                            |                           |
        +---------------> Mihomo mixed :7897 <------------------+
                                   |
                               订阅规则匹配
                              /            \
                           DIRECT       代理组 / 节点
                              \            /
                                   上行
```

这些监听均在本机。大小写环境变量、GSettings 和 Flatpak 覆盖将应用指向 `127.0.0.1:33332`。Gost 在 B 中把两种入口转给 `127.0.0.1:7897`，A 中直通，C 或未知状态下关闭监听。客户端显式连接透明入口 `33333` 会在 NAT 前被拒绝；该入口只供重定向流量使用。

Mihomo 为出站 socket 设置 `6666`（`0x1a0a`）mark；核心例外同时要求 socket 与 packet mark，并核对 socket UID 为 root 或 `clash-core`。默认 `my.proxy.proxyOnly=false` 时 Rule 可选 DIRECT；可选的[统一代理策略](proxy_CN.md#路由策略)在核心内拒绝应用 DIRECT。核心以外的私网和基础设施路径仍由各自规则控制。

[DNS](dns_CN.md) 是并行链路：resolved → dnsmasq → Mihomo 或 Unbound。[Firefox](browser_CN.md) 默认使用相同的 Gost HTTP 入口；可选透明路径使用原生 DNS 和宿主 socket，受本套 TUN/防火墙约束，支持 HTTPS-RR/ECH 发现。父进程及其后代运行于[外层文件系统与桌面接口沙箱](browser_CN.md#外层沙箱)，保留桌面 UID 和本套宿主网络策略。

核心内的字面或解析后私网目标按[本地 DIRECT 与远程叶节点](proxy_CN.md#域名目标)分别授权；订阅规则选中节点不等于获准访问该节点的内部网络。

## 模式与执行

| 模式 | 进入条件 | 应用出站 | 默认 DNS 上游 |
|---|---|---|---|
| A / `direct` | 默认直连策略下冷启动，或显式 `clash-off` | 直连 | 本地验证的 Unbound DoT，必要时有界明文恢复 |
| B / `proxy` | 代理意图、已授权核心、所需 TUN 选路和健康证明均成立 | 代理、明确例外或丢弃 | Mihomo fake-IP |
| C / `blocked` | 已请求代理但未就绪、崩溃或不健康 | 除恢复与链路基础设施外阻断 | Unbound；外部 DoT 被切断，缓存答案可能仍可用 |

`proxy-mode` 遵循 root 写入的单次开机意图。`clash-on` 后关闭 GUI、停止核心或崩溃均保持 C；需要 A 时使用 `clash-off`。只启动 GUI 无法授权 A → B。可选 `my.proxy.bootMode="proxy"` 在物理网络启动前安装 C，等待 B；默认是 `"direct"`。

DNS 传输独立设置：`my.proxy.dnsEncryption="strict"` 禁用 A 的公网明文恢复及运行时 override，保留已批准 split DNS 和 MagicDNS；默认 `"opportunistic"` 保留门户恢复。两种策略都让 dnsmasq 和核心共享域名过滤数据，覆盖代理目标检查，见 [DNS 各路径的责任](dns_CN.md#各路径的责任)。

`unenforced` 表示预期内核策略无法确认或修复，是错误状态。它不表示已经得到阻断保护；使用共用就绪检查的应用会拒绝该状态。

健康判断检查核心 cgroup、TCP/UDP 监听身份、受管 API、TUN 设备与路由，以及 root/桌面 UID 未打标 IPv6 的实际选路。HTTP 探针须经当前选中代理叶子完成，且日志匹配源端口；另向 fake-IP 发真实 HTTP 请求证明透明 TCP 路径。默认共享截止为 12 秒。明确的配置、路由或 TUN 失败立即阻断；观测不可用需连续两轮，间隔至少两秒。它证明抽样的 TCP 路径，不能概括全部 UDP、IPv6 或应用目的地。

## 防火墙生命周期

[network.nix](../../system/config/network.nix) 定义静态策略及 B/C 片段。[proxy-mode.nix](../../system/programs/systemd/proxy-mode.nix) 在共享锁下以一次 `nft -f` 事务安装静态规则和安全模式，只替换本仓库拥有的表，保留 Mihomo 等服务的表。

| Hook / priority | 用途 |
|---|---|
| Output `-202` | 受限 socket 访客的原始目标/UID 守卫 |
| Output `-201` | NAT 前拒绝显式使用 Gost 透明入口 |
| Prerouting `-201` | 重定向前限制访客公网目的地 |
| Prerouting `-199` | 访客源地址、私网目标和网关服务检查 |
| Output `-199` | 跟踪受限 socket 访客账户；将 dnsmasq 上游 DNS 限定到目的地租约；严格模式阻断核心外部 53，仅保留 MagicDNS |
| Output `-198` | 保留内部 DNS 端口、检查应答身份并复核访客宿主服务回包 |
| Output `-197` | C guard，位于 conntrack（`-200`）之后、auto-redirect（`-150`）之前 |
| Output `-196` | 限制独立使用 853 端口的 DoT/DoQ |
| Output `-195` | B 可选私网/多播授权与 Unbound DoT 关闭，位于 NAT/TUN 前 |
| Output `-194` | A/B 可选 Chrony 原始目标 CIDR 限制 |
| Output NAT `-190`、filter `-90` | 将受限 socket 访客 DNS 强制送至 1056，并拒绝旧解析器关联 |
| Input `-2`、forward `-1` | 按当前授权复核受限 socket / 转发访客回包 |
| Input `-1` | C 在 established 放行前切断已解密 tailnet 数据及已跟踪 socket 访客回包 |

NAT 优先级排列内核共享 destination-NAT hook 中的规则查找顺序；socket 访客 DNS 的 `-90` filter 在该 hook 之后检查实际改写后的目标。

B 重定向宿主公网 TCP，fake-IP 保留核心路径，其余公网出站在明确例外后丢弃。例外包括核心 mark、按 UID 限定的 Unbound DoT 与 Chrony NTS/NTP、窄 DHCP、组播和默认私网访问。Tailscale UDP 例外要求 UID 0、物理上行口，且 socket 的 `SO_MARK` 与 packet mark 都等于上游 Linux bypass mark（`0x80000`）。保留动态 peer/STUN 端口，不再整体放行未打标 root UDP。这是路由约定，不是服务身份认证或针对 root 的隔离；root 仍能设置 socket mark 或修改防火墙。当前 LKL 内核支持 `socket mark`，不支持 `socket cgroupv2`。

[tailscale-transport.nix](../../system/config/tailscale-transport.nix) 将该 mark 作为上游常量，而非可自行分配的配置。正常系统构建会核对所选 Tailscale 源码的常量及已审查的 magicsock/STUN 打标路径。切换网络不会改变常量；升级时须通过该检查与 `proxy-tailscale-vm`，后者用所选真实 CLI 和隔离的双栈 STUN peer 验证。退回 `SO_BINDTODEVICE` 不会静默重新放行未打标 root UDP。

[直连例外清单](egress_CN.md)逐项记录主体、目标、端口和模式。可选受限 B 按接口/UID/CIDR/端口授权，在 TUN/NAT 前切断撤权后的已有宿主主动连接。Unbound 即使在 A 也只能使用声明的 DoT 目标，B 的此项例外可关闭；Chrony 可在 A/B 固定目标 CIDR。默认保留 LAN 与 NTS 地址变化兼容性。

C 的早期 output guard 在核心重定向前阻断应用，保留 loopback IPC、核心 mark、root 到代理/DNS 端口和 fake-IP 53/80/443 的恢复访问、窄 DHCP 与 hop-limit 255 的 NDP。非 root 到核心/Gost 应用端口的 TCP/UDP 被阻断。自测应答例外限定为预期 loopback 源，或 root/core 的 conntrack DNAT reply 且原目的地为 fake-IP HTTP。访客 DHCP/DNS 应答按网桥、网关源地址和端口识别，不依赖守护进程 UID。Unbound 和普通 tailnet 流量不能外出，包括已有远控连接；B 的授权不提供 C 远控例外。另有入站守卫在 established 放行前丢弃已解密 tailnet 数据，包括切换时仍在到达的数据包。

[network-bootstrap.nix](../../system/programs/systemd/network-bootstrap.nix) 在 NetworkManager/dhcpcd 前安装入站和转发基线；首次完整装载失败仍保留它。静态漂移触发完整修复：直连意图回到 A，代理意图先回 C，再检查健康。失败报告 `unenforced`。

停止 `nftables.service` 保留规则；restart/reload 用于协调恢复。避免 `nft flush ruleset`，它会同时破坏核心自己的表。验证和恢复方式见[操作与诊断](operations_CN.md)。

## 上行与本地服务

有线 `ens1` 由 dhcpcd 管理，NetworkManager 按名称和 MAC 排除它。Wi-Fi `wlo1` 由 NetworkManager 管理，以 PCI 路径稳定匹配。扫描使用随机 MAC，连接默认 `stable-ssid`；持久化的 NetworkManager 状态和 machine-id 使同一 SSID 的身份跨根回滚保持稳定。连接 profile 可覆盖[默认 MAC 策略](operations_CN.md#配置入口)。

Tailscale 不自动接受 DNS、子网路由或出口节点；只有已认证 tailnet 的 MagicDNS 后缀获得条件 DNS 路由。宿主入站默认仅 Sunshine 串流 TCP 47984/47989/48010 和 UDP 47998/47999/48000/48002/48010。Sunshine 随图形会话启动，管理界面仅本机可用，关闭 UPnP，见[远程桌面操作](operations_CN.md#远程桌面)。Avahi 仅在 loopback、tailnet 及显式启用的 AP 上发布；外网 Wi-Fi 客户端不发布。本配置没有导入 sshd 模块，防火墙允许端口也不会自行创建 SSH 监听。

[network-time.nix](../../system/config/network-time.nix) 为 Chrony 配置认证 NTS、UDP 123 和 NTS-KE TCP 4460。RTC/drift 恢复提供离线时间下限；证书时间校验保留，不配置明文时间回退。[network-hardening.nix](../../system/config/network-hardening.nix) 提供共享 sysctl、redirect/source-route 和模块限制。Nix daemon 的 trusted-users 仅含 root。

## 访客与容器

[guest-policy.nix](../../system/config/guest-policy.nix) 区分经宿主转发的访客网络与创建宿主 socket 的后端。私网地址本身不构成信任依据。

| 类别 | 身份依据 | 私网目标 | 公网目标 |
|---|---|---|---|
| 受限桥接 / 热点（默认） | 精确入口接口与声明的源 CIDR | 明确的接口/CIDR/TCP 或 UDP 端口授权 | A 允许 IPv4；B 须显式选择 IPv4 TPROXY；C 拒绝 |
| 可信开发桥 | 相同的接口/源地址检查，显式 `trust="trusted"` | 允许私网路由，仍拒绝不安全目标 | 与受限桥相同 |
| 受限宿主 socket 后端 | 独立固定 UID 及其全部委派 subordinate UID | 明确的账户/原始出口接口/CIDR/端口授权 | 宿主 A/B/C 策略；C 也切断已跟踪回包 |
| 桌面账户的 passt / rootless Docker | 共用桌面 UID | 宿主策略 | 宿主策略 |

`my.proxy.guestInterfaces` 默认包含 `virbr0`、`waydroid0`；独立 AP 在热点启用后加入。`my.hardening.guestNetworks.<接口>` 声明信任级别、规范且互不重叠的 `sourceCidrs`、网关地址及宿主服务端口。内建角色默认使用 machine 中的 IPv4 子网/网关。IPv6 需要显式 ULA 源子网；所有模式均拒绝访客 IPv6 公网。Wi-Fi 客户端上行不能作为访客角色。创建 AP、DHCP 服务和网桥与安装防火墙策略仍是独立步骤。

所有角色（包括热点）的私网转发默认受限。`guestPrivateExceptions` 按精确入口接口、私网目标 CIDR、TCP/UDP 端口授权。开发网络可显式设 `trust="trusted"` 放行私网路由，但不会开放任意宿主服务或公网 B/C 转发。兼容选项 `guestPrivateEgress=true` 仅信任未明确设置 `trust` 的 libvirt/Waydroid 角色，逐接口选择优先。

网关 DHCP/DNS 与宿主 loopback 解析器分开。`hostTcpPorts` / `hostUdpPorts` 仅在该角色声明的网关地址开放服务；旧 libvirt `guestHostServices` 与端口清单汇入同一策略。源检查先于重定向，宿主访问检查先于 established 入站放行，当前授权同时约束既有转发连接的双向流量及宿主服务回包。因此撤销也会切断服务端主动推送。不同信任级别需要独立网桥：此策略识别网络，不隔离同桥二层流量，也不认证具体访客。

`transparentGuestInterfaces` 默认空，仅为健康 B 显式选择 IPv4 TPROXY 入口。有效 fake-IP 映射需要该入口；其它合成地址、选定元数据端点及已知转换/过渡网段仍拒绝。C 在保留核心恢复路径的同时切断已代理访客的回包。桥接私网授权与公网模式守卫独立。

Passt 与 rootless 网络将访客报文转换为宿主 socket，桥接规则无法据此识别容器。对不可信后端，应以 `my.hardening.socketGuestUsers` 声明独立固定普通账户；正常构建拒绝桌面/root/基础设施身份、共用 UID 及重叠的委派范围。`socketGuestPrivateExceptions` 按账户、原始出口接口、私网 CIDR 和目标端口授权。loopback/代理入口、保留代理/DNS 目标端口、fake-IP 和已知转换目标不可授权。TCP/UDP 53 强制进入[过滤后的真实地址解析器](dns_CN.md#受限-socket-访客-dns)，避免 B 的合成地址遮蔽 UID 守卫需要检查的私网最终目标。这些账户的 rootless Docker 启动器会清除继承的显式代理环境变量。

先安装策略，再以声明账户运行后端。新增/更改受限账户或 UID 映射前，应停止旧后端；回包跟踪无法追溯识别旧的未标记 socket。在身份保持声明时撤销私网授权，会切断已跟踪连接的双向流量。桌面账户的 rootless Docker 仍属于可信开发环境，因为它与桌面应用共用 UID。UID 策略约束网络出站；文件权限、共享 Unix socket、入站端口发布及同桥隔离需要另行控制。授权匹配宿主 socket/路由的原始目标，不检查应用隧道内部的目标。root 与拥有宿主 `NET_ADMIN` 的核心仍受信任。见[配置示例](operations_CN.md#配置入口)。
