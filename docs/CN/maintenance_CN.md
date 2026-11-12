# 网络补丁与自定义维护

[English](../maintenance.md) | 简体中文 | [文档目录](README_CN.md)

## 构建接入

版本由 [flake.lock](../../flake.lock) 决定，不跟随桌面自更新。稳定 Nixpkgs 提供 Gost、dnsmasq、Unbound、Firefox；unstable 提供 Mihomo、Clash Verge GUI 及独立打包的 helper。当前锁定组合为 Mihomo 1.19.33、vendored MetaCubeX sing-tun 0.4.28、Gost 3.3.2、GUI 2.5.2、helper 2.3.3、dnsmasq 2.93、Unbound 1.26.2。GUI/helper/core 版本相互独立。

[clash-verge.nix](../../system/programs/clash/clash-verge.nix) 构建定制核心/helper 并装入 GUI 包，[default.nix](../../system/programs/gost/default.nix) 构建定制 relay。两者以 `lib.fileset.toSource` 只收集 patcher、Go 代码和测试，避免无关仓库改动重建 Go 包。**新增补丁文件或测试时必须更新对应 fileset**，否则文件即使在仓库中也不会进入 builder。

Go patcher 在 `buildGoModule` 填充 `vendor/` 后的 `postConfigure` 执行；Rust helper 在 `postPatch` 追加修改，保留继承的 Nixpkgs 处理。锚点要求精确匹配，升级失败时应比较上游行为，不能放宽匹配或跳过补丁。

## 自定义清单

| 对象 | 仓库入口 | 目的 / 升级关注点 |
|---|---|---|
| Mihomo parser | [patch_mihomo.py](../../system/programs/clash/mihomo/patches/patch_mihomo.py)、[managed_profile.go](../../system/programs/clash/mihomo/config/managed_profile.go) | 最终受管策略、摘要、已知旧配置规范化，拒绝无条件 DIRECT 兜底 |
| Mihomo controller | [managed_route.go](../../system/programs/clash/mihomo/controller/managed_route.go)、[policy_middleware.go](../../system/programs/clash/mihomo/controller/policy_middleware.go) | 同 schema PATCH 解码与运行时写白名单 |
| Mihomo 出站 / DNS / 日志 | [target_guard.go](../../system/programs/clash/mihomo/guard/target_guard.go)、`patch_mihomo.py` | 目标验证/固定 IP、本地与远程叶节点授权、可选直拨拒绝、rule DNS metadata、Global 实际链路日志、只用快照 geodata |
| Mihomo SOCKS parser | [patch_socks.py](../../system/programs/clash/mihomo/patches/patch_socks.py)、[socks_addr_test.go](../../system/programs/clash/mihomo/tests/socks_addr_test.go) | 整数偏移避免长域名字节运算溢出 |
| Vendored sing-tun | [patch_sing_tun.py](../../system/programs/clash/mihomo/patches/patch_sing_tun.py) | nft 最大地址区间端点，以及 TUN-bound OUTPUT TCP DNAT |
| Vendored Gost HTTP | [patch_gost.py](../../system/programs/gost/patches/patch_gost.py)、[gost_http.go](../../system/programs/gost/src/gost_http.go) | 兼容裸数字 IPv6 CONNECT，保留 Go HTTP 解析 |
| Clash Verge helper | [patch_clash_service.py](../../system/programs/clash/service/patch_clash_service.py) | 固定 root 执行、日志及 socket/启动文件操作 |
| Clash Verge 打包/启动 | [clash-verge.nix](../../system/programs/clash/clash-verge.nix)、[核心启动器](../../system/programs/clash/service/clash-verge-core-launcher.sh)、[stage 工具](../../system/programs/clash/service/clash-verge-stage.py)、[GUI 启动器](../../system/programs/clash/gui/clash-verge-gui.py)、[GUI defaults](../../system/programs/clash/gui/clash-verge-defaults.sh) | 授权快照、降权、singleton 处理、HTTPS 延迟默认；复用原 GUI 二进制 |
| dnsmasq | [default.nix](../../system/config/network/network.nix) 内联替换 `src/forward.c` | 返回 strict-order 终端 UDP SERVFAIL/REFUSED，保留 DNSSEC EDE |
| Firefox 包 | [firefox.nix](../../home/programs/firefox/firefox.nix)、[guard.py](../../home/programs/firefox/guard.py)、[sandbox.py](../../home/programs/firefox/sandbox.py)、[scope.c](../../home/programs/firefox/scope.c) | 不可变 policy/AutoConfig/字体、预检、外层命名空间、过滤总线、门户身份及 Landlock/seccomp；无 Gecko 源码 patch |
| Unbound / Chrony / 网络 | [default.nix](../../system/config/network/network.nix)、[time.nix](../../system/config/network/time.nix)、[网络模块](../../system/config/network) | 声明策略和仓库自有协调器；没有本地 Unbound/Chrony 源码补丁 |
| 链路身份 | [default.nix](../../system/config/network/identity/default.nix)、[link_identity.py](../../system/config/network/identity/link_identity.py)、[partition.nix](../../system/hardware/partition.nix) | 协调 profile 身份、私有迁移/恢复、原生有线生命周期与 DHCPv6 共存；无上游源码 patch |
| 防火墙生命周期 | [default.nix](../../system/config/network/network.nix)、[coordinator.nix](../../system/config/network/proxy/coordinator.nix)、[bootstrap.nix](../../system/config/network/firewall/bootstrap.nix) | 精确表归属、持锁原子替换、失败保留和显式表迁移 |
| Tailscale 传输 | [transport.nix](../../system/config/network/tailscale/transport.nix)、[tailscale_contract.py](../../system/config/network/tailscale/tailscale_contract.py) | 正常系统构建强制检查上游 socket mark 约定；升级时检查 mark/后备/socket 变化并跑真实 CLI VM；没有 Tailscale 源码补丁 |
| 直连例外 | [options.nix](../../system/config/network/options.nix)、[default.nix](../../system/config/network/network.nix)、[egress_policy.py](../../system/config/network/firewall/egress_policy.py) | 主体/socket 身份、NAT 前授权、规范 CIDR 校验、时间/DoT 范围及 root/GUI 共享私网排除；比较兼容和受限模式 |
| 访客信任 | [policy.nix](../../system/config/network/guests/policy.nix)、[guest_policy.py](../../system/config/network/guests/guest_policy.py)、[socket-guest.nix](../../system/config/network/dns/socket-guest.nix)、[default.nix](../../system/programs/virtualisation/default.nix) | 接口/源角色、规范授权、主/委派 UID 归属、NAT 前真实地址 DNS、双向撤销及 rootless 启动器 |
| Sunshine | [sunshine.nix](../../system/programs/sunshine.nix) | 随图形会话提供 Desktop，管理仅本机、关闭 UPnP；保留 CUDA，以禁止本地编译的方式验证所配置 CUDA 缓存中的精确包 |

