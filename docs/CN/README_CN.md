# 网络文档

[English](../README.md) | 简体中文 | [项目首页](../../README_CN.md)

这一组文档主要说明这套工作站网络栈的设计与用法，包括流量路由、DNS、透明代理、Firefox 隔离和访客网络。

| 手册 | 内容 |
|---|---|
| [源码目录](layout_CN.md) | 系统与桌面职责、网络模块、构建载荷及测试目录 |
| [网络架构](network_CN.md) | 流量路径、A/B/C 模式、防火墙、上行、访客与 Tailscale |
| [链路身份](identity_CN.md) | MAC 默认值、按 profile 身份、DHCP/IPv6 协同迁移、有线策略与持久状态 |
| [直连例外](egress_CN.md) | 例外清单、可选受限 B 授权、时间服务器目标限制与 C 阻断 |
| [DNS](dns_CN.md) | 各路径责任、共享域名过滤、DNSSEC、可选严格加密、恢复与分流 DNS |
| [代理核心与权限](proxy_CN.md) | 配置授权、服务身份、控制 API 与路由策略 |
| [Firefox](browser_CN.md) | 受管启动器、外层沙箱、网络路径、DNS、ECH 与隐私设置 |
| [操作与诊断](operations_CN.md) | 模式切换、状态、恢复、配置入口与检查 |
| [网络补丁与自定义维护](maintenance_CN.md) | 自定义清单、构建接入、源码兼容性检查和组件测试选择 |

初次阅读建议先看[网络架构](network_CN.md)，把流量路径和 A/B/C 三个模式搞清楚；日常操作和排障查[操作与诊断](operations_CN.md)；准备升级依赖或动本地补丁时再对照[维护手册](maintenance_CN.md)。

默认策略下，系统开机进入直连模式 **A**。请求开启代理后，核心就绪就进入 **B**，不可用就停在 **C**。核心内部的路由策略由 `proxyOnly` 控制，开机默认模式由 `bootMode` 控制，具体选项都在对应手册里有说明；网卡名、服务 UID 和端口则统一写在 [machine.nix](../../system/config/machine.nix) 里。
