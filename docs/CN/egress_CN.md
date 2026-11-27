# 直连例外与收紧策略

[English](../egress.md) | 简体中文 | [文档索引](README_CN.md)

在 **B** 的 `rules` 子模式下，系统除了强制流量走受管代理路径外，还允许下面清单里的例外，核心本身也会按订阅 `Rule` 决定走 `DIRECT` 还是代理节点。**A** 依然是主动选择的直连，而 **C** 会直接切断普通应用出站和已解密的 tailnet 入站（包括远控和已经建立的连接），不会继承 **B** 的任何可选直连授权。

严格浏览器的网络命名空间里干脆没有宿主 IP 路径，只能通过专用 Unix relay 接入 `lf-strict`。**A** 和 **C** 会在原子策略事务里直接拒绝核心 `7898` 端口的收发，只有 **B** 才放行。在转发第一批浏览器字节之前，relay 会先核对已建立服务端 socket 的核心 UID，避免只看 listener 带来的竞态；到核心里之后，实际叶节点检查还会再拒绝 `DIRECT` 和私网目标。这套边界只管受管浏览器入口，普通桌面和其他应用按下面清单执行。

## Proxy-only 出口

下面那张表讲的是 `rules` 模式下的例外。开启运行时 `proxy-only` 之后，系统会在这些例外之前额外加一道宿主边界来锁死物理出口：外层传输只放行所选核心节点的固定 TCP/UDP 元组；如果配了 WireGuard 出口，物理白名单就只固定对端的 UDP 端点，relay 元组只允许从隧道里走。普通原生应用、root 服务和受限 socket 身份全都没有独立的公网、LAN、组播、Chrony、Tailscale 或入站回包权限——不光新请求，连既有的直连回包也会一并切断，只留下 loopback IPC、限定的 DHCP/NDP，以及授权外层传输所需的网络错误报文。

配置好的 IPv4 透明访客还可以走它们声明的核心入口，但其他经宿主机路由的访客流量（包括公网 IPv6 和私网转发）在严格模式下都会被拒绝。注意这管的是宿主路由层面的流量，同一网桥上的二层通信仍是独立边界。受限后端也依然需要真实地址 DNS，不能借用宿主显式代理端口或 fake-IP 目标。