## Mihomo

共享域名过滤位于 [dns_filter.go](../../system/programs/clash/mihomo/guard/dns_filter.go)、[target_guard.go](../../system/programs/clash/mihomo/guard/target_guard.go) 和 [blocklist.nix](../../system/config/network/dns/blocklist.nix)。`dns.newHandler` 与 `Resolver.ExchangeContext` 接入点需保持在 hosts、fake-IP、缓存答案及引导上游查询之前，覆盖非地址 RR 类型；应用目标先过滤后缀，再判断私网域名豁免。保留与 dnsmasq 一致的最长后缀、同精度例外优先语义，拒绝不可读/无效名单。过滤代码和测试须加入包 fileset，并保留 `./dns` Go 测试。

`patch_mihomo.py` 接入最终 `ParseRawConfig`、controller router/PATCH、direct adapter、DNS metadata/dialer、TCP dial、UDP mapping、geodata downloader 与 Global 路由日志。升级须检查全部入口，集成检查覆盖实际拨号和控制写 API 上的策略执行。

Root 策略来自 [profile.nix](../../system/config/network/proxy/profile.nix)，[cvr-merge.nix](../../home/config/clash/cvr-merge.nix)、controller 输出及 [health.py](../../system/config/network/proxy/health.py) 必须一致。新增 `RawTun` 字段须在 schema coverage 测试显式分类，检查监听/隧道字段、默认值、别名及 `GET /configs` 暴露值。保留重复键/非规范 PATCH 拒绝，以及同份解码对象的验证/应用。

