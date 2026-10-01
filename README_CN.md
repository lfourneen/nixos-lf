# NixOS 配置

[English](README.md) | 简体中文

基于 Nix flakes 的声明式 NixOS 系统配置，包含定制化的桌面环境与各种生产力工具。

---

## ⚠️ 注意 / 警告 ⚠️

> **请勿直接照搬此配置！**

* **修改用户名**：请将配置中所有 `lfour` 的引用替换为你自己的用户名。
* **硬件分区**：本配置使用 `disko`，需要编辑 `system/hardware/disko.nix` 以匹配你的磁盘布局。（参考[这个全新安装示例](https://github.com/lfourneen/nixos-disko-lf)。）
* **按需调整**：此配置针对我的硬件和偏好定制，使用前请审查并调整所有设置！

---

## 特性

* **桌面环境**：见 `system/config/desktop.nix`
* **文件系统**：BTRFS + LUKS2
* **Shell**：带自定义配置的 Nushell
* **系统管理**：使用 Home Manager 管理用户配置
* **硬件支持**：NVIDIA、AMDGPU、蓝牙、音频等硬件配置
* **安全**：SSH、防火墙及安全加固
* **虚拟化**：Docker 等虚拟化工具

---

## 代理链路

出站流量由一组本机组件串联处理：

| 组件 | 端口 / 接口 | 作用 |
|---|---|---|
| Clash Verge (mihomo) | `127.0.0.1:7897`，TUN 设备 `Mihomo` | 代理内核与 DNS 解析器 |
| `gost-pac`（uid 987） | `127.0.0.1:33332`（HTTP）、`127.0.0.1:33333`（透明重定向） | 本机中转；Clash 运行时转发到 mihomo，未运行时纯直通 |
| `dnsmasq` | `127.0.0.1:1054` | 系统唯一的 DNS 入口 |
| `unbound` | `127.0.0.1:1055` | mihomo 不可用时使用的加密（DoT）解析器 |
| nftables | — | 断网保护（kill switch）、透明重定向 |

支持代理设置的应用被指向 `127.0.0.1:33332`（会话环境变量、GSettings、Flatpak 覆盖）；忽略代理设置的应用的 TCP 流量由 nftables 重定向到 `127.0.0.1:33333`。

### 模式

`proxy-mode` 只在 Clash 运行时装载断网保护与 `:33333` 重定向，因此机器有两种保证不同的状态：

| | **模式 A —— Clash Verge 已关闭**（本机常态） | **模式 B —— Clash 运行中，TUN 已建立** |
|---|---|---|
| 如何进入 | 关闭 Clash Verge | 启动 Clash Verge（`my.proxy.tunMode` 决定 Nix 是否需要 TUN，是否真的创建由 GUI 决定） |
| 强制了什么 | `nftables.service` 不依赖 Clash，在启动时装载 `table inet filter`：`input policy drop`、连接跟踪状态规则、martian 源丢弃、公网 IPv6 丢弃、`forward` 链与 masquerade 全部生效。DNS 走 dnsmasq (:1054)，再到加密 DoT 解析器（unbound :1055） | 以上全部，外加：TUN 接管本机产生的流量，mihomo 的 `dns-hijack` 接管 TUN 路由的 :53，`proxymode_*` 片段加上断网保护（非 root 经上行口访问公网地址被丢弃）、`:33333` 重定向与访客策略 |
| 保证了什么 | 直连、真实地址、没有断网保护；门户/局域网/CGNAT 地址按设计可达，DHCP 下发的明文解析器是允许的兜底，但只在两个有界的时间窗内生效（见下文 DNS） | 本机产生的每条流要么走 mihomo，要么被丢弃；核心、TUN 或节点失败时 DNS 停止、出站被拒，而不是回退直连；热点/虚拟机客户端的流量被拒绝，而不是被转发出去 |
| 不保护什么 | 源地址与全部目的地址对 ISP 以及路径上的任何人都可见；明文协议依然可读；DNS 过滤只是缓解手段，不是墙；自带 DoH/DoT 的应用会绕过这条解析链路 | 策略判为 DIRECT 的流量以真实地址直出；绑定源地址/网卡的套接字会绕过 TUN，只由断网保护兜住；非 root 的 UDP `3478/5349`（STUN/TURN）被丢弃；手工执行 `sudo nft flush ruleset` 会清掉全部规则，直到 `nftables.service` 下次启动 |

模式 A 不隐藏源地址，也不加密应用发出的内容。模式 B 只和能看见它的检查一样强：`nftables-verify` 读取内核状态——TUN 设备、mihomo 自己的 `table inet mihomo`、FIB 规则、监听端口、实际 uid——而不是相信状态文件；失败会通过 `journalctl -t netsec-alert` 与 `/run/netsec/failed` 报警。

Clash 关闭时主机就是普通直连：门户、DNS 与日常上网都无需预热，`gost-pac` 也继续以纯直通方式提供 `:33332`（而不是拒绝连接），因此只认代理环境变量的应用（nix-daemon、Flatpak 应用、hermes、curl）仍可用。Clash 运行时 `gost-pac` 只转发到 `127.0.0.1:7897`：核心或节点缺失只会让连接失败，不会回退直连；模式判据是核心进程是否位于 `clash-verge.service` 的 cgroup 内（而不是进程名），因此核心崩溃后仍是 fail-closed。DNS 同理：mihomo 不可用期间 `dns-pac` 把 dnsmasq 指向加密解析器。

这些守护脚本是事件驱动的：`proxy-net-watch.path` 监听 mihomo 的控制 socket（及其目录），`proxy-net-wake.service` 在 Clash 核心起停时**同时**唤醒 `proxy-mode`、`dns-pac` 与 `gost-pac`；链路变化则由 NetworkManager dispatcher 钩子触发同样的事。每个循环保留一个自适应兜底——切换中或降级时快，稳定后放宽到 10/15/30 秒——因此切换在同一瞬间完成，稳态几乎零开销。

记录下来的状态在 `/run/proxy-mode/status`（`proxy` | `direct` | `unenforced`）、`/run/gost-pac/status`、`/run/dns-pac/status` 与 `/run/dns-pac/reason`（Nushell 配置中的 `proxy-status` 命令会显示）。`unenforced` 表示 Clash 正在运行、但 enforcement 片段没有装载：`nftables-verify` 会把它当作失败，并且 `nftables.service`/`proxy-mode.service`/`nftables-verify.service` 失败时都会通过 `netsec-alert@` 报警。这些文件描述的是守护脚本自身的状态，而不是某条连接实际走的路径。

### DNS

`systemd-resolved` 使用 `127.0.0.1:1054` 上的 dnsmasq，它的上游随模式变化。每个模式只有一个解析器，不存在常驻的第二个 server 可供回退：

* 模式 B：`127.0.0.1:1053`（mihomo）。应答来自 mihomo 转发的 DoH 上游，由它完成 DNSSEC 校验：`dig +dnssec @127.0.0.1 -p 1053 cloudflare.com` 带 `ad` 标志与 RRSIG，`dnssec-failed.org` 返回 SERVFAIL。由于没有别的上游，这个拒绝会直接到达客户端，而不会被不做校验的解析器替换。`nftables-verify` 会校验这一点，但只在 `dns-pac` 真的把解析器切到 `:1053` 之后（它的状态可能滞后于 `proxy-mode`）：要求 `:1054` 拒绝对 `dnssec-failed.org` 下随机标签的查询（SERVFAIL 或超时都算被拒绝），同时对照域名能正常解析，并带重试以挺过 dnsmasq 的异步重启。
* 模式 A：`127.0.0.1:1055`（unbound DoT 到 AliDNS）。unbound 不做校验（`enableRootTrustAnchor = false`，且上游会剥掉 RRSIG），因此模式 A 没有 DNSSEC 保护；对这个上游开启校验会让所有签名域名 SERVFAIL，所以保持关闭。
* 模式 A 且加密链路不可达时：DHCP 下发的解析器（用 `dhcpcd -U` 读取）只在两个有界、有日志的时间窗内被追加——链路建立或加密链路失败后的 120 秒引导窗，以及 NetworkManager 报告 `portal`/`limited` 期间。窗口之外 DNS 会停止并在 `/run/dns-pac/reason` 里说明原因，而不是退化为明文。`touch /run/dns-pac/force-plaintext` 可手工强制启用明文兜底。

凡不是 dnsmasq 自身上游 socket 的明文解析器都会被重定向进 dnsmasq（上行口的 `:53`，IPv4 与 IPv6，两种模式都生效），因此门户/局域网/CGNAT 范围内硬编码的解析器不会明文出网。

### 说明与限制

* TUN 设备启用时，nftables 到 `:33333` 的重定向**承载流量**，并非休眠：所有未进入 TUN 的 TCP 流（绑定源地址/网卡的套接字，以及手工 flush 之后的流量）都由它承载。`gost` 是这些流的单点故障，`nftables-verify` 会校验它的监听端口是否存在。经它到达的流会被改源为 `127.0.0.1`：任何按来源匹配的策略规则对它们都没有意义。
* 只有 `unbound`（DoT，tcp/853）与 `systemd-timesyncd`（udp/123）可以直接出网做 DNS 与 NTP；其它公网解析器的明文查询，对豁免集合之外的进程会被丢弃——除非它们先被重定向进 dnsmasq。
* 断网保护会丢弃非 root 进程的直接出站流量，回环、局域网地址、DNS/NTP 与组播除外。只有 root 被豁免，而这正是 mihomo 的 DIRECT 规则在代理模式下能直接出网的前提：DIRECT 的可用性隐含依赖 mihomo 以 root 运行。
* `gost-pac` 被排除在 `:33333` 重定向之外，这样它的直通中继不会拨回自己；它的出站仍受断网保护约束——代理模式装载期间会被丢弃（模式短暂过期只会造成瞬时的连接失败，不会造成直连泄漏）。
* 探测只认属于 `clash-verge.service` 的监听者：判据读取监听者的 cgroup，本机进程无法伪造，因此仅在 `127.0.0.1:7897` 上监听并不能把流量引过去。“Clash 在运行”的判据要求**核心**位于该 cgroup 内，只有 GUI 进程不再算数。
* mihomo 的外部控制口是一个全局可写的 unix socket 且不校验 secret，因此以登录用户身份运行的任意进程都能重配内核——包括把节点置为 DIRECT。单用户桌面下接受这一取舍；合并配置无法覆写控制口设置。
* 探测同时要求一个国内域名（走 DIRECT）与一个由代理组承载的域名，因此探测失败表示整条链路无法承载流量，而不只是某个上游节点不可用。
* `systemctl stop nftables.service` 不再移除防火墙：拆除与装载在同一个 `nft -f` 事务里，且模块的 deletions 文件为空，因此已装载的规则会保留到下次启动替换为止。手工复位仍然是 `sudo nft flush ruleset`（它同时会清掉 mihomo 自己的表，直到 TUN 重启）。
* 热点与虚拟机：转发流量不经过 output 链、也不带 uid，断网保护看不到它。Clash 运行期间 `proxymode_forward` 拒绝客户端发往公网目的地址的上行口流量（门户/局域网/CGNAT 仍可达）；Clash 关闭时热点/虚拟机规则与之前一致。客户端流量永远不会被代理——要么被 TUN 承载，要么被拒绝。
* 模式 A 的网络姿态（`my.hardening.*`）：热点 AP 默认**关闭**（其 accept 按来源网段匹配，在 wlo1 作为客户端时可被伪造——需要共享上行时再显式打开）；tailnet 只能访问 `my.hardening.tailnet{Tcp,Udp}Ports` 列出的端口，而不是所有通配监听。抓包权限（`dumpcap`/`usbmon`）默认关闭：抓包需要 `sudo dumpcap`。无线连接默认沿用 NetworkManager 自身的 MAC 策略，除非设置 `my.hardening.wifi.clonedMacAddress`（例如 `stable`）。
* `nftables-verify` 只做检查、不做修复：它从不改动状态（会自修复的检查会掩盖自己的失败）。`systemctl start nftables-verify-repair.service` 是显式、可选的修复入口。

### 仓库说明

* `system/programs/ssh.nix` 未被导入（见 `system/programs/default.nix`），因此不部署 sshd 单元与 `:22` 监听；启用时还需要放开 `system/config/network.nix` 里的 `tcp dport 22` 规则。
* `gost` 安装进 initrd（`boot.initrd.systemd.extraBin`）而不是 `environment.systemPackages`，因此不在主系统 PATH 上。`gost-pac` 直接使用 store 路径；`curl` 与 `sed` 仍由 nixpkgs 默认包提供。

---
## 使用方法

1. 克隆本仓库：
```bash
git clone https://github.com/yourusername/nixos-lf.git
cd nixos-lf/scripts/ && ./push-to-dir.sh

```

2. 按需修改配置文件，例如：
* 在 `system/config/user.nix` 中，将 `"lfour"` 改为你的用户名。


3. 构建并切换到新配置：
```bash
sudo nixos-rebuild switch --flake .#yourname

```

*(将 `yourname` 替换为你的实际主机名或要部署的 flake 输出名。)*

---

## 目录结构

```
.
├── flake.nix               # Flake 主配置
├── flake.lock              # Flake 锁文件
├── home/                   # 用户配置
│   ├── config/             # 用户级配置
│   ├── programs/           # 用户程序
│   ├── wallpapers/         # 壁纸文件
│   └── userpkgs/           # 用户软件包
│
├── overlays/               # Nixpkgs overlays
│   └── local_apps/         # 自定义本地应用
│
├── scripts/                # 工具脚本
│   ├── sync-to-git.sh      # 将 /etc/nixos 复制到 ~/Projects/Nix/nixos，宽松权限
│   └── push-to-dir.sh      # 将仓库配置推回 /etc/nixos，安全权限
│
└── system/                 # 系统级配置
    ├── config/             # 系统配置
    ├── hardware/           # 硬件相关配置
    ├── modules/            # 内核模块配置
    ├── programs/           # 系统程序与服务
    ├── secrets/            # 加密密钥（sops）
    └── systempkgs/         # 系统软件包

```

---

## 脚本

* **`scripts/sync-to-git.sh`** — 将 `/etc/nixos` 复制到 `~/Projects/Nix/nixos`（如 `~/Projects/Nix` 不存在则自动创建），把所有权改为当前用户并设置宽松权限（目录 755 / 文件 644），以便提交到 Git。
* **`scripts/push-to-dir.sh`** — 上面脚本的逆操作。将仓库中的 `home/`、`overlays/`、`system/` 和 `flake.nix` 复制到 `/etc/nixos` 并应用安全权限（目录 700 / 文件 600）。必须使用 `sudo` 运行。

---

## Hermes / Sops-Nix

本配置使用 `sops-nix` 配合 Age 密钥对来管理 `hermes` 的密钥。

### 1. Age 密钥初始化

如果是在恢复/迁移已有系统，请选择**选项 A**；首次配置请选择**选项 B**。

#### 选项 A：迁移 / 恢复已有配置（推荐）
如果你已经有备份的 Age 密钥对，只需将 `keys.txt` 复制到目标位置：

```bash
mkdir -p ~/.config/sops/age
cp /path/to/your/backup/keys.txt ~/.config/sops/age/keys.txt
chmod 600 ~/.config/sops/age/keys.txt

```

#### 选项 B：全新初始化

如果是为全新配置生成新的密钥对：

1. 生成新的 Age 密钥：
```bash
mkdir -p ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt

```

> ⚠️ **安全警告：** `keys.txt` 是你的私钥。切勿提交到 Git 或公开暴露！请备份到安全位置。

2. 从输出或文件中获取你的公钥：
```bash
# 公钥格式：age1...

```

3. 在配置根目录创建或更新 `.sops.yaml`：
```yaml
creation_rules:
  - path_regex: secrets\.yaml$
    key_groups:
      - age:
          - "age1ql30gw8xxxxxxxxxxxxxxxxxxxxxxxxxxx" # 在此粘贴你的公钥

```

4. 创建并编辑加密密钥文件：
```bash
cd /etc/nixos/system/secrets
sops secrets.yaml

```

以 YAML 格式添加你的 API 密钥（例如 `hermes_api_key: "sk-proj-1234567890abcdef"`）。保存后文件内容会自动加密。

> 之后添加新密钥（所有密钥均为 YAML 顶层条目）：

```bash
cd /etc/nixos/system/secrets

# 命令行直接设置。key 使用方括号索引语法，value 必须是合法 JSON 字符串（外层多加一层引号）：
sops set secrets.yaml '["github_token"]' '"ghp_xxx"'

# 从文件读取（长值，如 ssh 私钥）
sops set --value-file secrets.yaml '["ssh_host_ed25519_key"]' /tmp/key

# 或从 stdin 读取
echo -n 'ghp_xxx' | sops set --value-stdin secrets.yaml '["github_token"]'

# 交互式：编辑器打开明文，保存后自动重新加密
sops secrets.yaml

# 验证
sops -d secrets.yaml
```

### 2. 运行 Hermes

部署 NixOS 配置（`update`）后，可用以下任意一种方式启动 Hermes：

```bash
# Nushell 辅助函数（以'sudo -u hermes -i hermes'运行，带沙箱提示）
hermes

# 纯 bash
sudo -u hermes -i hermes

```

---

## 自定义

* **系统配置**：编辑 `system/config/` 下的文件
* **用户配置**：编辑 `home/config/` 下的文件
* **程序**：修改 `system/programs/` 和 `home/programs/`
* **硬件**：调整 `system/hardware/` 中的设置
* **应用**：见 `home/userpkgs/`、`overlays/` 和 `system/systempkgs/`

---

## 依赖

* NixOS 26.05 及 Unstable
* Home Manager
* Noctalia shell
* Disko
* Impermanence
* Nix-Flatpak
* Hermes Agent
* Sops-Nix
* MCP-NixOS
* LLM-Agents

---

## 许可证

本项目基于 MIT License 授权 - 详见 LICENSE 文件。
