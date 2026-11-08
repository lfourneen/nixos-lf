# 直连例外与收紧策略

[English](../egress.md) | 简体中文 | [文档索引](README_CN.md)

B 允许受管路径和下表的例外，订阅 Rule 仍可选择 DIRECT，不能把进入 B 理解为每条连接都经远端代理。A 继续提供明确选择的直连。C 阻断普通应用出口及已解密 tailnet 入站，包括远控和已有连接，不继承 B 的可选直连授权。

## 例外清单

UID、接口和端口来自 [machine.nix](../../system/config/machine.nix)、服务账户及 [hardening-options.nix](../../system/config/hardening-options.nix)。下表描述宿主 output；访客转发和宿主入站单独授权。

| 主体 / 路径 | 目标和传输条件 | 适用模式与限制 |
|---|---|---|
| 代理核心 | socket 属主为 root 或 `clash-core`（982），socket `SO_MARK` 和 packet mark **同时**为 6666；目标由核心选定 | B 及 C 恢复。仅 UID 或仅 packet mark 均不放行；root 和核心 `NET_ADMIN` 仍是信任边界 |
| Tailscale | root UDP socket，两个 mark **同时**为上游 `LinuxBypassMarkNum`（`0x80000`），物理 `ens1`/`wlo1`；peer/STUN 地址和端口动态 | B 保留打洞，C 阻断。正常系统构建检查[源码约定](../../system/config/tailscale-transport.nix)，不能对抗 root 冒用 |
| Unbound（983） | 声明的字面 IPv4 上游，TCP 853 | A/B 均检查目标；B 可用 `unboundDotInProxy=false` 关闭此例外。C 阻断外部 DoT。其它普通 UID 不得使用 TCP/UDP 853；root 诊断仍受信任。443 DoH 属于另一策略 |
| Chrony（61） | 物理上行，UDP 123、TCP 4460 | A/B，C 阻断。默认兼容地址变化；可用 `timeServerCidrs` 在 A/B 的 TUN/NAT 之前限制原始目标，须包含 NTS-KE 和协商出的 NTP 服务器 |
| dnsmasq（985）分流 DNS | 已安装、有到期时间的接口 + 解析器地址租约，TCP/UDP 53 | 更早的 DNS 守卫强制检查租约；受限 B 保留，C 阻断外部查询 |
| 受管 DNS 后续路径 | 普通物理上行 TCP/UDP 53，MagicDNS 除外 | 继续进入现有 DNS 重定向，不授予任意直连 DNS。更早的租约与内部端口守卫仍有效 |
| MagicDNS 主体 | root、dnsmasq、systemd-resolved（153），`tailscale0`、`100.100.100.100`、TCP/UDP 53 | 受限 B 仅保留这些主体；解析路由使用已认证 tailnet 后缀。C 阻断外部查询 |
| DHCP 客户端 | root 或 dhcpcd（986），物理上行，UDP 68→67 / 546→547 | A/B/C 链路维护；没有通用物理口服务器 67→68 例外 |
| 访客网关回包 | 声明的访客桥、网关源地址、访客子网；DHCP 67→68、DNS 源端口 53 | A/B/C 的窄基础设施例外。TCP 接受 socket 可能属于降权后 UID，因此不依赖 daemon UID |
| IPv6 维护 | 物理上行，规定的 NDP 类型，hop limit 255 | A/B/C；受限 B 另允许 hop limit 1 的链路本地 MLD 报告/离开消息 |
| 私网 / 多播 / 广播 | 私有 IPv4、CGNAT、链路本地、ULA、多播范围 | 兼容 B 保留原访问；受限 B 使用逐项授权。A 直连，C 阻断普通流量 |
| Sunshine 媒体 | 桌面 UID、`tailscale0`、声明的串流 UDP **源端口** | 受限 B 保留协商后的媒体和已获入站授权连接的回包；C 全部切断，管理 UI 仍仅本机 |

