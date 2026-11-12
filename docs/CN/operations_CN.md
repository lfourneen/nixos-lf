# 网络操作与诊断

[English](../operations.md) | 简体中文 | [文档目录](README_CN.md)

## 模式切换

使用 Nushell 的 `clash-on`、`clash-off`，它们记录特权意图，管理 GUI/helper 并等待一致状态。关闭 GUI 不会清除代理意图；核心停止后因此进入 C。

底层系统命令为 `sudo proxy-control on`、`sudo proxy-control off`。`on` 打开一次性配置导入窗口并启动 helper，GUI 仍需提供配置并启动核心；`off` 清除授权并停止 helper。桌面命令额外处理 GUI 进程。A 中仅启动 `clash-verge` 不会授权核心。

```sh
proxy-control preflight
proxy-policy
proxy-control status auto --check
proxy-control status proxy
proxy-control wait proxy
proxy-control status direct
firefox --check
proxy-inspect
```

`status auto` 跟随操作意图，`--check` 在 JSON 未就绪时返回非零状态。JSON 包含阶段、原因、DNS 额度和下一步操作；`wait` 每十秒显示进度。`proxy-status` 用英文面板显示模式、出站策略、DNS、校验时效、入口、告警和上次准备结果，读取同一份就绪契约。`--plain` 输出适合日志的文本，`--json` 输出结构化数据，`--no-color` 和 `NO_COLOR` 关闭颜色。`egress-audit` 提供额外主动诊断。`proxy-inspect` 仅读取状态、服务资源及链路/队列/offload 统计，不发送探针。

## 远程桌面

Sunshine 已启用，随 `graphical-session.target` 启动，将当前图形会话提供为 **Desktop**。部署后先登录电脑桌面，在电脑本机打开 `https://localhost:47990` 设置凭据并批准 Moonlight 配对 PIN。手机与电脑需登录同一获准的 tailnet，在 Moonlight 中手动添加电脑的 Tailscale IP，不依赖自动发现。同一 overlay 地址可用于局域网和公网，仍受 tailnet 权限策略约束。默认 `trustedLanCidrs` 为空，不直接开放物理 LAN IP 的远控访问。

管理界面仅接受本机客户端，默认 tailnet 防火墙也不放行该端口；UPnP 关闭。串流使用 [options.nix](../../system/config/network/options.nix) 的明确 TCP/UDP 清单。手机到电脑的 grants 需另在 Tailscale 管理后台配置，仓库不包含该权限策略。A 与健康 B 允许远控；C 会切断会话，包括已建立的 UDP 流及已解密 tailnet 入站。需要直连 A 时在本地使用 `clash-off`。

[sunshine.nix](../../system/programs/sunshine.nix) 选择 CUDA 构建变体。配置的 `https://cache.nixos-cuda.org` 提供与 lock 和软件包选项所选 derivation 匹配的二进制缓存。可用 `nix build --max-jobs 0 --no-link .#nixosConfigurations.lfour.config.services.sunshine.package` 检查缓存可用性，该命令禁用本地编译。凭据和配对状态保存在用户的 Sunshine 目录，Desktop 入口和服务设置由 Nix 声明。硬件捕获/编码与手机串流在部署后的图形会话中检查。

```sh
systemctl --user status sunshine.service
journalctl --user -b -u sunshine.service
sudo tailscale netcheck
sudo tailscale ping <手机的-tailnet-名称或-IP>
```

可选受限 B、Unbound DoT 开关和时间目标限制见[直连例外](egress_CN.md)。受限 B 保留协商后的 Sunshine 媒体；C 始终切断远控。

升级 Tailscale 时运行 `tailscale-transport`、`proxy-rules`、`proxy-egress-rules`、`proxy-tailscale-vm` 和 `proxy-egress-vm`；正常系统构建也强制执行源码约定检查。`0x80000` 来自 Tailscale 上游 Linux socket 标记，切换网络不变，升级时检查。无法打标或强制 bind-to-device 时，B 不会重新放行全部 root UDP；应诊断标记/权限，必要时明确切到 A。

