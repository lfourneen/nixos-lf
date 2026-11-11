# 网络文档

[English](../README.md) | 简体中文 | [项目首页](../../README_CN.md)

本配置将物理上行、本地 DNS、受管代理核心、访客网络和原生 Firefox 组合起来。网络模式决定应用可使用的路径；浏览器隐私和代理路由是独立选项。

| 手册 | 内容 |
|---|---|
| [网络架构](network_CN.md) | 流量路径、A/B/C 模式、防火墙、上行、访客与 Tailscale |
| [链路身份](identity_CN.md) | MAC 默认值、按 profile 身份、DHCP/IPv6 协同迁移、有线策略与持久状态 |
| [直连例外](egress_CN.md) | 例外清单、可选受限 B 授权、时间服务器目标限制与 C 阻断 |
| [DNS](dns_CN.md) | 各路径责任、共享域名过滤、DNSSEC、可选严格加密、恢复与分流 DNS |
| [代理核心与权限](proxy_CN.md) | 配置授权、服务身份、控制 API 与路由策略 |
| [Firefox](browser_CN.md) | 受管启动器、外层沙箱、网络路径、DNS、ECH 与隐私设置 |
| [操作与诊断](operations_CN.md) | 模式切换、状态、恢复、配置入口与检查 |
| [网络补丁与自定义维护](maintenance_CN.md) | 自定义清单、构建接入、源码复核门和组件回归计划 |

首次阅读从网络架构开始；日常管理查操作与诊断，更新依赖前查维护手册。

默认保留开机直连 A、用户主动开启代理 B、代理失败阻断 C，以及订阅 Rule 分流和宿主 LAN 访问。经宿主转发的访客默认限制私网访问；独立 socket 访客账户另有专门策略。`proxyOnly` 可要求核心内的应用流量使用代理；`bootMode` 独立决定开机直连还是先阻断。

文档描述本仓库的声明配置。示例接口、UID 和端口均为默认值，单点定义在 [machine.nix](../../system/config/machine.nix)。`docs/` 保留在仓库中，[push-to-dir.sh](../../scripts/push-to-dir.sh) 生成 `/etc/nixos` 部署目录时不复制它。
