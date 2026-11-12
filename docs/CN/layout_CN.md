# 源码目录

[English](../layout.md) | 简体中文 | [文档目录](README_CN.md)

## 系统与桌面职责

| 目录 | 职责 |
|---|---|
| [`system/config/network/`](../../system/config/network) | 网络声明与配套运行脚本，以 `default.nix` 为入口 |
| [`system/programs/clash/`](../../system/programs/clash) | 系统级 Clash Verge 打包、GUI 启动集成、特权 helper 和 Mihomo 构建 |
| [`system/programs/gost/`](../../system/programs/gost) | Gost 打包、HTTP 补丁与 relay 服务 |
| [`home/config/clash/`](../../home/config/clash) | Home Manager 写入用户目录的配置及英文状态渲染器 |
| [`home/programs/firefox/`](../../home/programs/firefox) | 受管浏览器偏好、启动器和桌面沙箱 |
| [`system/programs/systemd/`](../../system/programs/systemd) | journald 与 coredump 全局设置 |
| [`system/hardware/`](../../system/hardware) | 设备声明及配套的启动、CPU 和 NVIDIA 服务设置 |

`home/config/clash/status.py` 从 `system/config/network/proxy/state.py` 读取共用就绪契约；diagnostics 模块为交互 shell 和自动化调用者提供命令入口，Nushell 转发 `proxy-status` 参数。特权配置导入与快照授权由系统侧 Clash 服务负责。

## 网络配置

`system/config/network/default.nix` 组合网络功能；`options.nix` 定义宿主及访客访问选项，`proxy/options.nix` 定义受管代理选项。机器身份、接口、UID 和端口统一来自 `system/config/machine.nix`。

| 子目录 | 内容 |
|---|---|
| `dns/` | 上游协调、租约与额度、DNSSEC 探针、黑名单、NetworkManager/resolved 接入 |
| `proxy/` | 根策略、A/B/C 协调器、健康与共用就绪、监听检查、TUN 观测 |
| `firewall/` | 启动保护、有序规则验证、告警、恢复和直连权限检查 |
| `guests/` | 转发访客与宿主 socket 访客策略 |
| `identity/` | 链路监控、profile 身份准备及恢复 |
| `tailscale/` | 传输源码约定 |
| `diagnostics/` | 状态/诊断命令接入与被动资源采集 |
| `maintenance/` | 所选源码清单与组件检查计划 |
| `lib/` | 共用地址类别 |

## Clash 与 Gost 构建输入

Clash 的 `gui/` 保存系统包的 GUI 启动与默认值处理；`service/` 保存 helper 补丁、服务身份、核心启动器、授权快照和准备检查。`mihomo/` 按 `patches/`、`guard/`、`config/`、`controller/` 和 `tests/` 组织构建载荷，由软件包 fileset 收集并交给构建期 patcher。

Gost 在自己的程序目录内使用 `patches/`、`src/` 和 `tests/`，默认模块连接软件包与 relay 服务。源码入口及验证命令见[维护手册](maintenance_CN.md)。

## 测试

| 目录 | 范围 |
|---|---|
| [`tests/network/`](../../tests/network) | 代理、DNS、防火墙、访客、身份、Tailscale、时间、集成、单元和维护检查 |
| [`tests/home/clash/`](../../tests/home/clash) | GUI 默认值及进程处理 |
| [`tests/home/firefox/`](../../tests/home/firefox) | 浏览器策略、运行时、沙箱和 ECH fixture |
| [`tests/deployment/`](../../tests/deployment) | 部署复制检查 |

`flake.nix` 将这些源码接入 `checks.x86_64-linux.*`。脚本作为独立 store 文件运行时，由 Nix 测试显式传入源码路径；本地 Python 测试从所属功能目录定位仓库。