`proxy-policy` 同时显示原生 UID/接口/CIDR 授权、核心 DIRECT 范围、远程目标授权和订阅信任。这些权限相互独立：原生 restricted 与宽泛核心 DIRECT 范围并用时，连接本地代理入口的客户端仍可使用核心授权。限制宿主应用访问内网时，应同时审阅 `directEgress.mode="restricted"` 和 `localTargetCidrs=[]`，再声明必需授权。专用访客账户保留独立边界。

`proxy-inspect` 报告命令失败分类及退出码，不输出任意服务 stderr。

## 状态与定位

| 文件 | 含义 |
|---|---|
| `/run/proxy-mode/state.json` | 原子模式、原因、策略代次、revision 和更新时间 |
| `/run/proxy-mode/health.json` | 探针结论与分层耗时 |
| `/run/proxy-mode/heartbeat.json` | 最近成功监督轮次 |
| `/run/netsec/state.json` | 验证结论、所验证代次与新鲜度 |
| `/run/dns-upstream/state.json` | DNS 模式/原因、加密策略、会话、预算、截止和 override |
| `/run/dns-policy/state.json` | Broker 租约状态与应用代次 |
| `/run/gost-relay/state.json` | Relay 观测与应用代次 |
| `/run/netsec/alerts/*.json` | 各 owner 告警；`/run/netsec/failed` 汇总 owner |
| `/run/proxy-mode/intent`、`core-seen` | 单次开机意图和崩溃检测标记 |
| `/run/proxy-mode/authorized-config`、`config-digest`、`authorize-window` | root 私有快照授权与一次性导入凭据 |
| `/run/proxy-mode/profile-refresh.json` | 最近刷新结果、digest、固定原因、脱敏审计计数和 20 条历史，权限 0644 |
| `/run/proxy-mode/profile-refresh-pin.json` | 被拒候选 digest 与一小时 TTL，权限 0644 |
| `/run/proxy-mode/profile-refresh-verifying.json` | root 私有待验证事务及上一快照路径 |
| `/run/proxy-mode/profile-refresh-settled.json`、`profile-refresh-commit.json`、`profile-refresh-restart.json` | root 私有 settled 授权、提交中断恢复和待完成内核恢复 |
| `/run/proxy-mode/effective-config.json`、`boot-id` | 绑定共享 boot identity 的 effective YAML 摘要；`effective-config-sha256` 是兼容镜像 |
| `/run/proxy-mode/health-failure.json` | 最近失败探针的固定原因、分层耗时、boot identity 与快照摘要 |

消费者读取原子 JSON，独立 `status`、`reason`、`version`、`result`、`verified-version` 是兼容镜像。仅原因变化更新 `revision`；内核策略提交更新 `policy_version`，`version` 是其别名。跨新代次的检查结果丢弃。

就绪要求模式、DNS、broker、relay 同代次且新鲜，没有告警，并有新鲜验证。Broker 必须 `applied`，`suspended`/`revoked` 和 DNS `degraded-dot-only` 不算就绪。消费者默认有效期 120 秒，验证为 420 秒以容纳五分钟定时器及有界执行。Boot identity 和单调时间戳拒绝跨开机、未来与陈旧记录。

| 原因 | 定位方向 |
|---|---|
| `request`、`reload` | 转换中等待核心健康与验证 |
| `unauthorized-core`、`core-gone` | 意图、启动器/helper 与核心 cgroup |
| `no-tun`、`tun` | 设备、受管表/规则、实际选路与 fake-IP HTTP |
| `config` | 快照摘要、受管 API 和 TCP/UDP 监听身份 |
| `route` | 日志实际叶子为 DIRECT/REJECT 或与选择图不符 |
| `obs`、`obs:1/2`、`obs:2/2` | HTTP 完成、精确源端口日志和选择观测 |
| `resolver-error`、`split-error` | DNS 身份或批准 Wi-Fi profile/租约 |

