# dsh-TUI（Nix 二进制打包）

`dsh-tui.nix` 提供 `pkgs.dsh-tui`，已通过 `overlays/default.nix` 注册并加入
`system/systempkgs/tools.nix`。启动命令是 `dsh-tui`，也可用短命令 `dst`。

## TUI 和 DSH 的来源

- TUI：GitHub `v0.20.0` Linux standalone release 中已编译的 JavaScript 和原生依赖，
  校验 SHA-256 后解包，不运行 npm install、TypeScript 编译或源码构建。
- DSH：现有 `pkgs.dsh`，来自 flake 锁定的 `llm-agents`，目前为 `0.2.2-rc.3`。
  更新 `llm-agents` 后随系统更新，不在启动时联网追踪 npm latest。
- 便携包自带的 DSH 及其 `@deepseek-ai/*` 依赖被替换为原生 DSH 的链接，TUI 与宿主
  共用 Cordis、Loader 和 DSH 服务。构建时检查宿主 peer 版本范围和 Cordis 实例，
  避免混用两套框架。
- 上游 `v0.20.0` 的验证线仍是 `rc.2`；[本地兼容适配](patch-host-compat.mjs)只为
  `TUI 0.20.0 + DSH 0.2.2-rc.3` 补充精确版本记录。两代宿主中，CLI 与适配器所用
  包的 249 个类型声明文件一致；构建核对这些声明的摘要，再同步 peer 与运行时版本清单。
  [运行时检查](check-runtime.mjs)另加载真实宿主导出、Loader 和 TUI，核对共享 Cordis。
- Node 和 pnpm 由 Nix 提供。DSH `0.2.2` 要求 Node `>=24.9`，FHS 使用 nixpkgs 的
  `nodejs_24`。FHS 环境为运行时安装的插件及原生扩展提供常见 Linux 路径。

启动使用上游支持的 `dsh --profile dsh-tui` 路径，由原生 DSH 的包装器提供其 Loader
所需的 Node 参数，避免 TUI 的直接入口丢失这些参数后再回退启动。

上游 standalone 启动器会在每次运行时重写 profile 包清单和用户补丁，因此这里从
release 提取运行时，使用其已有的普通 TUI 启动器。初始化只建立缺失的用户文件；
Nix 版本变化时只刷新两个内置 bundle 的链接和 `link:` 依赖，保留第三方插件、
bundle 顺序和用户补丁。

## 插件放在哪里

默认 TUI 的 `DSH_HOME` 为 `${XDG_DATA_HOME:-~/.local/share}/dsh-tui`。
在通常未设置 XDG_DATA_HOME 的系统上：

| 内容 | 路径 |
| --- | --- |
| 插件清单、bundle 列表 | `~/.local/share/dsh-tui/profiles/dsh-tui/package.json` |
| 第三方插件及依赖 | `~/.local/share/dsh-tui/profiles/dsh-tui/node_modules/` |
| profile 配置覆盖 | `~/.local/share/dsh-tui/profiles/dsh-tui/cordis.patch.yml` |
| pnpm 配置 | `~/.local/share/dsh-tui/profiles/dsh-tui/pnpm-workspace.yaml` |
| DSH 凭据、会话等 | `~/.local/share/dsh-tui/` 下对应文件及目录 |
| TUI 设置、主题、扩展授权等 | 上游固定的 `~/.dsh-tui/` |

TUI 不继承外部的 `DSH_HOME`，可用 `DSH_TUI_HOME=/absolute/path dsh-tui` 单独指定。
`~/.dsh-tui/` 仍由所有同用户 TUI 实例共享；不要将 `DSH_TUI_HOME` 指向 desktop/web
正在使用的数据目录。已有的非 Nix 管理 `dsh-tui` profile 不会被自动接管。

## 安装、移除与启停插件

进入 TUI 后使用上游插件管理界面：

