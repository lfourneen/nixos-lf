# NixOS 配置

[English](README.md) | [简体中文](README_CN.md)

基于 Nix flakes 的声明式 NixOS 系统配置，包含定制化的桌面环境与各种生产力工具。

---

## ⚠️ 注意 / 警告 ⚠️

> **请勿直接照搬此配置！**

* **修改用户名**：请将配置中所有 `lfour` 的引用替换为你自己的用户名。
* **硬件分区**：本配置使用 `disko`，需要编辑 `system/hardware/disko.nix` 以匹配你的磁盘布局。（参考[这个全新安装示例](https://github.com/LFour86/nixos-disko-lf)。）
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
│   ├── sync-to-git.sh      # 将 /etc/nixos 复制到 ~/Downloads/nixos，宽松权限
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

* **`scripts/sync-to-git.sh`** — 将 `/etc/nixos` 复制到 `~/Downloads/nixos`，把所有权改为当前用户并设置宽松权限（目录 755 / 文件 644），以便提交到 Git。
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

* NixOS 26.05 及 unstable
* Home Manager
* Noctalia shell
* Disko
* Impermanence
* Nix-Flake
* Hermes Agent
* Sops-Nix
* MCP-NixOS

---

## 许可证

本项目基于 MIT License 授权 - 详见 LICENSE 文件。
