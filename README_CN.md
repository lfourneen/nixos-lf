# nixos-lf

[English](README.md) | 简体中文

这是一套以 Niri、Noctalia 和 Home Manager 为基础的个人 NixOS 工作站配置。
桌面、加密存储、启动时重置的根文件系统、开发工具、AI agent 和网络策略都由同一个 Nix flake 管理。

配置面向 `x86_64-linux`，基于 NixOS 26.05，部分软件来自 Unstable。
系统构建入口为 `nixosConfigurations.lfour`。

## 包含哪些功能

- **Wayland 桌面。**
  默认使用 Niri，Noctalia 提供状态栏、启动器、通知和随壁纸变化的主题。
  GDM 中也可选择 GNOME，中文输入使用 Fcitx5 与 Rime。

- **存储与持久化。**
  Disko 声明 LUKS2 加密的 Btrfs 布局。
  每次启动重建根子卷，Impermanence 将选定的系统和应用状态保存在 `/persist`。

- **开发环境。**
  配置 Nushell、带语言服务器的 Neovim、VS Code 和 Zed，提供 rootless Docker、虚拟化工具，以及供传统 Linux 软件使用的 FHS 环境。

- **受管网络。**
  nftables、本地 DNS 和 Mihomo 协同控制直连、代理和阻断三种状态。
  Clash Verge 提供图形界面，核心运行在独立网络命名空间中，只加载已登记来源授权的配置。

- **Firefox。**
  原生软件包包含锁定的隐私设置、文件系统沙箱、独立身份 profile 和临时门户浏览器，启动时检查网络状态。

- **Hermes Agent。**
  通过 Nix 管理 agent、gateway、dashboard、MCP 集成和技能。
  Agent 数据保存在 `/var/lib/hermes`，容器工作负载使用独立账户和 Docker 后端。

- **硬件与远程访问。**
  按作者的机器配置 AMD/NVIDIA 显卡、音频、蓝牙、USBGuard，以及经 Tailscale 使用的 Sunshine 串流。
  首次启用及设备授权见 [USBGuard 使用说明](scripts/README_CN.md#usbguard-策略)。

各功能的模块入口见[源码目录](docs/CN/layout_CN.md)。
网络设计和日常命令见[网络文档](docs/CN/README_CN.md)。

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
| `proxy-status` | 查看模式、DNS 和就绪状态 |
| `clash-diff` | 只读查看来源变化，不改变网络模式 |
| `firefox-identity` | 通过严格代理入口打开独立身份 profile |
| `firefox-portal http://192.168.1.1/` | 在直连模式打开临时门户会话 |

A 表示直连，B 表示代理健康可用，C 在代理不可用时阻断应用流量。
关闭 GUI 不会切换到 A。
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
| [scripts/](scripts) | 配置复制工具和黑名单刷新脚本 |
| [tests/](tests) | 软件包、策略和隔离 VM 检查 |
| [docs/](docs/CN/README_CN.md) | 架构、配置、日常操作和维护手册 |

修改网络组件或更新相关依赖前，可按[维护手册](docs/CN/maintenance_CN.md)选择检查，并核对本地补丁。

## 许可证

本项目基于 MIT License 授权 - 详见 [LICENSE](LICENSE) 文件。
