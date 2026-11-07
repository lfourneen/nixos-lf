# 仓库 Flake 工作流

用于复用、扩展或按技能中的适用条件新建目标项目的环境。下文的项目路径指当前任务处理的工程。

先确定目标项目的绝对路径。Git 项目可用 `git -C "$project_root" rev-parse --show-toplevel` 查根目录；非 Git 项目按任务给定的位置确定。下列 `.#名称` 中的点指目标项目根目录。从其他目录调用时，将点替换为项目的绝对路径，例如：

```bash
project_root='/目标项目的绝对路径'
nix develop "$project_root" --command 项目命令
nix build "$project_root#名称"
nix run "$project_root#名称" -- 参数
```

Nix 不会自动把命令的工作目录切到项目根目录。构建或测试依赖相对路径时，先进入该项目再执行，或向工具传入明确的源码和输出路径。

## 复用已有环境

阅读 `flake.nix`、相关 `.nix`、`flake.lock`、README 和 CI，确定输出名称、目标系统及项目命令。仓库使用 `shell.nix` 时沿用 `nix-shell`；仅有 `default.nix` 时先确认它定义的是包、开发环境还是其他值。

| 输出 | 调用方式 |
|---|---|
| `devShells.<system>.default` | `nix develop --command 项目命令` |
| `devShells.<system>.<name>` | `nix develop .#名称 --command 项目命令` |
| `packages.<system>.<name>` | `nix build .#名称` |
| `apps.<system>.<name>` | `nix run .#名称 -- 参数` |
| `checks.<system>` | `nix flake check` |

复用现有环境时可加 `--no-update-lock-file`，锁文件需要变化时会直接失败。`--no-write-lock-file` 只禁止写回文件，仍允许在内存中重算，不能据此认定使用了原有锁定。锁缺失或与定义不一致时，在当前任务范围内修复并查看差异。

临时诊断工具可用临时 shell；项目构建实际依赖的工具加入已有开发环境。Python 库需与使用的解释器配套，例如 `python3.withPackages`。项目的语言依赖继续使用原有 lock 和构建流程。

## 新建开发环境

以下为 Linux C/C++ 项目的示例，按实际目标系统和依赖裁剪。使用 `nixos-unstable` 选择输入分支，再通过 lock 固定实际 revision。

```nix
{
  description = "Project development environment";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { nixpkgs, ... }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
    in {
      devShells = nixpkgs.lib.genAttrs systems (system:
        let pkgs = nixpkgs.legacyPackages.${system};
        in {
          default = pkgs.mkShell {
            packages = with pkgs; [ cmake ninja pkg-config ];
            PROJECT_MODE = "development";
          };
        });
    };
}
```

`mkShell` 的默认 stdenv 提供 C/C++ 编译器。其余依赖和项目环境变量集中声明；密钥在运行时注入，不写入 Nix 表达式或 store。已有项目使用 flake-parts 等组织方式时沿用其结构。

## 提供脚本入口

开发 shell 用于执行仓库命令；需要 `nix run` 入口时，为脚本声明运行依赖和应用输出。例如，在对应系统的 `pkgs` 作用域中定义：

```nix
let
  runner = pkgs.writeShellApplication {
    name = "project-check";
    runtimeInputs = with pkgs; [ bash cmake ninja pkg-config stdenv.cc ];
    text = ''exec bash ${./scripts/check.sh} "$@"'';
  };
in {
  packages.${system}.check = runner;
  apps.${system}.check = {
    type = "app";
    program = "${runner}/bin/project-check";
  };
}
```

`./scripts/check.sh` 是待打包项目内的示例路径，应替换为该项目的实际脚本。`${./scripts/check.sh}` 在 Nix 求值时将脚本放入 store，入口通过固定的 store 路径调用它；运行时从哪个目录启动都能找到脚本。

脚本还需要相邻资源时，将这些资源一并打包并传入其 store 路径。待构建的项目、输入文件和可写输出目录通过参数传入；不要向只读 store 写入构建结果。开发 shell 中的变量不会自动传给应用输出，入口所需的默认变量应在 wrapper 中声明，外部配置继续在运行时传入。

## 管理锁文件和源码

```bash
nix flake lock                    # 创建锁文件或补新增输入
nix flake update nixpkgs          # 升级指定输入
git diff -- flake.nix flake.lock
```

`nix flake lock` 保留仍有效的已有锁定；修改输入定义可能使相关条目重算。升级已有依赖时只更新任务涉及的输入，并检查相关差异。新建项目将 `flake.nix`、`flake.lock` 和引用文件一起交付。

Git flake 的源码来自已跟踪文件。工作目录中存在文件却在 store 源码内找不到时，用 `git status --short`、`git ls-files -- 路径` 和 `git check-ignore -- 路径` 检查。将本次新增源码加入索引即可参与本地 flake 求值，无需提前提交；保留用户原有暂存内容。只需验证未跟踪文件时可显式用 `path:.`，使用前检查是否会带入无关或敏感文件。

## 验证结果

先用 `nix flake check --no-build` 检查求值，再按改动运行实际项目命令。修改开发环境就执行相关构建或测试；添加应用入口就验证 `nix run` 的参数和工作目录；修改包定义则用 `nix build` 验证沙箱构建。按实际验证的平台报告结果。

CI 复用相同 lock 和项目命令。项目已有 direnv 时可沿用 `use flake`，在授权执行 `.envrc` 前阅读其内容。

官方文档：[开发环境](https://nix.dev/manual/nix/2.34/command-ref/new-cli/nix3-develop)、[锁文件更新](https://nix.dev/manual/nix/2.34/command-ref/new-cli/nix3-flake-update)、[Flake 源码规则](https://nix.dev/manual/nix/2.34/command-ref/new-cli/nix3-flake)。
