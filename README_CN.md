<p align="center">
  <a href="https://nixos.org">
    <img src="assets/nixos-logomark-default-gradient-minimal.svg" width="140" alt="NixOS logo">
  </a>
</p>

<h1 align="center">nixos-lf</h1>

<p align="center">个人 NixOS 系统配置</p>

[English](README.md) | 简体中文

这是一套通过 Nix flakes 和 Home Manager 管理的个人 NixOS 系统配置，涵盖 Niri 桌面、应用软件、开发环境和加密存储。
工作站的网络配置集成了 nftables 防火墙、Mihomo（Clash）系统级透明代理，以及支持 DNSSEC 验证的加密 DNS。
配置面向 `x86_64-linux`，基于 NixOS 26.05，部分软件来自 Unstable，系统构建入口为 `nixosConfigurations.lfour`。

## 网络安全与架构

主机由 nftables 管理 IPv4 和 IPv6 入站、出站及访客转发，systemd 服务以原子事务切换策略。请求代理时先进入 C，监听端口、路由、DNS 和 TUN 通过健康检查后才进入 B。内核中的 B 授权租约最长为 60 秒，健康检查持续通过才能续期，授权到期后已建立的应用连接也会被阻断。见[模式与执行](docs/CN/network_CN.md#模式与执行)。

| 模式 | 普通应用流量 | 公网 DNS |
|---|---|---|
| **A** · 直连 | 原生 socket 通过有线或 Wi-Fi 出站 | Unbound 经 DoT 查询并在本地验证 DNSSEC |
| **B** · 代理 | TCP/UDP 经 TUN 进入 Mihomo。TCP 可由 nftables 重定向，经 Gost 进入核心 | Mihomo 在本地验证 DNSSEC，使用 DoH 查询上游。通常返回 fake-IP，使用 WireGuard 出口时返回验证后的真实地址 |
| **C** · 阻断 | 阻断普通外部流量，仅放行必要的链路维护和代理恢复流量 | 保留本地解析和符合条件的缓存应答 |

- **透明代理和出口策略。** TUN 和 Gost 两条路径都经私有 veth 进入 Mihomo 的独立网络命名空间，主机防火墙在边界复核核心流量。B 下的 `rules` 按订阅规则决定直连、代理或拒绝，`proxy-only` 将受支持的流量固定到一个已批准的出口，并阻断应用独立出站。可选的 WireGuard 对端承载固定 SOCKS5 中继及原生 IPv4/IPv6 ping。见[流量路径](docs/CN/network_CN.md#流量路径)和[路由策略](docs/CN/proxy_CN.md#路由策略)。
- **订阅信任和 GUI 权限。** 由 root 管理的服务登记订阅并授权通过审计的配置快照，低权限进程负责下载和生成候选配置。系统服务管理路由策略，保留 DNS 和代理监听端口。Clash Verge 通过受限的 Unix socket 查看状态和选择获准的节点。见[受控订阅与服务身份](docs/CN/proxy_CN.md)。
- **DNS 分流和过滤。** 系统查询沿 `systemd-resolved → dnsmasq → Unbound 或 Mihomo` 解析。nftables 接管普通出站的 53 端口查询，dnsmasq 与 Mihomo 共用域名过滤列表。在允许直连例外的模式下，经批准的 Wi-Fi 分流 DNS 和 Tailscale MagicDNS 各有受限路径。见 [DNS 策略](docs/CN/dns_CN.md)。
- **上行链路和本地服务。** dhcpcd 管理有线，NetworkManager 管理 Wi-Fi。支持按 Wi-Fi 配置管理 MAC、DHCP 和 IPv6 身份，也可固定 IPv4 网关的 ARP 绑定。防火墙按模式放行必要的链路维护、使用 NTS 认证的 Chrony 时间同步，以及 Sunshine 等已声明的 Tailscale 服务。见[链路身份](docs/CN/identity_CN.md)和[直连例外](docs/CN/egress_CN.md)。
- **访客和浏览器隔离。** 经主机路由的访客流量按网桥和源网段授权，B 中可显式启用 IPv4 TPROXY。直接创建主机 socket 的受限容器后端按独立 UID 识别。日用 Firefox 遵循主机策略，身份浏览器使用独立网络命名空间和仅在 B 开放的专用中继，门户登录在 A 中使用临时浏览器。见[访客网络](docs/CN/network_CN.md#访客与容器)和 [Firefox 网络策略](docs/CN/browser_CN.md#网络策略与身份)。

[网络模块源码](system/config/network/)和 [NixOS 虚拟机测试定义](tests/checks.nix)涵盖模式切换、DNS 路由、访客隔离和 WireGuard 出口。配置与恢复方法见[网络文档](docs/CN/README_CN.md)。

## 桌面与系统

- **Wayland 桌面。**
  默认使用 Niri，Noctalia 提供状态栏、启动器、通知和随壁纸变化的主题。
  GDM 中也可选择 GNOME，中文输入使用 Fcitx5 与 Rime。

- **存储与持久化。**
  Disko 声明 LUKS2 加密的 Btrfs 布局。
  每次启动重建根子卷，Impermanence 将选定的系统和应用状态保存在 `/persist`。

- **开发环境。**
  配置 Nushell、带语言服务器的 Neovim、VS Code 和 Zed，提供 rootless Docker、虚拟化工具，以及供传统 Linux 软件使用的 FHS 环境。

- **Firefox。**
  原生软件包包含锁定的隐私设置、文件系统沙箱、独立身份 profile 和临时门户浏览器，启动时检查网络状态。

- **Hermes Agent。**
  通过 Nix 管理 agent、gateway、dashboard、MCP 集成和技能。
  Agent 数据保存在 `/var/lib/hermes`，容器工作负载使用独立账户和 Docker 后端。

- **硬件与远程访问。**
  按作者的机器配置 AMD/NVIDIA 显卡、音频、蓝牙、USBGuard，以及经 Tailscale 使用的 Sunshine 串流。
  首次启用及设备授权见 [USBGuard 使用说明](scripts/README_CN.md#usbguard-策略)。

各功能的模块入口见[源码目录](docs/CN/layout_CN.md)。

## 使用这套配置

这是一套针对具体机器编写的配置。
用于自己的设备前，需要调整账户、硬件标识、磁盘布局、持久化路径和加密密钥。
在已有系统上使用时，尤其要先理解 Disko 与开机重置根目录的行为。
全新安装可参考独立的 [Disko 安装示例](https://github.com/lfourneen/nixos-disko-lf)。

克隆仓库：

```sh
git clone https://github.com/lfourneen/nixos-lf.git
cd nixos-lf
```

从以下文件开始调整：

| 文件 | 调整内容 |
|---|---|
| [flake.nix](flake.nix) | 系统输出、用户集成和依赖版本 |
| [system/config/user.nix](system/config/user.nix) | 账户与用户组 |
| [system/config/machine.nix](system/config/machine.nix) | 网络接口、地址、端口和服务身份 |
| [system/hardware/](system/hardware) | 磁盘、启动、CPU 和 GPU 配置 |
| [system/hardware/partition.nix](system/hardware/partition.nix) | 需要持久保存的目录与文件 |
| [system/programs/sops.nix](system/programs/sops.nix) | 密钥声明和 Age 私钥位置 |
| [home/](home) | 桌面、shell、编辑器和应用偏好 |

本机选择严格加密 DNS，并要求登记代理来源。
使用代理前先完成[来源登记](docs/CN/operations_CN.md#来源登记)。
请求代理后，如果核心不可用，系统会阻断应用流量，直到代理恢复或你主动选择直连。

先构建调整后的系统：

```sh
nix build --no-link .#nixosConfigurations.lfour.config.system.build.toplevel
```

准备好后再激活：

```sh
sudo nixos-rebuild switch --flake .#lfour
```

如果修改了 flake 输出名，请相应替换命令中的 `lfour`。
后续重建也应指向你实际维护的仓库。

发行分支和共用的 `stateVersion` 都在 [flake.nix](flake.nix) 中配置。
升级 NixOS 时，同步调整 Nixpkgs 和 Home Manager 的 input 分支。
`stateVersion` 应保持不变，只有检查兼容性变化并完成相关应用数据迁移后才调整。

## 日常网络操作

| 命令 | 用途 |
|---|---|
| `clash-on` | 请求代理并启动受管核心 |
| `clash-off` | 切换到直连 |
| `clash-rules` | 选择订阅规则路由并请求健康 B |
| `clash-proxy-only` | 选择严格代理路由并请求健康 B |
| `clash-egress [ID]` | 列出已接受来源的出口候选，或经受保护事务选择其中一个 |
| `sudo proxy-control recover [--cached]` | 在 C 中下载新候选，或应用已有下载，期间持续阻断应用流量 |
| `proxy-status` | 查看模式、DNS 和就绪状态 |
| `clash-diff` | 只读查看来源变化，不改变网络模式 |
| `firefox-identity` | 通过严格代理入口打开独立身份 profile |
| `firefox-portal http://192.168.1.1/` | 在直连模式打开临时门户会话 |

A 表示直连，B 表示代理健康可用，C 在代理不可用时阻断应用流量。
关闭 GUI 保留受管核心运行。使用 `clash-off` 切换到 A。
Clash Verge 保持上游打包版本，用于选择和监控节点。系统会丢弃 GUI 发出的 System Proxy、TUN 和核心模式请求。
使用系统命令选择 `rules` 或 `proxy-only`，两种路由均使用 Mihomo 的 `mode: rule`。
运行 `clash-on` 时，受控订阅优先恢复已接受快照。进入健康 B 后，再在后台获取启动更新。
已登记订阅也会每 30 分钟刷新，正常节点轮换自动批准。
成功更新不弹窗，失败通过桌面通知和 `proxy-status` 提示。
来源管理、诊断和离线恢复见[操作与诊断](docs/CN/operations_CN.md)。

## Hermes 与密钥

Hermes 配置和技能位于 [system/programs/hermes/](system/programs/hermes)。
Nushell 中可直接运行 `hermes`，其它 shell 使用 `sudo -u hermes -i hermes`。
容器入口与数据管理见 [Hermes 容器](docs/CN/operations_CN.md#hermes-容器)。

密钥由 sops-nix 管理。系统服务默认使用 `/persist/secrets/age/system.key` 处的 root 专属 Age 身份解密，因此首次重建前需要按说明放好密钥。
接管现有 `age.txt` 或 `keys.txt`、从零生成身份、把 recipient 写入 `.sops.yaml`，以及重新加密已有文件的步骤见 [脚本说明](scripts/README_CN.md#system-age-keypy)。
私钥不要提交到 Git，并做好离线备份。
要回退到 `~/.config/sops/age/keys.txt`，设置 `my.secrets.systemAgeKey.enable = false`。

以 root 编辑系统秘密时，显式指定系统身份：

```sh
sudo SOPS_AGE_KEY_FILE=/persist/secrets/age/system.key \
  sops edit system/secrets/secrets.yaml  # API 和消息服务凭据
sudo SOPS_AGE_KEY_FILE=/persist/secrets/age/system.key \
  sops edit system/secrets/soul.yaml     # Hermes 人格设置
sudo SOPS_AGE_KEY_FILE=/persist/secrets/age/system.key \
  sops edit system/secrets/proxy.yaml    # 代理来源清单
```

以 root 保存会改变文件属主，提交前用 `sudo chown "$USER": ...` 恢复。
保存时 sops 会重新加密文件，重建系统后应用修改。
密钥名称及服务属主见 [sops.nix](system/programs/sops.nix)。

## 仓库导航

| 目录 | 内容 |
|---|---|
| [system/](system) | 系统服务、硬件、网络策略和软件包 |
| [home/](home) | Home Manager 模块和用户应用 |
| [overlays/](overlays) | 软件包覆盖 |
| [local-data/](local-data) | 纳入版本管理的构建输入，如 DNS 黑名单快照 |
| [maintenance/](maintenance) | 全仓库更新台账和核查命令 |
| [scripts/](scripts) | 配置复制工具和黑名单刷新脚本 |
| [tests/](tests) | 软件包、策略和隔离 VM 检查 |
| [assets/](assets) | README 图标和署名 |
| [docs/](docs/CN/README_CN.md) | 架构、配置、日常操作和维护手册 |

运行 `python3 scripts/check-maintenance.py` 校验更新台账。
修改网络组件或更新相关依赖前，可按[维护手册](docs/CN/maintenance_CN.md)选择检查，并核对本地补丁。

## 许可证

本项目基于 MIT License 授权 - 详见 [LICENSE](LICENSE) 文件。