如果配了 [WireGuard 出口](proxy_CN.md#包含真实-ping-的-wireguard-出口)，公网 IPv4 和 IPv6 的 echo 请求会和固定 relay 共用同一个对端；fake-IP echo 会被拒绝，普通应用发 echo 必须持有有效的内核租约。而在普通应用代理传输下，`proxy-only` 会直接禁止 echo；无论哪种传输，健康检查都不会持续探测 UDP 数据面。

整个切换过程都会经过 **C**，必须消费一份绑定当前 epoch 的新鲜完整证明才能续上内核授权。显式执行 `clash-off` 可以切回 **A**，退回 `rules` 同样走这套受管事务。严格入口和出口的拒绝计数都能在 `proxy-inspect --drops` 里看到。

## 例外清单

UID、接口和端口都取自 [machine.nix](../../system/config/machine.nix)、服务账户以及 [options.nix](../../system/config/network/options.nix)。下表只描述宿主 `output` 方向；访客转发和宿主 `input` 另有独立策略。

| 主体 / 路径 | 目标和传输条件 | 适用模式与限制 |
|---|---|---|
| 隔离代理核心 | 固定私有 veth/源地址，公网 TCP/UDP 或已授权本地目标。宿主清除 packet mark 并核对回包归属 | **B** 及 **C** 恢复。危险目标、宿主保留服务和外部 53 端口受限。网络能力仅作用于核心命名空间 |
| Tailscale | root UDP socket，两个 mark **同时**为上游 `LinuxBypassMarkNum`（`0x80000`），物理 `ens1`/`wlo1`。peer/STUN 地址和端口动态 | **B** 保留打洞，**C** 阻断。正常系统构建检查[源码约定](../../system/config/network/tailscale/transport.nix)，不能对抗 root 冒用 |
| 应用 Unbound（983） | 声明的字面 IPv4/IPv6 上游，TCP 853 | 仅 **A**。进入 **B** 时内核切断此解析器的流量和 loopback 回包 |
| 基础设施 Unbound（979） | 同一组声明的 DoT 上游，TCP 853 | **A** 和 Rules **B** 可用于来源下载及受限 socket 后端。通过 `unboundDotInProxy=false` 收回 Rules 中的例外 |
| Chrony（61） | 物理上行，UDP 123、TCP 4460 | **A**/**B**，**C** 阻断。默认兼容地址变化。可用 `timeServerCidrs` 在 **A**/**B** 的 TUN/NAT 之前限制原始目标，须包含 NTS-KE 和协商出的 NTP 服务器 |
| dnsmasq（985）分流 DNS | 已安装、有到期时间的接口 + 解析器地址租约，TCP/UDP 53 | 更早的 DNS 守卫强制检查租约。受限 **B** 保留，**C** 阻断外部查询 |
| 受管 DNS 后续路径 | 普通物理上行 TCP/UDP 53，MagicDNS 除外 | 继续进入现有 DNS 重定向，不授予任意直连 DNS。更早的租约与内部端口守卫仍有效 |
| MagicDNS 主体 | root、dnsmasq、systemd-resolved（153），`tailscale0`、`100.100.100.100`，TCP/UDP 53 | 受限 **B** 仅保留这些主体。解析路由使用已认证 tailnet 后缀。**C** 阻断外部查询 |
| DHCP 客户端 | root 或 dhcpcd（986），物理上行，UDP 68→67 / 546→547 | **A**/**B**/**C** 链路维护。没有通用物理口服务器 67→68 例外 |
| 访客网关回包 | 声明的访客桥、网关源地址、访客子网。DHCP 67→68、DNS 源端口 53 | **A**/**B**/**C** 的窄基础设施例外。TCP 接受 socket 的属主随服务降权，因此按桥、网关地址和端口匹配 |
| IPv6 维护 | 物理上行，规定的 NDP 类型，hop limit 255 | **A**/**B**/**C**。受限 **B** 另允许 hop limit 1 的链路本地 MLD 报告/离开消息 |
| 私网 / 多播 / 广播 | 私有 IPv4、CGNAT、链路本地、ULA、多播范围 | 兼容 **B** 保留原访问。受限 **B** 使用逐项授权。**A** 直连，**C** 阻断普通流量 |
| Sunshine 媒体 | 桌面 UID、`tailscale0`、声明的串流 UDP **源端口** | 受限 **B** 保留协商后的媒体和已获入站授权连接的回包。**C** 全部切断，管理 UI 仍仅本机 |

Loopback 保留用于本地 IPC，**C** 下还留着[网络架构](network_CN.md)里提到的窄范围核心恢复与自检路径；普通应用不可能借着 loopback 代理监听逃出 **C**。

开启 `localDnssec=true` 后，私有验证器只能查询核心命名空间里的 loopback 传输监听，没有外部 DNS 传输权限。`rules` 模式下，海外应用查询会通过独立的 `lf-dns` 选择组走认证的 Cloudflare 或 Google DoH；运行时 `proxy-only` 则把应用和引导 DoH 一并走固定叶子。**C** 下核心原有的恢复传输依然可用。注意 `unboundDotInProxy` 只管基础设施 Unbound 在 `rules` 里的例外；**C** 会阻断普通宿主 DoT，已登记来源的恢复会使用单独租约授权的解析器，而 root 诊断本身是受信任的。

DNS 传输由 `my.proxy.dnsEncryption` 独立控制。默认允许 **A** 下有租约的明文恢复；设为 `"strict"` 后会收回这项权限，并阻断核心外部 53 端口，只保留指定的 tailnet MagicDNS。已批准的 split DNS 仍受租约约束，原生 MagicDNS 保留条件路由，细节见 [DNS](dns_CN.md#可选严格加密)。

## 可选的受限 B

默认 `my.hardening.directEgress.mode="compatibility"` 会保留局域网访问兼容性。如果想收紧原生宿主 socket，可以切到 `"restricted"`，只按需放行必要服务：

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

每条规则都必须写明精确的出口接口、规范目标 CIDR、明确的 UID 列表（写 `uids=[]` 表示该条目不限 socket 属主）以及目标端口。正常系统构建会直接拒绝带主机位的网段、公网目标或没填端口的配置；另外，允许主动出站并不会自动开放入站服务。

这条守卫在 `output -195`、透明重定向之前检查原始目标。哪怕旧配置里写着 `allowPrivateEgress=false`，受限模式下私网路由也依然留在 TUN/NAT 之外，由前置守卫直接授权，被拒绝的 socket 没法再绕进代理。撤销某项授权会立刻切断宿主主动发起的已有连接，只有已经获准入站连接的 **reply 方向** 会自动保留；而兼容模式下如果设了 `allowPrivateEgress=false`，私网目标仍会继续走代理路由。

注意这里管的是原生宿主 socket。核心内部的[本地与远程内部目标](proxy_CN.md#域名目标)是通过 `localTargetCidrs` 和 `remoteTargetGrants` 单独授权的，原生受限授权既不限制也不会授予核心 socket 权限，访客转发和入站授权同样各自独立。

## 访客信任范围

经宿主转发的访客网络使用 `guestNetworks` 和 `guestPrivateExceptions`。libvirt、Waydroid 以及启用的热点默认都限制私网转发；把开发网桥设为可信可以放开私网转发，但不会改变宿主服务或公网模式策略，而且当前授权会在通用 established 放行前同时约束双向流量，详见[访客架构](network_CN.md#访客与容器)。

`socketGuestUsers` 会在 **A**、**B**、**C** 三个模式下为独立的宿主 socket 后端及其委派 UID 安装单独的原始目标守卫。因为它在宿主兼容例外、TUN 和 NAT 之前执行，桌面原本宽泛的 LAN 权限不会顺带授给受限后端。私网放行使用 `socketGuestPrivateExceptions`，它属于额外边界，不能豁免另行启用的宿主受限策略；53 端口也会强制走过滤后的真实地址解析路径。

回环代理、合成地址和已知翻译地址都不能拿来授权，保留端口在宿主地址和核心内部链路上也会继续受保护，不过如果远端私网服务正好用同一端口，仍可以通过显式例外放行。逐账户的 conntrack 身份会在撤销授权以及进入 **C** 时切断回包，因此撤销授权期间要先把账户身份声明留着。普通 loopback IPC 和桌面所属后端仍然保留宿主信任级别。

## 可选的时间服务器目标限制

保持 `timeServerCidrs=[]` 时，系统只按 UID、物理口和端口约束 Chrony，能兼容 NTS 服务器地址变化。如果填上规范的 IPv4/IPv6 CIDR，就会在 **A 和 B** 的透明代理之前把 Chrony 原始的 NTP/NTS-KE 目标锁死在清单里——记得填实际服务商的端点（文档里的示例地址不能用），而且要同时包含 NTS-KE 端点和密钥交换返回的那台 NTP 服务器。一旦地址对不上就会直接拒绝，不会自动扩大白名单，也不会退回明文时间协议。选项用法可以参考 [Chrony server 选项](https://chrony-project.org/doc/4.8/chrony.conf.html)。

受管 DNS 会为配置好的 NTS-KE 主机名返回真实地址，Chrony 才能通过物理上行连过去。如果密钥交换返回了另一个主机名，把它加进 `timeServerNames`；同时启用 `timeServerCidrs` 的话，也别忘了把那台服务器的地址加进允许的 CIDR。Gost 的 TCP 后备同样保留了限定后的 NTS-KE 例外：合法目标按配置选路，走直连例外还额外要求从物理上行口出去。在 **B** 下关闭宿主 Unbound DoT 不会影响独立的 **A** 恢复策略和 **B** 受管 DNS。

## 验证与边界

核心只在宿主授权的传输范围内负责应用路由，它的网络能力也仅限于私有命名空间内部；宿主防火墙由 root 服务管理，root 依然属于可信边界。

手机配对、画面捕获和编码依赖部署后的图形会话，配置方法见[远程桌面](operations_CN.md#远程桌面)；改动出站策略后需要跑的检查则列在[维护手册](maintenance_CN.md#验证)。
