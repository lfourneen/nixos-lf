# Overlay 与桌面软件包维护

[English](maintenance.md) | 简体中文

本地 overlay 在 [default.nix](default.nix) 注册。AppImage 和便携包在各自的包表达式中
固定上游 release 资产及 hash；其它包跟随 [flake.lock](../flake.lock) 中所属的 input。

| 入口 | 自定义与维护 |
| --- | --- |
| [default.nix](default.nix) | Niri SHM screencast 固定 hash diff，此 override 禁用包检查；移除前比较上游并测试录屏 |
| [dsh-desktop-appimage.nix](dsh-desktop-appimage.nix) | ASAR 菜单、内置 pnpm git 安装、解包/执行位和 Landlock 启动器；逐版本检查 JS 锚点、pnpm、sharp WASM 与可执行资源 |
| [dsh-tui.nix](dsh-tui/dsh-tui.nix) | 预编译 release 提取、原生 DSH 依赖链接、持久化插件 profile 与 FHS 启动器；检查 pkg 资产布局、宿主 peer 范围、共享 Cordis 实例和插件持久性，详见[打包与插件指南](dsh-tui/README.md) |
| [bilibili-appimage.nix](bilibili-appimage.nix) | 桌面命令替换与 Wayland 启动器；核对 desktop 布局和沙箱参数 |
| [wemeet-cursor-hook.nix](wemeet-cursor-hook.nix) | 固定外部 cursor hook，源码构建；合成器更新后检查 preload/光标 |

相关的 Rime 包 override 位于 [layout.nix](../system/config/layout.nix)：将 Rime Ice
suggestion YAML 安装为默认，升级时核对上游数据文件名。

更新 release 时，除版本与 hash 外，还要比较资产布局和本地补丁。保留精确锚点失败，
使上游代码变化必须经过复核。先构建受影响的包，再验证其自定义行为，例如桌面启动、
录屏、输入或插件安装与持久化；包含原生扩展的包还需检查实际运行时的加载路径。
