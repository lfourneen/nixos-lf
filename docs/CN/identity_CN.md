# 链路身份与 MAC 策略

[English](../identity.md) | 简体中文 | [索引](README_CN.md)

本地局域网在应用代理还没介入前就能看到你的链路身份，所以 MAC 策略、DHCP 标识和 IPv6 地址生成最好放在同一个生命周期里协调。至于 A/B/C 路由和浏览器隐私，依然是独立配置。

## 默认值与优先级

Wi-Fi 扫描默认使用随机地址。如果某个连接 profile 没有单独指定 MAC，就会采用 `my.hardening.wifi.clonedMacAddress`（默认 `stable-ssid`，同一台机器上连同一个 SSID 会用同一个 MAC）。NetworkManager 状态、已保存的连接和 `machine-id` 在根目录回滚后都会保留，但这个 MAC 默认值本身并不会帮你选按网络区分的 DHCPv6 DUID。上游文档分别说明了 [profile MAC 优先级](https://networkmanager.dev/docs/api/latest/settings-802-11-wireless.html)和独立的 [DHCPv6 身份选项](https://networkmanager.dev/docs/api/latest/settings-ipv6.html)。

如果已保存的 profile 里显式写了 `802-11-wireless.cloned-mac-address`，它会覆盖全局默认值；把这个属性清空，下次激活时就会退回默认行为。设为 `permanent` 会用硬件地址，`preserve` 则保持当前地址不变。

主有线上行默认使用硬件 MAC。`my.machine.wired.mac` 通过 `PermanentMACAddress` 认出硬件设备，所以就算线上地址改了，接口名和防火墙角色也不会受影响。有线继续归 `dhcpcd` 管，Wi-Fi 归 NetworkManager 管，互不干扰。

## 按网络声明 Wi-Fi 身份

用现有 Wi-Fi 客户端 profile 的 UUID 来声明目标身份。先用 `nmcli -f NAME,UUID connection show` 查出 UUID，然后把下面的示例替换掉：

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

迁移工具会为 DHCPv4 client-id/IAID、DHCPv6 DUID/IAID 以及稳定 IPv6 地址生成设置统一的 `connection.stable-id` 种子，其中 `random` 模式会加上 NetworkManager 的每次激活随机替换量。同时工具还会开启临时 IPv6 地址优先、禁止发送 DHCP 主机名，并清空原有的 hostname/FQDN 和 vendor-class 值——这些都基于 NetworkManager 的 [stable-id 替换量](https://networkmanager.dev/docs/api/latest/settings-connection.html)和 [DHCPv4 设置](https://networkmanager.dev/docs/api/latest/settings-ipv4.html)。

就算两个网络共用同一个 SSID，只要用不同的 profile UUID 就能各自拥有独立身份（SSID 重叠时记得手动选对目标 profile）。想轮换派生出来的标识，把 `epoch` 加大再重新应用即可；`fixed` 和 `permanent` 模式则会保留指定的 MAC，只轮换派生的 DHCP/IPv6 标识。服务发现、认证凭据和账户会话依然保持各自原有的标识。

## 应用与恢复

在 Nix 里写好声明并不会自动改写已经保存的 profile。构建并激活包含声明的 generation 后，先检查目标连接：

```sh
profile_uuid=11111111-1111-4111-8111-111111111111
network-identity plan "$profile_uuid"
sudo nmcli connection modify uuid "$profile_uuid" connection.autoconnect no
```

如果连接正连着，先用 `sudo nmcli connection down uuid "$profile_uuid"` 断开，再应用并手动重连：

```sh
sudo network-identity apply "$profile_uuid"
sudo nmcli connection up uuid "$profile_uuid"
```

`apply` 和 `restore` 都要求目标 profile 处于断开且关闭自动连接的状态，操作完之后按需把自动连接开回来就行。工具不会帮你自动重连、切换网络模式，也不会动凭据、DNS 和路由。每次修改前，它都会先存一份只包含身份字段的私有备份，再通过单次 NetworkManager 设置事务提交更改。所选用的 [nmcli 属性](https://networkmanager.dev/docs/api/latest/nm-settings-nmcli.html)同时覆盖主机名三态开关及其兼容布尔值，保证恢复时能原样还原行为。

要恢复最近一次备份，同样先关掉自动连接并断开，再执行：

```sh
sudo network-identity restore "$profile_uuid"
sudo nmcli connection up uuid "$profile_uuid"
```

恢复时会把保存的属性写回 profile，同时保留 NetworkManager 的租约状态（lease 模式会继续从这份租约状态里挑 DUID，恢复后记得顺手核对下租约和门户授权）。如果迁移之后你手动改过身份字段，`restore` 会拒绝覆盖。哪怕把 Nix 里的目标声明删掉了，备份也依然能用；因为 profile 独立于系统 generation 持久保存，如果要回滚到不含这个工具的旧 generation，记得先做好恢复。另外，`/var/lib/NetworkManager/secret_key` 和 `/etc/machine-id` 也要留着，删掉任意一个都会让派生的身份连带受影响。

## 有线策略

主 `dhcpcd` 上行除了默认的 `"permanent"`，还可以选下面这两种策略：

```nix
my.hardening.wired.clonedMacAddress = "random";
# 也可指定固定地址，例如 "02:11:22:33:44:66"。
# 默认值为 "permanent"。
```

选 `"random"` 时，udev 会在初始配置时先设一个地址，之后由 `dhcpcd` 原生逻辑在接口启用或载波丢失时负责随机化；写死字面地址则直接通过 `.link` 文件配置。只要不是 `"permanent"`，系统就会同时启用 DHCP 匿名化和基于当前 MAC 的 SLAAC：客户端标识跟随当前 MAC、抑制主机名发送，并且每次都从头协商而不复用旧租约的 REBOOT 流程。具体参考锁定版本的 [dhcpcd 配置手册](https://raw.githubusercontent.com/NetworkConfiguration/dhcpcd/v10.3.1/src/dhcpcd.conf.5.in)和 [systemd.link 匹配与地址策略](https://www.freedesktop.org/software/systemd/man/latest/systemd.link.html)。

有线 `dhcpcd` 以专用用户运行在原生单接口模式下，把 DHCPv6 监听绑定到有线链路本地地址，这样就能和 NetworkManager 的 Wi-Fi DHCPv6 客户端同时工作，同时保留上游生成的配置和服务加固。修改有线策略时，建议先构建好新启动 generation 再安排重启，重连前确认门户、MAC 白名单或 DHCP 预约已经放行了目标地址。有线策略统一作用于主上行，没有 NetworkManager 那种按网络区分的 profile 选择器。

## IPv4 ARP 防护

默认情况下，宿主机只对接收接口上的地址应答 ARP，并尽量挑适合目标链路的源地址。`my.hardening.arp.sameSubnetReplies` 可以在指定的上行或访客接口上进一步开启 `arp_ignore=2`，要求请求方和目标地址必须位于同一子网（对 Wi-Fi 接口开启后会影响所有 profile，适合路由器与客户端共用 IPv4 子网的场景；见 [Linux ARP 参数](https://docs.kernel.org/networking/ip-sysctl.html#ip-variables)）。

登记连接或更换路由器之前，先从路由器管理后台或受管网络清单里核对好网段、网关 IP 和 MAC（不确定时可以结合 DHCP 和抓到的 ARP 报文定位差异；如果是故障转移网关共用虚拟 MAC，就登记核验过的虚拟 MAC）。

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

`wired` 作用于 `dhcpcd` 管理的专用有线口，`wifi` 则以 NetworkManager 连接 UUID 为键（用 `nmcli -f UUID,NAME connection show` 查询），双上行可以各自独立绑定。策略在安装永久邻居前会先核对接口地址和 IPv4 主路由表，断链或换网时会自动清理自己创建的旧条目。默认配置不登记任何网关，未登记的 Wi-Fi profile 继续走动态 ARP。

用 `network-arp status` 可以看到 `pinned`、`dynamic` 或绑定失配状态。只要活动上行出现绑定失配，系统就会阻止开通 **B** 或续租，直到链路绑定和常规代理健康检查都通过才恢复 **B**；IPv6 则继续走独立的 NDP 策略。

`filter = "observe"` 会固定网关，并在宿主 ARP 入站 hook 统计无效帧和冲突声明；确认兼容后可以先用 `sudo nft list table arp lf_arp` 看计数，再切到 `"enforce"` 正式拦截这些帧。`managedInterfaces` 会对声明的访客接口应用同样的帧检查，合法的地址冲突探测和换址通告依然能正常通过（见 [IPv4 地址冲突检测](https://www.rfc-editor.org/rfc/rfc5227.html)）。

需要显式切回 **A** 时跑 `clash-off`，已配置的绑定在 **A** 里照样生效。万一绑定本身卡住了恢复流程，可以激活改好参数的新配置，或者把有线设为 `wired = null`、把对应 Wi-Fi UUID 删掉，让连接退回动态解析；改回 `"observe"` 则能保留固定邻居、只撤回帧拦截。

还要注意，当前链路 MAC 在整个广播域内都是可见的。如果在做二层或网络侧隔离，记得结合 VLAN 或 AP 隔离设置分别核对同 AP、跨 AP 和有线桥接的可达性；部署 DHCP Snooping 和 Dynamic ARP Inspection 时，也要核对 VLAN、受信上行、静态绑定，以及接入端口是否能拦住同时冒用已授权 IP 和 MAC 的设备。需要隔离邻居视图的工作负载可以放进独立网络命名空间，比如[身份浏览器的网络沙箱](browser_CN.md#网络策略与身份)（参考 [OpenWrt 无线隔离](https://openwrt.org/docs/guide-user/network/wifi/basic?s%5B%5D=2&s%5B%5D=server) 和 [Cisco Dynamic ARP Inspection](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9400/software/release/16-6/configuration_guide/sec/b_166_sec_9400_cg/configuring_dynamic_arp_inspection.html)）。

## 持久状态与验证

身份备份保存在 `/var/lib/network-identity` 下，目录和文件都只有 root 能访问。NetworkManager 的状态和 profile 默认已经持久化，而 `dhcpcd` 状态需要单独开启：

```nix
my.hardening.wired.persistDhcpIdentity = true;
```

第一次开启前，先构建候选配置、停掉 `dhcpcd`，把当前的 `/var/lib/dhcpcd` 连同属主和权限一起复制到 `/persist/var/lib/dhcpcd`，然后再启动新 generation——如果直接挂载空目录，会把原有的 DUID 和租约遮住。若目标目录已经存在，先核对并协调一下，别把更新的状态盖掉了。匿名模式下会忽略保留的 DUID，保持 `false` 则沿用原有的持久化布局。

重连后检查一下实际 MAC、IPv4/IPv6 获取和门户授权，再确认[网络就绪状态](operations_CN.md#状态与定位)。注意 **C** 依然会阻断应用出站，有需要时按[离线恢复流程](operations_CN.md#离线恢复)做门户恢复。升级 NetworkManager、systemd 或 `dhcpcd` 时，在[维护流程](maintenance_CN.md#源码兼容性检查)里选 `link-identity` 组件检查兼容性即可。