loopback 保留本地 IPC。C 还保留[网络架构](network_CN.md)说明的窄核心恢复与自检路径；普通应用不能借 loopback 代理监听逃出 C。

## 可选的受限 B

默认 `my.hardening.directEgress.mode="compatibility"` 保留 LAN 兼容性。需要限制原生宿主 socket 时，选择 `"restricted"` 并只授权必要服务：

```nix
my.hardening.directEgress = {
  mode = "restricted";
  unboundDotInProxy = false;
  exceptions = [
    { interface = "ens1"; cidr = "192.168.1.20/32";
      uids = [ 1000 ]; tcpPorts = [ 443 ]; }
    { interface = "ens1"; cidr = "224.0.0.251/32";
      uids = [ 1000 ]; udpPorts = [ 5353 ]; }
  ];
};
```

每项须指定精确出口接口、规范目标 CIDR、明确 UID 列表和目标端口。显式 `uids=[]` 允许该项的任意 socket 属主。正常系统构建拒绝带主机位的网段、公网目标和没有端口的配置；允许发起连接不会自动开放入站服务。

B 守卫在 output `-195`、透明重定向之前检查原始目标。即使旧 `allowPrivateEgress=false`，受限模式仍让私网留在 TUN/NAT 外，由前置守卫授权，被拒绝的 socket 不能再绕入代理。撤销授权也切断已有宿主主动连接，自动保留的只有已获入站授权连接的 **reply 方向**。兼容模式的旧 `allowPrivateEgress=false` 则继续让私网目标接受代理路由。

此处约束原生宿主 socket，不隔离受信核心内部选定的目标，也不改变访客转发或代替入站授权；这些分别由核心目标与访客策略负责。

## 可选的时间服务器目标限制

`timeServerCidrs=[]` 保持 UID/物理口/端口约束，兼容 NTS 服务器地址变化。填写规范 IPv4/IPv6 CIDR 后，会在 **A 和 B** 的透明代理之前限制 Chrony 原始 NTP/NTS-KE 目标。应按实际提供商端点填写，文档示例地址不能作为服务器。NTS-KE 可协商另一台 NTP 服务器，只固定密钥交换端点可能导致校时失败；参见 [Chrony server 选项](https://chrony-project.org/doc/4.8/chrony.conf.html)。地址变化时拒绝连接，不自动扩大白名单，也不退回明文时间协议。

启用此限制时，受管 DNS 将声明的 NTS-KE 名排除出 fake-IP，让目标检查看到真实地址。如果 KE 协商出另一个主机名，还须将它加入 `timeServerNames`，同时限定其地址 CIDR；补充域名不扩大地址授权。Gost 的 TCP 后备保留限定后的 NTS-KE 例外；合法目标也可能走受管 TUN，物理直连仍要求物理口。关闭 B 的 Unbound DoT 不影响独立的 A 恢复策略和 B 受管 DNS。

## 验证与边界

`proxy-egress-config` 校验授权范围，并接入正常系统构建。`proxy-rules` / `proxy-egress-rules` 在 LKL 内解析完整兼容/受限规则。profile 检查核对 root/GUI 路由排除和 DNS 过滤器。`proxy-tailscale-vm` / `proxy-egress-vm` 覆盖两种策略、双栈真实 Tailscale STUN、UID/socket/packet 身份、DNS/DoT/NTS、多播、接口/端口拒绝、已有连接撤权与 C。`proxy-control-vm` / `proxy-egress-control-vm` 使用生产 helper 和真实 TUN 核心；`proxy-network-vm` 负责完整集成。命令见[维护检查](maintenance_CN.md#验证)。

socket mark 加 UID 减少意外或普通用户绕行，不隔离恶意 root 或持有 `NET_ADMIN` 的核心。构建期 LKL 支持 `socket mark`，不支持 `socket cgroupv2`；不受支持时不会静默退回全部 root UDP。隔离 VM 不能证明手机配对、硬件捕获/编码及真实公网打洞性能，部署后需按[远程桌面步骤](operations_CN.md#远程桌面)验证。