```sh
journalctl -b -u proxy-mode -u dns-upstream -u gost-relay -u clash-verge
cat /run/proxy-mode/health.json
cat /run/dns-upstream/state.json
cat /var/lib/dns-upstream/history.log
```

部分日志和授权文件需要 sudo。诊断输出中应去掉快照、订阅配置等敏感内容。

### 刷新状态

`proxy-status` 的 `Auto update` 显示更新调度，`Profile refresh` 和 `Update result` 显示最近刷新，`Profile vs runtime` 比较 GUI 与已导入快照的摘要。`proxy-policy` 包含 `profile_refresh`，`proxy-inspect` 提供最多 20 条脱敏刷新历史。

`verifying` 表示已导入快照正在等待健康结果；`fallback` 表示候选被拒绝，旧授权快照保留；`rolled_back` 表示已恢复快照授权并请求 helper 重启。核心是否恢复须查看网络就绪字段。摘要不同只表示内容不同，不能据此判断哪个文件更新。

切换订阅仍使用同一份 root 策略。新的 hosts 覆盖须匹配 `subscriptionAuditAllowedHosts`，切换动作本身不会扩大该权限。探测目标和预期路由均可配置；受保护路由仍无法确定时，`enforce` 保留旧快照，应先根据审计分类定位约束，再决定策略。

用户通知 timer 每五分钟读取本地文件，将刷新失败、内容审计结果、更新逾期、内容差异和订阅 stale 合并为每轮一条消息。刷新失败按结果与完整摘要去重，逾期和内容差异按状态迁移提醒，stale 每天最多提醒一次。去重状态私有存于 `$XDG_STATE_HOME/clash-verge/refresh-notified.json`，发送成功后才推进；timer 不下载订阅。

### 订阅连通性

显式运行 `proxy-subscription --check --json` 检查当前订阅。命令从 GUI 索引读取地址，报告 HTTP 状态、传输路径、耗时、字节数、HTTPS 和重定向次数。提供 `--etag` 时发送条件 GET，没有 validator 时发送普通 GET。

检查先使用当前网络策略允许的直连路径，传输失败后尝试配置的 mixed 端口。403 等完整 HTTP 响应直接报告，不再通过代理重试。命令不写 profile、不导入配置，也不推进更新时间。

总超时默认 15 秒，覆盖域名解析、两条传输路径、重定向和正文读取；`--timeout` 支持 1–120 秒。响应上限为 8 MiB，最多允许三次同源重定向，跨源跳转和 HTTPS 降级均拒绝。输出不包含 URL、响应正文和 validator 原文。`proxy-inspect --deep` 另提供服务重启次数和固定 journal 查询命令，供有权限的操作者排障。

## 协调与防火墙恢复

Socket 目录事件、TUN link 监视、nft 事件、NetworkManager dispatcher 和 dhcpcd hook 唤醒监督器。自适应轮询后备为转换中两秒、执行模式最多五秒、直连最多 60 秒；模式监督器有 90 秒 watchdog。取消的探针不算失败观测。

构建期的[预期规则](../../system/config/network/proxy/coordinator.nix) 在 LKL 用户态内核中用同版 nft 生成。Verifier 比较所属表、hook、priority、policy 和完整有序表达式，仅规范化 handle、counter、集合元素顺序和受严格约束的 DNS 租约。读取/解析错误是失败，不当作空成功；没有核心但正确安装 C 也能通过。

`nftables-verify` 报告失败和告警后仍退出零，以便修复/重建继续；应看 JSON 结论，不能只看 unit 退出码。各组件只清自己的告警，忙碌轮次保留排队验证请求。

```sh
sudo systemctl restart nftables.service
sudo systemctl start nftables-verify.service
```