目标检查位于路由/嗅探后、真实拨号前，远程代理也须保持已验证 IP。内部后缀与字面 IP 也需出口授权；检查应在实际选中的子节点执行，不能先检查 `Unwrap` 再重新拨代理组。复用 UDP association 时保留实际叶节点与已验证目标来源，检查新映射，缓存映射每包重查入口、端口和授权，不重复解析。保留 mapped IP、元数据、全部 RFC 6052 前缀长度、自定义 fake-IP 与基础设施范围测试。`LFRuleDNS` 区分按规则 DNS 与内部引导，避免 `proxyOnly` 豁免应用 DNS。Metadata、适配器命名和组叶子变化需对照直拨测试。

生产 helper 必须设置 `LF_MANAGED_PROXY_POLICY`；未设置时保留上游行为，供开发 fixture 使用。策略摘要与 root 快照摘要分开；geodata 下载限制同时覆盖 parser 验证和损坏/缺失资源替换。

`patch_socks.py` 修改 `adapter/inbound/util.go` 运算并复制边界测试，头部记录上游参考。上游实现修正后，应比较长域名解码和畸形输入行为，保留回归覆盖，只移除冗余 patch 调用与 payload 项。

受管 fake-IP 必须保留地址合成和共享过滤，同时完整转发 HTTPS/SVCB 参数。Rule 和统一代理均保持关闭嗅探，旧 profile 继承 root 策略，避免 ECH public name 替换请求目标。

可选访客 TPROXY 入口在 `listener/listener.go` 与 `listener/tproxy` 中单独处理绑定及 IPv4 socket 选择。更新时保留 `allow-lan=false` 下 mixed/DNS/controller 的 loopback 行为、TPROXY 被动身份检查以及真实 TCP/UDP 访客测试。

## Mihomo 内的 sing-tun

补丁作用于 Mihomo 内嵌的 `vendor/github.com/metacubex/sing-tun`，版本由 Mihomo 依赖树确定。检查 `go.mod`、replace 指令与实际 vendor 树。

补丁包含三个独立部分：

1. `redirect_nftables_exprs.go`：最大 IPv4/IPv6 地址没有有效后继，省略区间结束标记，避免倒置或空 nft interval。Patcher 记录 SagerNet 参考，移除前须比较 MetaCubeX fork 的实际代码。
2. `redirect_nftables_rules.go`：受管非 mark auto-redirect 模式中，OUTPUT TCP DNAT 使用 TUN 自身地址代替 loopback，使 `SO_BINDTODEVICE(TUN)` 在 `route_localnet=0` 下正常。其它模式保留上游行为。
3. `redirect_nftables_rules.go`：PREROUTING 排除已由自有 B 模式访客 TPROXY 规则设置 `0x233` mark 的报文，保留选定透明 socket 与原始目标。该 mark 与 `network.nix` 共享，普通访客无法通过线上报文设置宿主 packet mark。

核对双栈、排除网段、hook 条件、访客 mark 和 redirect port；访客 mark 的查表规则优先级为 100，须早于 TUN 规则；C 自测应答例外与健康探针依赖该路径。控制/网络 VM 覆盖内核重选路，属于 sing-tun 升级检查集。

## Gost

补丁作用于 `pkgs.gost` 的 vendored `github.com/go-gost/x/handler/http`。升级同时检查 Gost 和 `go-gost/x` 版本。`lfReadRequest` 只改写数字 IPv6、十进制非零端口的裸 CONNECT 目标；`net/http.ReadRequest` 保留 HTTP 校验，返回的 reader 保留已缓冲隧道数据。

[gost_http_test.go](../../system/programs/gost/tests/gost_http_test.go) 应保留带括号 IPv6、普通请求、非法端口/framing、立即到达 payload 的测试。Relay 唯一监听、旧进程回收和资源限制也要保留；parser 修正不能替代生命周期保护。实际服务使用 `my.proxy.gostPackage`，不能换成 PATH 中未打补丁的包。

## Clash Verge helper 与启动器

GUI `.unwrapped` 有意复用上游并断言相等。自定义位于独立 Rust helper、安装的 wrapper 和运行脚本。检查 Nixpkgs helper derivation 和继承补丁；覆盖 `postPatch` 或复制错 helper 会丢失集成行为。

Helper patch 涉及 `src/core/manager.rs`、`server.rs`、`logger.rs`、`reconcile.rs`。升级时枚举所有执行、log writer、socket chmod/unlink 和已存进程恢复入口。受管执行须解析到 `LF_CLASH_CORE_PATH`，文件/祖先 root 所有，允许 root 所有 sticky store 祖先。日志始终使用私有服务目录，客户端路径/PID 不得转为 root 文件或进程操作。

