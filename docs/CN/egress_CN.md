# 直连例外与收紧策略

[English](../egress.md) | 简体中文 | [文档索引](README_CN.md)

B 的 rules 子模式允许受管路径和下表的例外，核心按订阅 Rule 选择 DIRECT 或代理节点。
A 继续提供明确选择的直连。
C 阻断普通应用出口及已解密 tailnet 入站，包括远控和已有连接，不继承 B 的可选直连授权。

严格浏览器的网络命名空间没有宿主 IP 路径，仅通过专用 Unix relay 进入 `lf-strict`。
A/C 在原子规则事务中拒绝核心 `7898` 的收发，B 才开放。
转发首个浏览器请求前核对已建立服务端 socket 的核心 UID，避免只查询 listener 的竞态。
实际叶子拒绝 DIRECT 与私网目标。
此约束适用于受管浏览器入口，普通桌面及其它应用保持下述规则。

## Proxy-only 出口

下方清单描述 rules 中的例外。运行时 proxy-only 增加独立宿主边界，先于这些例外限制物理出口。
外层传输只允许所选核心节点的固定 TCP/UDP 端点。配置 WireGuard 出口后，物理出口只放行固定的对端 UDP 端点，relay 端点只允许经隧道访问。
普通原生应用、root 服务和受限 socket 身份均没有独立公网、LAN、组播、Chrony、Tailscale 或入站回复权限。
既有直连回包与新请求同时被撤销。Loopback IPC、限定的 DHCP/NDP，以及授权外层传输所需的网络错误继续可用。

