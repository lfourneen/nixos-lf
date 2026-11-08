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

[DNS](dns_CN.md) 是并行链路：resolved → dnsmasq → Mihomo 或 Unbound。[Firefox](browser_CN.md) 使用相同的 Gost HTTP 入口。

## 模式与执行

| 模式 | 进入条件 | 应用出站 | 默认 DNS 上游 |
|---|---|---|---|
| A / `direct` | 默认直连策略下冷启动，或显式 `clash-off` | 直连 | 本地验证的 Unbound DoT，必要时有界明文恢复 |
| B / `proxy` | 代理意图、已授权核心、所需 TUN 选路和健康证明均成立 | 代理、明确例外或丢弃 | Mihomo fake-IP |
| C / `blocked` | 已请求代理但未就绪、崩溃或不健康 | 除恢复与链路基础设施外阻断 | Unbound；外部 DoT 被切断，缓存答案可能仍可用 |

`proxy-mode` 遵循 root 写入的单次开机意图。`clash-on` 后关闭 GUI、停止核心或崩溃均保持 C；需要 A 时使用 `clash-off`。只启动 GUI 无法授权 A → B。可选 `my.proxy.bootMode="proxy"` 在物理网络启动前安装 C，等待 B；默认是 `"direct"`。

`unenforced` 表示预期内核策略无法确认或修复，是错误状态。它不表示已经得到阻断保护；使用共用就绪检查的应用会拒绝该状态。

健康判断检查核心 cgroup、TCP/UDP 监听身份、受管 API、TUN 设备与路由，以及 root/桌面 UID 未打标 IPv6 的实际选路。HTTP 探针须经当前选中代理叶子完成，且日志匹配源端口；另向 fake-IP 发真实 HTTP 请求证明透明 TCP 路径。默认共享截止为 12 秒。明确的配置、路由或 TUN 失败立即阻断；观测不可用需连续两轮，间隔至少两秒。它证明抽样的 TCP 路径，不能概括全部 UDP、IPv6 或应用目的地。

## 防火墙生命周期

[network.nix](../../system/config/network.nix) 定义静态策略及 B/C 片段。[proxy-mode.nix](../../system/programs/systemd/proxy-mode.nix) 在共享锁下以一次 `nft -f` 事务安装静态规则和安全模式，只替换本仓库拥有的表，保留 Mihomo 等服务的表。

| Hook / priority | 用途 |
|---|---|
| Output `-201` | NAT 前拒绝显式使用 Gost 透明入口 |
| Prerouting `-201` | 重定向前限制访客公网目的地 |
| Output `-199` | 将 dnsmasq 上游 DNS 限定到已安装的目的地租约 |
| Output `-198` | 保留内部 DNS 端口并检查应答身份 |
| Output `-197` | C guard，位于 conntrack（`-200`）之后、auto-redirect（`-150`）之前 |
| Output `-196` | 限制独立使用 853 端口的 DoT/DoQ |
| Output `-195` | B 可选私网/多播授权与 Unbound DoT 关闭，位于 NAT/TUN 前 |
| Output `-194` | A/B 可选 Chrony 原始目标 CIDR 限制 |
| Input `-1` | C 在 established 放行之前丢弃已解密 tailnet 数据 |

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

`my.proxy.guestInterfaces` 默认包含 `virbr0`、`waydroid0`；独立 AP 仅在热点策略启用后加入，Wi-Fi 客户端上行不作为访客入口。B/C 在 NAT 前检查公网目的地，并在 established 放行前约束双向转发。

B/C 默认拒绝访客公网转发。`transparentGuestInterfaces` 显式选择健康 B 中的 IPv4 TPROXY 子集，默认空；访客 IPv6 公网仍拒绝。A 直接共享上行。热点选项仅安装防火墙角色，独立 AP 接口、hostapd 和 DHCP 仍需另行配置。

访客网关 DHCP/DNS 与宿主 loopback 解析器分开；默认宿主访问仅限该基础设施。Libvirt 桌面服务需要 `guestHostServices` 和明确的 TCP/UDP 清单，撤销后也切断已有访客主动连接。TUN 入站仅允许已跟踪的 established/related 流量。

访客私网转发默认允许。`my.hardening.guestPrivateEgress=false` 配合 `guestPrivateExceptions` 按接口/CIDR/协议/端口限制 libvirt/Waydroid，不改变单独启用的热点策略。LAN/CGNAT/ULA 可达是兼容选择，不代表可信。Passt 或 rootless 容器若转换为宿主 socket，则走宿主路径，需要单独验证。