共同启动路径要求子进程存活，且固定控制 socket 在 15 秒内可连接。候选解析和附加审计共享 11 秒准备预算，为核心启动或回退预留时间；受管 IPC handler 的预算为 20 秒。取消或就绪超时会结束本次启动的进程组，包括校验进程，避免超时后继续提交配置。修改 helper 或 IPC 依赖时须保留这一关系。

预构建 GUI 保留上游 IPC 客户端及较短的请求超时。Helper 串行处理启动，对相同请求复用最近 30 秒内完成的结果。重试签名包含固定 GUI 文件的身份与变化时间；复用成功还要求原 PID 仍在运行且固定 socket 可连接。输入变化、显式授权或核心已停止时须重新启动，使 GUI 的传输重试不会反复重启核心，也无需重新构建 GUI。

[runtime.nix](../../system/programs/clash/service/runtime.nix)、[identity.nix](../../system/programs/clash/service/identity.nix)、launcher 和 stage 工具须同步。顶层 runtime 组须匹配 systemd，避免重启递归改所有权。可写核心缓存由独立 tmpfiles 创建，不加入 root helper 的 `StateDirectory`。快照跨 helper 重启保留，授权/验证日志私有，并从真实 helper 启动的核心读取五类 capability 集。

启动默认值 helper 还负责规范化 `profiles.yaml`：只把已存在的整数 `update_interval` 从 1–1439 提升到 1440，不改 `allow_auto_update`、local profile、注释和未知字段；在 GUI 已停止的前提下持每用户锁执行。外层启动器持续持锁，直到规范化完成且 GUI 进程可观察，绝不创建缺失的 `option` 或 interval。

`clash-verge-stage.py` 负责候选检查、快照授权和健康接受；`profile-refresh-verify.timer` 每十秒运行。模式监督器发布 `/run/proxy-mode/boot-id`，`refresh.nix` 定义五分钟用户通知 timer。审计 worker 和状态工具使用由 Nix 显式提供的 PyYAML。worker 保持非特权身份，输出固定 JSON schema 和原因词表；`subscriptionAuditAllowedHosts` 从 root 配置传入，不能由订阅内容推导权限。

当前 Nixpkgs 以 `.verge-mihomo-wrapped` 暴露底层核心，包将公开 `verge-mihomo` 替换为授权启动器。GUI/打包更新需检查 wrapper 布局、`clash-on/off` 使用的 GUI 进程身份和旧同用户 singleton 清理，防止保留旧包路径。HTTPS delay 默认通过可写偏好设置，不重新编译 GUI。

## DNS 与浏览器集成

Dnsmasq patch 是 `network.nix` 的内联 `--replace-fail`。Strict-order 重试中，最后服务器的 UDP SERVFAIL/REFUSED 应返回客户端并保留 EDE；使用 sent-to 数组索引，支持重复端点。对照新 `forward.c` 检查终端错误、重试和重复服务器，并保留网络 VM 的真实 DNS 应答测试。

Unbound 使用未改源码的 `pkgs.unbound-with-systemd`。未来 override 必须保留 `--enable-systemd`，unit 为 `Type=notify`。DNSSEC/CD、持久化锚初始化、TLS 认证、答案过滤、陈旧缓存上限和探针结论应与 [DNS](dns_CN.md) 一致；Chrony 仍须强制 NTS 和证书时间校验。

Firefox 在打包层加入不可变 policy、AutoConfig、字体、preflight 和外层隔离。最终包/wrapper 和偏好语义由 `browser-privacy` 检查，覆盖最终启动器、桌面/MIME、policy lock、DoH/代理值、沙箱环境及旧 profile/进程。