```text
/plugin install <npm包名>@<版本>
/plugin remove <bundle包名>
/plugin disable <名称或行ID>
/plugin enable <名称或行ID>
```

也可从终端调用同一原生 DSH，命令会继承这里的独立数据目录及 FHS 环境：

```sh
dst dsh --version
dst dsh plugin --profile dsh-tui add <npm包名>@<版本>
dst dsh plugin --profile dsh-tui remove <npm包名>
dst dsh --profile dsh-tui --dump-config
```

优先安装作者发布到 npm 的成品包；也可传本地已构建的 `.tgz`。Git 来源可能执行
`prepare` 并编译源码，与「不源码编译」的使用目标不符。Nix 打包本身不编译 TUI，
第三方插件是否需要编译仍取决于该插件。pnpm 默认不自动安装宿主 peers；构建脚本
是否放行沿用 DSH/pnpm 的提示，不全局允许所有插件脚本。

不要直接执行没有 TUI 入口前缀的 `dsh plugin ...`：它会使用系统 DSH 默认的数据目录。
`dsh-working-activity` 已随 TUI 提供，无需重复安装。安装后按插件说明重启 TUI，
尤其是新增后端需要重启才能出现在选择器中。

## 与 desktop/web 插件的关系

它们共用 DSH/Cordis 的 bundle 机制：通常是带 `package.json`、
`dsh.bundle.patch` 和 `cordis.patch.yml` 的 npm 包，不是浏览器扩展文件。
TUI 专用贡献还可能声明 `dsh-plugin.json` 和 `tui.dsh/*` 协议。

| 插件类型 | 在 TUI 中的适用性 |
| --- | --- |
| 模型、工具、MCP、技能、会话等宿主能力 | 宿主版本、服务依赖及 preset 作用域匹配时可复用 |
| TUI 状态栏、主题、侧栏、场景、快捷键等 | 需要作者明确适配 TUI 的接口和协议 |
| 依赖 Web DOM、浏览器界面或 Electron API 的 UI | 不能仅因属于 DSH 插件就认为可在 TUI 中运行 |

这里独立保存插件依赖和 profile，安装、卸载或禁用 TUI 插件不会改 desktop/web
的插件清单。独立目录不代表进程沙箱：插件若自行使用固定的全局路径、外部端口或
共享服务，仍需遵循该插件自己的并发与存储约定。FHS 也不保证每个第三方原生插件
都具备其专有的系统依赖。

## 更新

TUI 与原生 DSH 由 Nix 管理，`dsh-tui update` 和程序内 `/update` 不替换它们。
更新 TUI 时修改本目录 `dsh-tui.nix` 的版本及 release hashes；更新宿主时更新
`llm-agents` input，再执行常规 NixOS 构建/切换。第三方插件仍可用 DSH 管理。
若新宿主超出当前 TUI 的 peer 范围，构建会报错。优先选择上游已支持的组合；需要本地
适配时，先比较适配器依赖的接口和行为，再更新精确版本及声明摘要，并验证真实启动。
兼容适配不会自动接受后续 RC，也不改变上游的主验证线或独立入口的宿主副本版本。

上游依据（固定到此次打包版本）：

- [v0.20.0 release](https://github.com/ccch1mneyyy/dsh-TUI/releases/tag/v0.20.0)
- [上游安装与 profile 机制](https://github.com/ccch1mneyyy/dsh-TUI/blob/v0.20.0/docs/getting-started.md)
- [standalone 初始化代码](https://github.com/ccch1mneyyy/dsh-TUI/blob/v0.20.0/standalone/entry.mjs)
- [插件安装与生命周期](https://github.com/ccch1mneyyy/dsh-TUI/blob/v0.20.0/docs/plugins.md)
- [插件形态与适配契约](https://github.com/ccch1mneyyy/dsh-TUI/blob/v0.20.0/tui-profile/docs/plugin-admission-and-development.md)
