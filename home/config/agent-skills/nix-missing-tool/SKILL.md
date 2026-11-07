---
name: nix-missing-tool
description: 在 NixOS 上遇到缺失命令、库或编译依赖，或需要维护仓库的 Nix 开发环境时使用。
---

# 补齐 Nix 开发依赖

先确认目标项目的位置和依赖的作用范围，再查包并运行原先失败的命令。系统工具由 Nix 提供；Python 虚拟环境和 npm、pnpm、uv 等项目依赖继续沿用项目已有的管理方式。

## 选择环境

| 当前需求 | 处理方式 |
|---|---|
| 目标项目已有 Nix 环境 | 阅读该项目的环境定义和 README，复用对应的开发 shell、包或应用输出。 |
| 少量包、临时诊断或一次性脚本 | 默认用 `nix-shell -p` 或 `nix shell`，无需新建 Flake。 |
| 协调较多依赖或环境变量、统一执行项目脚本、提供用户运行入口 | 按实际工程需求建立 Flake；用户明确要求或表达强烈倾向时也可采用。 |
| 用户希望长期安装的通用工具 | 加入现有 Home Manager 或 NixOS 包列表。 |

涉及仓库环境定义时，读 [仓库 Flake 工作流](references/project-flakes.md)。新建 Flake 的 nixpkgs 输入使用 `nixos-unstable`，由 `flake.lock` 固定 revision；已有项目保持其输入约定。

## 查找包

1. 用 `command -v` 检查命令是否已在 PATH 中；项目有环境时也检查该环境内是否可用。
2. 有 NixOS MCP 时，用其实际暴露的查询接口查找属性路径。MCP 不可用时用 `nix search nixpkgs 关键词`。
3. 包属性与命令名可能不同，例如 `ripgrep` 提供 `rg`。只有文件名或库名时，可用已有 nix-index 索引反查：

   ```bash
   nix-locate -w 'bin/rg'
   ```

索引缺失时先尝试包搜索；确需文件级反查再生成或使用预构建索引。`nix-locate` 本身由 `nix-index` 包提供。

## 临时运行

下面以 ripgrep 为例，将包名和命令替换为当前任务需要的工具。

```bash
command -v rg
nix-shell -p ripgrep --run 'rg --version'
nix shell nixpkgs#ripgrep -c rg --version
nix run nixpkgs#ripgrep -- --version
```

- 多个工具放进同一个环境，例如 `nix-shell -p cmake ninja pkg-config --run 'cmake --version'`；实际构建时换成当前任务的项目命令。
- 自动执行任务用 `--run` 或 `-c`；交互式 shell 留给用户需要手动操作的场景。
- `nix run` 需要包具有可运行入口；只需把工具加入 PATH 时用 `nix shell`。
- `nix-shell -p` 按 `NIX_PATH` 解析 nixpkgs，`nixpkgs#` 按 registry 解析。查询结果与本地版本不一致时核对来源；需要锁定版本时复用项目输入或指定 revision。

工具可运行后，重新执行原先失败的构建、测试或诊断命令，确认实际依赖已满足。

## 按错误处理

| 现象 | 下一步 |
|---|---|
| `attribute … not found` | 重新查属性路径及所用 nixpkgs revision，不把命令名直接当包名。 |
| 进入 shell 后仍缺命令 | 检查失败的是主命令还是其子命令，将缺失工具放入同一环境。 |
| 缺头文件、链接库或 `.so` | 读 [编译与运行时依赖](references/native-dependencies.md)，区分构建依赖和运行时库。 |
| 文件存在却报 `No such file or directory` | 检查脚本解释器或 ELF 动态加载器，不能仅按“文件不存在”处理。 |
| 下载或 daemon 访问失败 | 检查具体网络、缓存或权限错误；使用客户端的授权机制处理受限操作。缓存齐全时可用 `--offline`。 |
| 临时工具需要构建大量依赖 | 先用 `nix build --dry-run` 检查对应包的构建计划，核对平台和替代缓存；`--offline` 不会禁止源码构建，也不保证构建过程完全无网络。 |

临时依赖不写入用户 profile。只有当前任务需要持久安装时才修改声明式配置；补工具不附带更新全部输入、切换系统或清理 Nix store。