用 `browser-sandbox-vm` 检查真实 Wayland 浏览器、系统 CA 的 TLS 接受及不可信/域名不匹配证书拒绝、父进程文件/设备/socket 限制、音频、门户身份与文档授权、精确 profile 的链接复用、代次拒绝、重启及代理故障。依赖或内核升级时检查 bubblewrap、xdg-dbus-proxy、Landlock ABI、seccomp 和 xdg-desktop-portal 身份协议，保留应用专用文档视图及宿主实例记录。设备选择检查须拒绝普通文件和无关 GPU 的 primary 节点；NVIDIA 权限跟随声明的驱动选择，也可单独关闭。同时核对解析符号链接后的 NixOS CA bundle 可读且 `/etc/static` 仍隐藏。系统信任测试使用全新浏览器 profile，由 NSS/p11-kit 从系统 CA bundle 读取信任。默认 HTTP 和可选透明路径还需在外层隔离内运行 `browser-ech-vm`，检查所选 Firefox 的 CONNECT 门、原生 type-65 API 和 NSS 重试/证书行为。自动化的原生 DNS 测试开关只放在临时 profile，区分认证后不带 ECH 重试与证书验证失败。[浏览器手册](browser_CN.md#ech-发现与代理兼容)介绍这些路径；本地 CA 与密钥只属于隔离 fixture。

`dnsEncryption="strict"` 在协调器、broker 和静态防火墙中必须一致：不能自动或强制回退，不授予明文租约，不安装后备集合放行规则。保留已批准 split DNS、MagicDNS 和字面门户访问；两种传输策略都要验证，默认恢复预算仍是独立行为。

NVIDIA 系统还应使用全新 profile 检查真实 Wayland 合成器及 PipeWire/Pulse 后端。在相同软件包与隐私策略下对照隔离内外的 `about:support`，选择 NVIDIA 权限时应保留预期的硬件 WebRender 与 DMA-BUF 能力，并确认沙箱内 EGL 平台配置可解析。硬件视频解码另有驱动及 Gecko 要求，使用相同软件包与无外层隔离的基线单独检查解码。

## 防火墙归属与加载

[防火墙生命周期](network_CN.md#防火墙生命周期)保留精确的表 family/name 组合，激活、重载、模式切换和漂移修复在该归属范围内执行。删除列表专用于 `proxy-control migrate-firewall`；[迁移操作](operations_CN.md#防火墙表迁移)要求操作者在删除前确认每个目标的归属。

保留共享 policy 锁，以及静态规则和安全模式片段的单次事务。显式迁移将清理追加到同一事务，不留下持久清理状态。nft 拒绝事务时须保留原策略，首次完整加载失败须保留独立 bootstrap 基线；停止 nftables unit 也须保留保护。代理意图在健康证明允许 B 前必须经过 C，包括重载或迁移时核心仍存活的情况。

修改 nftables、systemd 加载或协调逻辑时，运行 `proxy-firewall-vm`、相关完整规则变体、`proxy-control-vm` 和 `proxy-network-vm`。专用 VM 检查 A/C 表归属、外部集合与有序规则、失败事务、锁竞争及迁移目标名称复用。控制 VM 检查真实 helper/核心、TUN 表保留、C 阻断与 B 恢复；完整网络 VM 保留首次加载和漂移恢复覆盖。

## 链路身份

[default.nix](../../system/config/network/identity/default.nix) 和 [link_identity.py](../../system/config/network/identity/link_identity.py) 使用上游 NetworkManager/systemd/dhcpcd 接口，没有源码补丁。升级时核对实际 nmcli 属性名及枚举格式、两个主机名开关、stable-id 替换量、DHCP 标识和 IPv6 地址生成。保留仅含身份字段的备份白名单、私有状态、断开/自动连接守卫及拒绝覆盖手工修改的行为；重建不自动应用 profile 目标。

扩展 dhcpcd 单接口命令、PID 文件和 reload 时，保留上游生成配置与服务加固。其 DHCPv6 socket 必须能与 NetworkManager 的 Wi-Fi 客户端共存。一起运行 `network-identity-config`、`network-identity-vm`、`proxy-control-vm` 和 `proxy-network-vm`；身份 VM 对每次激活都要求新报文，包括 MAC 不变的连接。持久化准备和重新授权见[链路身份指南](identity_CN.md)。

## 升级流程

### 源码兼容性检查

[sources.json](../../system/config/network/maintenance/sources.json) 按组件记录维护入口、不变量和回归检查。[default.nix](../../system/config/network/maintenance/default.nix) 对实际选定包的源码、可用的 vendored 依赖摘要及主要 Nixpkgs 打包文件生成指纹。正常系统构建要求这些输入匹配配置的源码基线；即使版本号未变，源码或 recipe 改变仍须检查兼容性。Mihomo 变化也会选中其 vendored sing-tun 和 SOCKS 组件。

在仓库中生成当前输入清单并查看计划：

```sh
nix eval --json .#nixosConfigurations.lfour.config.my.proxy.upgradeSources \
  > /tmp/network-upgrade-selected.json
python3 system/config/network/maintenance/upgrades.py plan \
  system/config/network/maintenance/sources.json /tmp/network-upgrade-selected.json \
  > /tmp/network-upgrade-plan.json
```

计划列出变化的组件、源码入口、不变量和检查。可在命令末尾添加 `mihomo sing-tun socks`、`gost`、`clash`、`dns`、`firefox`、`tailscale`、`firewall` 或 `link-identity`，为本地定制变更或未变化的输入显式选择组件。执行选定检查：

```sh
jq -r '.checks[] | ".#checks.x86_64-linux." + .' /tmp/network-upgrade-plan.json \
  | xargs -r nix build --no-link
```

先比较上游源码与打包配置并完成组件检查，再修改对应基线条目，随后运行 `proxy-upgrade-review` 并构建候选系统。计划工具不会更新基线。Schema 要求各组件保留最小源码与回归覆盖，flake 也检查所列检查是否存在。

该门检查源码版本与指纹；精确补丁锚点、软件包测试及内核/浏览器集成测试检查对应行为。Recipe 指纹只覆盖主要软件包表达式，不覆盖完整传递构建闭包；仍需检查 lock 差异中的相关变化，包括部署内核和图形栈。增加补丁、依赖或入口时同步更新组件清单。

### 升级步骤

1. 确认所属 flake input，在仓库更新目标 input，比较 lock、Nixpkgs 包表达式、源码和 vendor 变化，保留可用 generation。
2. 比较上述全部受影响入口，重定位本地行为或在保留回归覆盖后移除冗余修复；保持精确锚点，文件移动时更新 fileset/构建阶段。
3. 约定变化时同步 root policy、Merge、health/controller、监听身份和 VM fixture；小控制 VM 继续使用生产身份和包。
4. 修改完成后统一运行相关集合。刷新、审计和启动默认值涉及 `proxy-refresh`、`proxy-control`、`proxy-gui`、`proxy-control-vm` 和 `proxy-refresh-vm`。构建候选系统而不激活，通过后再部署。激活后重启旧浏览器，主动 `clash-off` → `clash-on` 导入，再检查就绪和真实流量。
5. 更新清单、版本及相关手册，使其与最终实现一致。

## 验证

部署脚本在仓库中通过 `python3 tests/deployment/test_deployment.py scripts/push-to-dir.sh` 单独验证，以临时目录检查构建输入保留、文档排除、权限和输入缺失行为，无须 sudo 或宿主目标。需要 Python、Bash、rsync 和脚本使用的标准命令行工具；由于 `scripts/` 不部署，该检查与 flake checks 分开。

以下为 `checks.x86_64-linux.*` 属性。Nix 构建通常使用本机 daemon，测试本身不要求宿主 root 网络操作；VM 使用隔离内核，通过 KVM 运行时需要本机 KVM 访问权限。首次依赖构建成本较高，按受影响行为选择后统一运行。

| 检查 | 范围 |
|---|---|
| `proxy-control` | 临时文件验证快照授权、过期、重启、恶意输入和权限 |
| `proxy-refresh` | 离线审计、有界 HTTP 检查、本地更新状态和通知去重 |
| `proxy-refresh-vm` | 真实 auto/enforce helper 与核心：健康接受、坏候选回退、pin 重启、回滚及 desired-state 恢复 |
| `proxy-upgrade-review` | 所选源码/依赖/recipe 基线、组件覆盖、检查名称及源码兼容性检查；输入检查也接入正常系统构建 |
| `network-identity-config` | 身份迁移边界、实际 nmcli 解析/主机名恢复、有线变体、服务范围及持久化；正常系统构建也校验目标 |
| `network-identity-vm` | 真实有线/Wi-Fi DHCPv6 共存、新双栈身份报文、UUID/epoch/随机轮换、稳定/固定/硬件行为、profile 优先级、恢复及宿主密钥保留 |
| `proxy-regressions` | 健康、状态、规则、DNS 租约、预算及监督器 |
| `proxy-profile`、`proxy-gui` | 生成配置变体、策略/模板一致、GUI 默认与进程 wrapper |
| `proxy-rules`、`proxy-egress-rules` | 构建期 LKL nft 解析完整兼容/受限模式规则 |
| `proxy-firewall-vm` | A/C 表归属、外部表保留、加载被拒绝时的回滚、迁移锁、幂等及迁移目标名称复用 |
| `proxy-dns-strict-rules` | LKL 解析完整严格 DNS 防火墙规则 |
| `proxy-dns-config` | 默认/严格 DNS 协调器生成脚本语法 |
| `proxy-dns-strict-vm` | 真实 helper/核心、共享 DNS/代理过滤、严格故障/override/租约及残留集合拒绝、私网 split 权限与字面门户恢复 |
| `proxy-guest-config`、`proxy-guest-rules` | 生产/独立账户配置与 rootless 启动器语法；LKL 中的完整混合角色规则。正常系统构建也要求访客校验通过 |
| `proxy-guest-vm` | 三个真实转发网络、DHCP/DNS、IPv4/ULA、伪造源地址、实际 pasta/RootlessKit/subuid socket、fake-IP 拒绝、旧 DNS conntrack、双向撤销以及核心存活时的 C |
| `proxy-egress-config` | 规范 CIDR 与授权/时间目标范围；接入正常系统构建 |
| `proxy-egress-vm` | Tailscale VM 的受限策略版本；双栈私网/多播/DNS/DoT/NTS、UID/接口/端口拒绝、已有连接撤权与 C |
| `proxy-egress-control-vm` | 真实生产 TUN/NAT 之前的受限策略，包含私网允许与拒绝 |
| `tailscale-transport` | 所选上游 Linux bypass 常量与预期的 magicsock/STUN socket 打标路径；正常系统构建也强制执行 |
| `proxy-tailscale-vm` | 所选真实 Tailscale CLI、隔离双栈 STUN、双上行、socket/packet/UID 约束、B/C 恢复及远控端口策略 |
| `proxy-control-config` | 生产/测试身份、helper 能力与监听一致 |
| `proxy-core` | 定制核心构建和 parser/router/目标/direct/UDP/DNS/SOCKS Go 测试 |
| `proxy-target-vm` | 真实 helper/核心，宿主与节点 loopback 陷阱，HTTP/CONNECT/SOCKS、内部域名固定 IP、窄远程授权、Rule/Global 与 A/C 行为 |
| `proxy-gost` | 定制 relay 构建及 vendored HTTP 测试 |
| `browser-privacy` | 最终原生浏览器 policy lock 与离线启动 |
| `browser-sandbox-vm` | 真实 Wayland Firefox、系统根 TLS 与无效证书拒绝、父进程隔离、音频、门户授权、链接复用、代次/重启及总线代理故障阻断 |
| `browser-ech-vm` | 真实 Firefox/helper/核心、原生 HTTPS 发现、ECH 与 GREASE、密钥/无 ECH 重试、不可信拒绝、证书错误、掩护名不改目标及 A/C 行为 |
| `browser-ech-uniform-vm` | 在 `proxyOnly=true` 下验证相同真实浏览器路径及策略消费 |
| `proxy-control-vm` | 真实生产 helper/launcher、能力、授权、DNS、TUN 核心存活时的迁移及 C→B 恢复 |
| `proxy-uniform-vm` | 统一代理下的生产 helper/核心：全新 TCP/UDP 的 DIRECT 拒绝、双栈 UDP、原生 IPv6 TUN 和恢复 |
| `proxy-network-vm` | 广泛内核故障注入：模式、DNS、租约、休眠、访客、TPROXY 和恢复 |

例如将相关包与快速检查合并构建：

```sh
nix build --no-link .#checks.x86_64-linux.proxy-core \
  .#checks.x86_64-linux.proxy-gost .#checks.x86_64-linux.proxy-profile \
  .#checks.x86_64-linux.proxy-control .#checks.x86_64-linux.proxy-control-config \
  .#checks.x86_64-linux.proxy-regressions .#checks.x86_64-linux.proxy-gui
nix build --no-link .#checks.x86_64-linux.proxy-control-vm \
  .#checks.x86_64-linux.proxy-network-vm .#checks.x86_64-linux.proxy-target-vm
nix build --no-link .#checks.x86_64-linux.proxy-egress-config \
  .#checks.x86_64-linux.proxy-egress-rules .#checks.x86_64-linux.proxy-egress-vm \
  .#checks.x86_64-linux.proxy-egress-control-vm
nix build --no-link .#nixosConfigurations.lfour.config.system.build.toplevel
```

广泛 VM 含有意的模拟/root-core fixture，生产启动权限由小控制 VM 验证；两者均不改宿主路由/防火墙。包测试证明 parser/policy，不能替代内核路径测试。宿主激活后另需特权观测实际身份/能力、监听所有者、快照状态与 A → B/C 转换。
