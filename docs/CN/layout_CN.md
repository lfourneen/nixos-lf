# 源码目录

[English](../layout.md) | 简体中文 | [文档目录](README_CN.md)

`system/` 负责系统服务和策略，`home/` 里的 Home Manager 模块管桌面和用户应用。每个模块目录都用 `default.nix` 来汇总导入，具体的软件包、服务定义和配置项写在被导入的子模块里；overlay 入口则直接导出软件包覆盖函数。

## 系统与桌面职责

| 目录 | 职责 |
|---|---|
| [`system/config/network/`](../../system/config/network) | 网络声明与配套运行脚本，以 `default.nix` 为入口 |
| [`system/programs/clash/`](../../system/programs/clash) | 系统级 Clash Verge 打包、GUI 启动集成、特权 helper 和 Mihomo 构建 |
| [`system/programs/gost/`](../../system/programs/gost) | Gost 打包、HTTP 补丁与 relay 服务 |
| [`system/programs/hermes/`](../../system/programs/hermes) | Agent 配置、`hermes-skills/`、邮件 CLI 与受限 rootless Docker 集成 |
| [`home/config/clash/`](../../home/config/clash) | Home Manager 写入用户目录的配置及英文状态渲染器 |
| [`home/programs/brush.nix`](../../home/programs/brush.nix) | 桌面用户的交互式 Brush 命令和环境变量 |
| [`home/programs/firefox/`](../../home/programs/firefox) | 受管浏览器偏好、启动器和桌面沙箱 |
| [`system/programs/systemd/`](../../system/programs/systemd) | journald 与 coredump 全局设置 |
| [`system/hardware/`](../../system/hardware) | 设备声明及配套的启动、CPU 和 NVIDIA 服务设置 |

`home/config/clash/status.py` 从 `system/config/network/proxy/state.py` 读取共用的就绪状态契约。`diagnostics` 模块提供 `proxy-status`，交互式 Shell 和自动化脚本都能直接调用。特权配置导入和快照授权由系统侧的 Clash 服务负责。

## 网络配置

`system/config/network/default.nix` 负责把整套网络功能组装起来。宿主和访客的访问控制选项写在 `options.nix`，受管代理选项在 `proxy/options.nix`，而机器相关的身份、网卡名、UID 和端口都统一取自 `system/config/machine.nix`。

| 子目录 | 内容 |
|---|---|
| `dns/` | 上游协调、租约与额度、DNSSEC 探针、黑名单、NetworkManager/resolved 接入 |
| `proxy/` | 根策略、A/B/C 协调器、健康与共用就绪、监听检查、TUN 观测、固定核心命名空间/veth 和 localhost broker |
| `firewall/` | 启动保护、有序规则验证、告警、恢复和直连权限检查 |
| `guests/` | 转发访客与宿主 socket 访客策略 |
| `identity/` | 链路监控、profile 身份准备及恢复 |
| `tailscale/` | 传输源码约定 |
| `diagnostics/` | 状态/诊断命令接入与被动资源采集 |
| `maintenance/` | 所选源码清单与组件检查计划 |
| `lib/` | 共用地址类别 |

## Clash 与 Gost 构建输入

在 Clash 目录中，`gui/` 放系统包的 GUI 启动脚本与默认值处理，`service/` 放 helper 补丁、服务身份、核心启动器、授权快照工具和准备检查，`mihomo/` 则按 `patches/`、`guard/`、`config/`、`controller/` 和 `tests/` 分类存放构建载荷，由软件包 fileset 收集后交给构建期 patcher。

Gost 在自己的程序目录内按 `patches/`、`src/` 和 `tests/` 组织代码，由 [package.nix](../../system/programs/gost/package.nix) 构建定制版 Gost，并由 [relay.nix](../../system/programs/gost/relay.nix) 定义 relay 服务。源码入口和对应的验证命令见[维护手册](maintenance_CN.md)。

## 测试

| 目录 | 范围 |
|---|---|
| [`tests/network/`](../../tests/network) | 代理、DNS、防火墙、访客、身份、Tailscale、时间、集成、单元和维护检查 |
| [`tests/home/clash/`](../../tests/home/clash) | GUI 默认值及进程处理 |
| [`tests/home/firefox/`](../../tests/home/firefox) | 浏览器策略、运行时、沙箱和 ECH fixture |
| [`tests/deployment/`](../../tests/deployment) | 部署复制检查 |

[tests/checks.nix](../../tests/checks.nix) 把这些检查接进了 `checks.x86_64-linux.*`。具体该跑哪些检查、怎么跑，参考[维护手册](maintenance_CN.md#验证)。
