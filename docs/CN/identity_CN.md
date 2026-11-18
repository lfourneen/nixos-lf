# 链路身份与 MAC 策略

[English](../identity.md) | 简体中文 | [索引](README_CN.md)

本地网络在应用代理开始前就能观察链路身份，因此 MAC、DHCP 标识和 IPv6 地址生成需要一致的生命周期。
A/B/C 路由和浏览器隐私仍是独立配置。

## 默认值与优先级

Wi-Fi 扫描使用随机地址。
连接 profile 未显式配置时，采用 `my.hardening.wifi.clonedMacAddress`，默认为 `stable-ssid`：同一宿主上相同 SSID 使用相同 MAC。
NetworkManager 状态、连接配置和 machine-id 跨根目录回滚持久保存。

这个 MAC 默认值本身不选择按网络区分的 DHCPv6 DUID。
上游分别说明了 [profile MAC 优先级](https://networkmanager.dev/docs/api/latest/settings-802-11-wireless.html)和 [DHCPv6 身份选项](https://networkmanager.dev/docs/api/latest/settings-ipv6.html)。

Profile 中显式的 `802-11-wireless.cloned-mac-address` 优先于全局默认。
清空该属性后，下次激活采用默认值。
`permanent` 选择硬件地址，`preserve` 保持当前地址。

主有线上行默认使用硬件 MAC。
`my.machine.wired.mac` 通过 `PermanentMACAddress` 识别硬件。
线上地址变化不改变接口名或防火墙角色。
有线由 dhcpcd 管理，Wi-Fi 由 NetworkManager 管理。

## 按网络声明 Wi-Fi 身份

使用既有 Wi-Fi 客户端 profile 的 UUID 声明目标。

通过 `nmcli -f NAME,UUID connection show` 查看 UUID，并替换示例：

```nix
my.hardening.wifi.identities = {
  "11111111-1111-4111-8111-111111111111" = {
    mode = "stable";
    epoch = "1";
  };
  "22222222-2222-4222-8222-222222222222" = {
    mode = "random";
  };
  "33333333-3333-4333-8333-333333333333" = {
    mode = "fixed";
    address = "02:11:22:33:44:55";
  };
};
```

| 模式 | 激活连接时的 MAC |
|---|---|
| `stable` | 根据 profile UUID、设备名、宿主密钥和 epoch 派生 |
| `random` | 每次激活生成新地址 |
| `permanent` | 硬件地址 |
| `fixed` | 指定的非零单播 `address` |

迁移工具为 DHCPv4 client-id/IAID、DHCPv6 DUID/IAID 和稳定 IPv6 地址生成设置共同的 `connection.stable-id` 种子。
随机模式加入 NetworkManager 的每次激活随机替换量。
同时优先使用临时 IPv6 地址，禁用 DHCP 主机名发送，并清空既有 hostname/FQDN 和 vendor-class 值。

相关语义见 [stable-id 替换量](https://networkmanager.dev/docs/api/latest/settings-connection.html)和 [DHCPv4 设置](https://networkmanager.dev/docs/api/latest/settings-ipv4.html)。

不同 profile UUID 可为同名 SSID 的网络提供不同身份。
SSID 相同时应主动选择目标 profile。
提高 `epoch` 并显式应用目标，可轮换派生标识。
固定和硬件模式仍保留其指定 MAC。
服务发现、认证凭据和账户会话保留各自的标识。

## 应用与恢复

声明目标不会自动改写保存的 profile。

构建并安装包含声明的 generation 后，先检查目标连接：

```sh
profile_uuid=11111111-1111-4111-8111-111111111111
network-identity plan "$profile_uuid"
sudo nmcli connection modify uuid "$profile_uuid" connection.autoconnect no
```

若连接正在使用，先执行 `sudo nmcli connection down uuid "$profile_uuid"`。

随后应用并主动重连：

```sh
sudo network-identity apply "$profile_uuid"
sudo nmcli connection up uuid "$profile_uuid"
```

`apply` 和 `restore` 都要求客户端 profile 已断开、自动连接已关闭。
完成后可按需重新启用自动连接。
工具不主动重连、不切换网络模式，也不更改凭据、DNS 和路由。
每次修改先保存仅含身份字段的私有备份，再通过一次 NetworkManager 设置事务提交。
[nmcli 属性](https://networkmanager.dev/docs/api/latest/nm-settings-nmcli.html)同时覆盖主机名三态开关及其兼容布尔值，使恢复保留原有行为。

恢复最近一次备份时，同样先关闭自动连接并断开，再执行：

```sh
sudo network-identity restore "$profile_uuid"
sudo nmcli connection up uuid "$profile_uuid"
```

恢复写回保存的属性，同时保留 NetworkManager 租约状态。
lease 模式继续从保留的租约状态选择 DUID。
恢复后应核对租约和授权。
迁移后若手动修改过身份字段，restore 会拒绝覆盖。
移除 Nix 目标后仍可使用备份。
Profile 独立于系统 generation 持久保存，因此应在回滚到不含工具的 generation 前完成恢复。

保留 `/var/lib/NetworkManager/secret_key` 和 `/etc/machine-id`。
删除它们会影响所选网络之外的身份。

## 有线策略

主 dhcpcd 上行可选：

```nix
my.hardening.wired.clonedMacAddress = "random";
# 也可指定固定地址，例如 "02:11:22:33:44:66"。
# 默认值为 "permanent"。
```

随机策略在初始 udev 配置时设置地址，之后由 dhcpcd 原生逻辑在接口启用或载波丢失时随机化。
字面地址由 `.link` 文件设置。
非 `permanent` 策略同时启用 DHCP anonymity 和基于当前 MAC 的 SLAAC：客户端标识跟随当前 MAC、抑制主机名，并从新的协商开始而不重用旧租约的 REBOOT 流程。

上游说明见锁定版本的 [dhcpcd 配置手册](https://raw.githubusercontent.com/NetworkConfiguration/dhcpcd/v10.3.1/src/dhcpcd.conf.5.in)及 [systemd.link 匹配与地址策略](https://www.freedesktop.org/software/systemd/man/latest/systemd.link.html)。

有线 dhcpcd 保持专用用户，以原生单接口模式运行。
其 DHCPv6 监听绑定有线链路本地地址，使 NetworkManager 的 Wi-Fi DHCPv6 客户端可以同时工作。
保留上游生成配置和服务加固。

修改有线策略时，先构建准备好的启动 generation，再计划重启。
重连前确认门户、MAC 白名单或 DHCP 预约已授权目标地址。
有线策略作用于主上行，不提供 NetworkManager 的按网络 profile 选择器。

## IPv4 ARP 防护

宿主默认只对接收接口上的地址应答 ARP，并优先选用适合目标链路的源地址。
`my.hardening.arp.sameSubnetReplies` 可在指定的上行或访客接口启用 `arp_ignore=2`，进一步要求请求方与目标地址位于同一子网。
Wi-Fi 接口启用后对所有 profile 生效，适合路由器与客户端共用 IPv4 子网的链路。[Linux ARP 参数](https://docs.kernel.org/networking/ip-sysctl.html#ip-variables)

登记连接或更换路由器前，从路由器管理控制台或受管网络清单核验网段、网关 IP 和 MAC。
DHCP 与观察到的 ARP 报文可以用来定位差异。故障转移网关共用虚拟 MAC 时，登记核验后的虚拟 MAC。

```nix
my.hardening.arp = {
  wired = {
    subnet = "192.0.2.0/24";
    gateway = "192.0.2.1";
    mac = "02:00:00:00:02:01";
    filter = "observe";
  };
  wifi."11111111-1111-4111-8111-111111111111" = {
    subnet = "198.51.100.0/24";
    gateway = "198.51.100.1";
    mac = "02:00:00:00:51:01";
    filter = "observe";
  };
  sameSubnetReplies = [ "ens1" ];
  managedInterfaces.virbr0 = "observe";
};
```

`wired` 对 dhcpcd 管理的专用有线口生效。
`wifi` 以 NetworkManager 的连接 UUID 为键，可用 `nmcli -f UUID,NAME connection show` 查询。
双上行可以各自绑定。策略先核对接口地址与 IPv4 主路由表，再安装永久邻居。断链或换网后会清理策略创建的旧项。
默认配置未登记网关，未登记的 Wi-Fi profile 使用动态 ARP。

`network-arp status` 显示 `pinned`、`dynamic` 和绑定失配状态。活动上行失配会阻止开通 B 或续租。链路绑定与代理健康检查均通过后可以恢复 B。IPv6 继续使用独立的 NDP 策略。

`filter = "observe"` 固定网关，并在宿主 ARP 入站 hook 统计无效帧和冲突声明。用 `sudo nft list table arp lf_arp` 查看计数，在确认兼容的连接上选择 `"enforce"` 拒绝这些帧。
`managedInterfaces` 对显式声明的访客接口应用帧检查。合法的地址冲突探测与换址通告仍可通过。[IPv4 地址冲突检测](https://www.rfc-editor.org/rfc/rfc5227.html)

需要显式进入 A 时使用 `clash-off`，已配置的绑定在 A 中仍生效。
如果绑定阻碍恢复，激活带有正确参数的配置，或为有线设置 `wired = null`、为 Wi-Fi 移除对应 UUID，让该连接重新使用动态解析。恢复 `"observe"` 可撤回帧拒绝并保留固定邻居。

当前链路 MAC 对所在广播域内的设备可见。使用 VLAN 或 AP 隔离时，分别核对同 AP、跨 AP 与有线桥接的可达范围。部署 DHCP Snooping 和 Dynamic ARP Inspection 时，核对 VLAN、受信上行、静态绑定及接入端口能否拒绝同时冒用已授权 IP 和 MAC 的设备。对需要隔离邻居查询的工作负载，可使用独立网络命名空间，例如[身份浏览器的网络沙箱](browser_CN.md#网络策略与身份)。[OpenWrt 无线隔离](https://openwrt.org/docs/guide-user/network/wifi/basic?s%5B%5D=2&s%5B%5D=server) · [Cisco Dynamic ARP Inspection](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9400/software/release/16-6/configuration_guide/sec/b_166_sec_9400_cg/configuring_dynamic_arp_inspection.html)

## 持久状态与验证

身份备份保存在持久化的 `/var/lib/network-identity`，目录和文件仅 root 可访问。
NetworkManager 状态和 profile 已持久化。

dhcpcd 状态另行显式启用：

```nix
my.hardening.wired.persistDhcpIdentity = true;
```

首次启用前，先构建候选配置，停止 dhcpcd，将当前 `/var/lib/dhcpcd` 目录连同所有权和权限复制到 `/persist/var/lib/dhcpcd`，再启动新 generation。
首次绑定空目录会遮蔽原有 DUID 和租约。
若目标已存在，应检查并协调，避免覆盖更新的状态。
匿名模式忽略保留的 DUID。
保持 false 则沿用既有持久化布局。

重连后检查实际 MAC、IPv4/IPv6 获取和门户授权，再检查[网络就绪状态](operations_CN.md#状态与定位)。
C 仍阻断应用出站。
有意进行门户恢复时使用既有[离线恢复流程](operations_CN.md#离线恢复)。

更新 NetworkManager、systemd 或 dhcpcd 时，按[维护流程](maintenance_CN.md#源码兼容性检查)选择 `link-identity` 组件检查兼容性。