已配置的 IPv4 透明访客可以使用声明的核心入口，其余经宿主路由的访客流量，包括公网 IPv6 和私网转发，在严格模式拒绝。
此约束作用于宿主路由，同桥二层流量仍属于独立边界。
受限后端继续要求真实地址 DNS，不得借用宿主显式代理端口或 fake-IP 目标。
配置 [WireGuard 出口](proxy_CN.md#包含真实-ping-的-wireguard-出口)后，公网 IPv4 和 IPv6 echo 请求与固定 relay 使用同一对端。Fake-IP echo 会被拒绝，普通应用的 echo 需要有效的内核租约。应用代理传输下的 proxy-only 拒绝 echo。健康检查仍不持续探测 UDP 数据面。

切换全程经过 C，只有绑定当前 epoch 的新鲜完整证明才能续内核授权。
显式 `clash-off` 恢复 A，返回 rules 也走同一受管事务。
`proxy-inspect --drops` 包含严格入口和出口的拒绝计数。

## 例外清单

UID、接口和端口来自 [machine.nix](../../system/config/machine.nix)、服务账户及 [options.nix](../../system/config/network/options.nix)。
下表描述宿主 output。
访客转发和宿主入站单独授权。

| 主体 / 路径 | 目标和传输条件 | 适用模式与限制 |
|---|---|---|
| 隔离代理核心 | 固定私有 veth/源地址，公网 TCP/UDP 或已授权本地目标。宿主清除 packet mark 并核对回包归属 | B 及 C 恢复。危险目标、宿主保留服务和外部 53 端口受限。网络能力仅作用于核心命名空间 |
| Tailscale | root UDP socket，两个 mark **同时**为上游 `LinuxBypassMarkNum`（`0x80000`），物理 `ens1`/`wlo1`。peer/STUN 地址和端口动态 | B 保留打洞，C 阻断。正常系统构建检查[源码约定](../../system/config/network/tailscale/transport.nix)，不能对抗 root 冒用 |
| 应用 Unbound（983） | 声明的字面 IPv4/IPv6 上游，TCP 853 | 仅 A。进入 B 时内核切断此解析器的流量和 loopback 回包 |
| 基础设施 Unbound（979） | 同一组声明的 DoT 上游，TCP 853 | A 和 Rules B 可用于来源下载及受限 socket 后端。通过 `unboundDotInProxy=false` 收回 Rules 中的例外 |
| Chrony（61） | 物理上行，UDP 123、TCP 4460 | A/B，C 阻断。默认兼容地址变化。可用 `timeServerCidrs` 在 A/B 的 TUN/NAT 之前限制原始目标，须包含 NTS-KE 和协商出的 NTP 服务器 |
| dnsmasq（985）分流 DNS | 已安装、有到期时间的接口 + 解析器地址租约，TCP/UDP 53 | 更早的 DNS 守卫强制检查租约。受限 B 保留，C 阻断外部查询 |
| 受管 DNS 后续路径 | 普通物理上行 TCP/UDP 53，MagicDNS 除外 | 继续进入现有 DNS 重定向，不授予任意直连 DNS。更早的租约与内部端口守卫仍有效 |
| MagicDNS 主体 | root、dnsmasq、systemd-resolved（153），`tailscale0`、`100.100.100.100`、TCP/UDP 53 | 受限 B 仅保留这些主体。解析路由使用已认证 tailnet 后缀。C 阻断外部查询 |
| DHCP 客户端 | root 或 dhcpcd（986），物理上行，UDP 68→67 / 546→547 | A/B/C 链路维护。没有通用物理口服务器 67→68 例外 |
| 访客网关回包 | 声明的访客桥、网关源地址、访客子网。DHCP 67→68、DNS 源端口 53 | A/B/C 的窄基础设施例外。TCP 接受 socket 的属主随服务降权，因此按桥、网关地址和端口匹配 |
| IPv6 维护 | 物理上行，规定的 NDP 类型，hop limit 255 | A/B/C。受限 B 另允许 hop limit 1 的链路本地 MLD 报告/离开消息 |
| 私网 / 多播 / 广播 | 私有 IPv4、CGNAT、链路本地、ULA、多播范围 | 兼容 B 保留原访问。受限 B 使用逐项授权。A 直连，C 阻断普通流量 |
| Sunshine 媒体 | 桌面 UID、`tailscale0`、声明的串流 UDP **源端口** | 受限 B 保留协商后的媒体和已获入站授权连接的回包。C 全部切断，管理 UI 仍仅本机 |

loopback 保留本地 IPC。
C 还保留[网络架构](network_CN.md)说明的窄核心恢复与自检路径。
普通应用不能借 loopback 代理监听逃出 C。

启用 `localDnssec=true` 时，私有验证器只能查询核心命名空间中的 loopback 传输监听，没有外部 DNS 传输权限。
Rules 模式下，海外应用查询通过独立的 `lf-dns` 选择组发往认证的 Cloudflare 或 Google DoH。运行时 `proxy-only` 通过固定叶子传输应用和引导 DoH。
C 中继续保留核心原有的恢复传输。
`unboundDotInProxy` 仅控制基础设施 Unbound 在 Rules 中的例外。C 阻断普通宿主 DoT，已登记来源的恢复使用单独租约授权的解析器。root 诊断受信任。

DNS 传输由 `my.proxy.dnsEncryption` 独立控制。
默认允许 A 中有租约的明文恢复。
`"strict"` 移除此权限，并阻断核心外部 53，仅保留指定 tailnet MagicDNS。
已批准 split DNS 继续受租约约束，原生 MagicDNS 保留条件路由，见 [DNS](dns_CN.md#可选严格加密)。

## 可选的受限 B

默认 `my.hardening.directEgress.mode="compatibility"` 保留 LAN 兼容性。

需要限制原生宿主 socket 时，选择 `"restricted"` 并只授权必要服务：

```nix
my.hardening.directEgress = {
  mode = "restricted";
  unboundDotInProxy = false;
  exceptions = [
    {
      interface = "ens1";
      cidr = "192.168.1.20/32";
      uids = [ 1000 ];
      tcpPorts = [ 443 ];
    }
    {
      interface = "ens1";
      cidr = "224.0.0.251/32";
      uids = [ 1000 ];
      udpPorts = [ 5353 ];
    }
  ];
};
```

每项须指定精确出口接口、规范目标 CIDR、明确 UID 列表和目标端口。
显式 `uids=[]` 允许该项的任意 socket 属主。
正常系统构建拒绝带主机位的网段、公网目标和没有端口的配置。
允许发起连接不会自动开放入站服务。

B 守卫在 output `-195`、透明重定向之前检查原始目标。
即使旧 `allowPrivateEgress=false`，受限模式仍让私网留在 TUN/NAT 外，由前置守卫授权，被拒绝的 socket 不能再绕入代理。
撤销授权也切断已有宿主主动连接，自动保留的只有已获入站授权连接的 **reply 方向**。
兼容模式的旧 `allowPrivateEgress=false` 则继续让私网目标接受代理路由。

此处约束原生宿主 socket。
核心通过 `localTargetCidrs` 和 `remoteTargetGrants` 单独授权[本地与远程内部目标](proxy_CN.md#域名目标)，原生受限授权不约束也不授予这些核心 socket 的权限。
访客转发和入站授权仍独立。

## 访客信任范围

经宿主转发的访客使用 `guestNetworks` 与 `guestPrivateExceptions`。
libvirt、Waydroid 及启用的热点默认限制私网转发。
可信开发角色允许私网转发，但不改变宿主服务或公网模式策略。
当前授权在通用 established 放行前约束双向流量。
见[访客架构](network_CN.md#访客与容器)。

`socketGuestUsers` 为独立宿主 socket 后端及其委派 UID 在 A/B/C 安装单独的原始目标守卫。
它先于宿主兼容例外、TUN 与 NAT，因此桌面的宽泛 LAN 权限不能授权受限后端。
私网使用 `socketGuestPrivateExceptions`。
它是额外约束，不豁免单独启用的宿主受限策略。

53 端口强制经过过滤的真实地址解析。
loopback 代理、合成/已知转换目标及保留监听端口不可授权。
逐账户 conntrack 身份在撤销授权和 C 中切断回包。
撤销期间需要保持身份声明。
普通 loopback IPC 与桌面所属后端仍保留宿主信任级别。

## 可选的时间服务器目标限制

`timeServerCidrs=[]` 保持 UID/物理口/端口约束，兼容 NTS 服务器地址变化。
填写规范 IPv4/IPv6 CIDR 后，会在 **A 和 B** 的透明代理之前限制 Chrony 原始 NTP/NTS-KE 目标。
应按实际提供商端点填写，文档示例地址不能作为服务器。
目标 CIDR 需同时包含 NTS-KE 端点和密钥交换返回的 NTP 服务器。

参见 [Chrony server 选项](https://chrony-project.org/doc/4.8/chrony.conf.html)。
地址变化时拒绝连接，不自动扩大白名单，也不退回明文时间协议。

启用此限制时，受管 DNS 将声明的 NTS-KE 名排除出 fake-IP，让目标检查看到真实地址。
如果 KE 协商出另一个主机名，还须将它加入 `timeServerNames`，同时限定其地址 CIDR。
补充域名不扩大地址授权。
Gost 的 TCP 后备保留限定后的 NTS-KE 例外。
合法目标按配置选路，直连例外还要求物理上行口。
在 B 关闭宿主 Unbound DoT 不影响独立的 A 恢复策略和 B 受管 DNS。

## 验证与边界

核心在宿主授权的传输范围内负责应用路由，其网络能力仅作用于私有命名空间。
宿主防火墙由 root 服务管理，root 仍属于可信身份。

手机配对、画面捕获和编码依赖部署后的图形会话，配置方法见[远程桌面](operations_CN.md#远程桌面)。
修改出站策略所需的检查见[维护手册](maintenance_CN.md#验证)。