Restart/reload 原子重装所属静态策略和安全模式；代理意图先回 C，再由健康门恢复 B。Stop 保留已加载规则。`nftables-verify-repair.service` 显式修复并复验，`nftables-recover` 重试失败的 nftables unit。不要清空全局 ruleset。

## 防火墙表迁移

`proxy-control migrate-firewall` 显式删除 `inet filter`、`ip proxymode_nat` 和 `ip6 proxymode_nat`，并安装[保留表](network_CN.md#防火墙生命周期)。普通启动、重载和修复保留这三个名称。执行迁移前须确认这些表归本配置所有。

查看迁移目标并确认归属：

```sh
sudo nft list table inet filter
sudo nft list table ip proxymode_nat
sudo nft list table ip6 proxymode_nat
```

确认归属后执行迁移，并检查安装的策略：

```sh
sudo proxy-control migrate-firewall
sudo systemctl start nftables-verify.service
```

该 root 命令持有 operator 和 policy 锁，以一次 nft 事务提交三个目标表的删除、静态规则和安全模式。事务被拒绝时保留已安装的规则和目标表。目标表允许缺失，因此在名称仍归本配置所有时支持重复执行。

代理意图或核心仍在运行时先安装 C，监督器在健康检查通过后开放 B；直连意图且没有核心时安装 A。迁移保留意图、授权和运行中的核心。使用上文诊断命令检查模式和验证 JSON。清理仅在本命令执行期间发生，其它服务的表由各自所有者管理。

## 配置入口

| 来源 | 设置 |
|---|---|
| [machine.nix](../../system/config/machine.nix) | 用户、接口及匹配键、UID、端口、mark、网段和上游 |
| [options.nix](../../system/config/network/proxy/options.nix) | TUN、表 2022、fake-IP、探针、开机意图、proxyOnly、dnsEncryption、共享域名过滤、访客、split DNS 和新鲜度 |
| [profile.nix](../../system/config/network/proxy/profile.nix) | root 受管 DNS/TUN/profile 约定 |
| [options.nix](../../system/config/network/options.nix) | 宿主/访客私网、逐接口访客信任/源地址、独立 socket 访客账户、宿主端口、热点、tailnet 和 Wi-Fi MAC |
| [cvr-merge.nix](../../home/config/clash/cvr-merge.nix) | 匹配的 GUI Merge 及路由增强 |
| [firefox.nix](../../home/programs/firefox/firefox.nix)、[options.nix](../../home/programs/firefox/options.nix) | 原生浏览器、networkPath、nvidiaAcceleration、隐私、策略、字体和外层沙箱 |

TUN 默认 `Mihomo`，表 2022、规则优先级 9000、内部 mark 8227/8228、fallback priority 32768、file descriptor 0，由 parser、模板和健康检查共用。`tunMode=false` 选择 HTTP-only/redir-host，不再要求 TUN。Fake-IP 网络由 `fakeIpRange` 推导，避免单独修改消费者。

需要不同访客信任级别时，应显式选择开发网桥，并只为其它访客授权必要的私网服务。宿主 socket 后端另需独立固定账户；选择空闲 UID 与互不重叠的 subordinate 范围：

```nix
my.hardening = {
  guestNetworks.virbr0.trust = "trusted";
  guestPrivateExceptions = [{
    interface = "waydroid0"; cidr = "192.168.1.20/32";
    tcpPorts = [ 443 ];
  }];
  socketGuestUsers = [ "guestnet" ];
  socketGuestPrivateExceptions = [{
    user = "guestnet"; interface = "ens1"; cidr = "192.168.1.20/32";
    tcpPorts = [ 443 ];
  }];
};
users.users.guestnet = { isNormalUser = true; uid = 1100; };
```

示例地址需要替换。内建网桥源地址/网关默认来自 `my.machine`；额外 `guestInterfaces` 必须有规范且互不重叠的 `guestNetworks` 源 CIDR。仅在需要开放宿主服务时，才为相应角色添加 `hostTcpPorts` / `hostUdpPorts`，目标限定于声明网关。IPv6 需显式 ULA 源地址/网关，访客 IPv6 公网仍拒绝。热点只有明确调整自身 trust 后才变为可信。

先安装/协调完整 nft 策略，再以 `guestnet` 运行 pasta 或 rootless Docker。Docker 用户单元会为受限账户清除继承的显式代理环境变量，并使用声明的 rootless 包与设置。新增/更改身份或委派 UID 映射前停止旧后端。为运行中的后端撤销私网访问时，保持身份声明、移除授权并重载防火墙，已跟踪连接的双向流量都会切断。仅设置 `socketGuestUsers` 不会迁移桌面容器。这些账户使用单独的真实地址 DNS 路径，没有明文/split DNS 后备。见[访客边界](network_CN.md#访客与容器)。

Wi-Fi profile 显式的克隆 MAC 优先于 `my.hardening.wifi.clonedMacAddress`；否则默认 `stable-ssid` 为相同 SSID 保留相同 MAC。`permanent` 选择硬件地址，`preserve` 保持当前地址。需要协调身份变化时，声明 `my.hardening.wifi.identities`，查看 `network-identity plan UUID`，关闭自动连接并断开后再执行 `apply` 或 `restore`。完整步骤、有线策略和 DHCP 状态准备见[链路身份](identity_CN.md)。

核心内部服务访问由[目标授权](proxy_CN.md#域名目标)单独管理：`localTargetCidrs` 限定本地 DIRECT，`remoteTargetGrants` 按实际叶节点、核心入口、协议和端口授权远程内部地址，`nat64Prefixes` 声明已知翻译前缀。内部后缀只允许本地解析私网答案，不授权节点代解析或拨号。Gost CONNECT 在核心中是 `HTTPS`；修改后需重建并有意导入/重启核心。A 字面门户、C 阻断与原生直连授权各自保持独立。

## 离线恢复

需要直连时用 `clash-off`。默认 `dnsEncryption="opportunistic"` 下，A 中门户/DNS 恢复先读 DNS 状态和历史，必要时显式开启有界续租 override：

```sh
sudo touch /run/dns-upstream/force-plaintext
# 恢复后删除，重启也会清除。
sudo rm /run/dns-upstream/force-plaintext
```

它允许明文 DNS 并继续计入预算，不能越过 C 的出站 guard，范围见 [DNS](dns_CN.md)。

`my.proxy.dnsEncryption="strict"` 忽略 override，不授予明文后备；`clash-off` 选择的 A 也保留加密 DNS。可尝试字面 HTTP 门户地址、更换上行或修复认证 DoT 配置。门户若依赖不可用的公网 DNS，需要主动改变策略，例如离线选择已构建的 `opportunistic` generation；在出行依赖严格模式前先准备可用 generation。私网 split DNS 和 MagicDNS 仍受各自批准及模式约束。

Generation 故障时从 systemd-boot 选择旧条目；菜单保留八个以适应 ESP 容量。GC 尚未删除的 store 路径也能离线激活：查 `/nix/var/nix/profiles/system-*-link`，以 root 运行 `nix-env -p /nix/var/nix/profiles/system --set /nix/store/<nixos-system-…>`，再运行 `/nix/var/nix/profiles/system/bin/switch-to-configuration boot`。全部无法启动时用 NixOS ISO 解锁并挂载根及 ESP，在 `nixos-enter --root /mnt` 中恢复。

## 配置检查

[维护手册](maintenance_CN.md#验证)按改动列出检查，区分临时文件测试、包构建、隔离内核 VM 和宿主激活。修改完成后统一执行相关集合。`scripts/push-to-dir.sh` 仅部署构建输入和测试源码，不运行检查，也不复制 `docs/`。
