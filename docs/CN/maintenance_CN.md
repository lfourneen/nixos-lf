# 网络补丁与自定义维护

[English](../maintenance.md) | 简体中文 | [文档目录](README_CN.md)

网络系统包含 Mihomo、sing-tun、Gost、Clash Verge 和 dnsmasq 的本地补丁，以及受管 Firefox 包装。
本手册列出这些定制的源码入口和升级时需要检查的兼容性。

## 构建接入

[flake.lock](../../flake.lock) 固定软件包源码。
稳定 Nixpkgs 提供 Gost、dnsmasq、Unbound 和 Firefox，Unstable 提供 Mihomo、Clash Verge GUI 和单独打包的 helper。
GUI、helper 和核心的版本相互独立。

[clash-verge.nix](../../system/programs/clash/clash-verge.nix) 构建定制核心/helper 并装入 GUI 包，[default.nix](../../system/programs/gost/default.nix) 构建定制 relay。

两者以 `lib.fileset.toSource` 只收集 patcher、Go 代码和测试，避免无关仓库改动重建 Go 包。
**新增补丁文件或测试时必须更新对应 fileset**，否则文件即使在仓库中也不会进入 builder。

Go patcher 在 `buildGoModule` 填充 `vendor/` 后的 `postConfigure` 执行。
Rust helper 在 `postPatch` 追加修改，保留继承的 Nixpkgs 处理。
锚点要求精确匹配，升级失败时应比较上游行为，不能放宽匹配或跳过补丁。

## 自定义清单

| 对象 | 仓库入口 | 目的 / 升级关注点 |
|---|---|---|
| Mihomo parser | [patch_mihomo.py](../../system/programs/clash/mihomo/patches/patch_mihomo.py)、[managed_profile.go](../../system/programs/clash/mihomo/config/managed_profile.go) | 最终受管策略、摘要、已知旧配置规范化，拒绝无条件 DIRECT 兜底 |
| Mihomo controller | [managed_route.go](../../system/programs/clash/mihomo/controller/managed_route.go)、[policy_middleware.go](../../system/programs/clash/mihomo/controller/policy_middleware.go) | 静默丢弃 mode/TUN/重载与有限的运行时写白名单 |
| Mihomo 出站 / DNS / 日志 | [target_guard.go](../../system/programs/clash/mihomo/guard/target_guard.go)、`patch_mihomo.py` | 目标验证/固定 IP、本地与远程叶节点授权、可选直拨拒绝、rule DNS metadata、Global 实际链路日志、只用快照 geodata |
| Vendored sing-tun | [patch_sing_tun.py](../../system/programs/clash/mihomo/patches/patch_sing_tun.py) | TUN-bound OUTPUT TCP DNAT 与访客 TPROXY socket 保留 |
| Vendored Gost HTTP | [patch_gost.py](../../system/programs/gost/patches/patch_gost.py)、[gost_http.go](../../system/programs/gost/src/gost_http.go) | 兼容裸数字 IPv6 CONNECT，保留 Go HTTP 解析 |
| Clash Verge helper | [patch_clash_service.py](../../system/programs/clash/service/patch_clash_service.py) | 内核 peer 身份约束生命周期、固定 root 执行、日志及 socket/启动文件操作 |
| Clash Verge 打包/启动 | [clash-verge.nix](../../system/programs/clash/clash-verge.nix)、[核心启动器](../../system/programs/clash/service/clash-verge-core-launcher.sh)、[stage 工具](../../system/programs/clash/service/clash-verge-stage.py)、[GUI 启动器](../../system/programs/clash/gui/clash-verge-gui.py)、[GUI defaults](../../system/programs/clash/gui/clash-verge-defaults.sh)、[控制 shim](../../system/programs/clash/gui/control_shim.sh) | 授权快照、降权、singleton 处理、HTTPS 延迟默认。OS 代理写入拦截与仅控制字段变化时复用快照 |
| dnsmasq | [default.nix](../../system/config/network/network.nix) 内联替换 `src/forward.c` | 返回 strict-order 终端 UDP SERVFAIL/REFUSED，保留 DNSSEC EDE |
| Firefox 包 | [firefox.nix](../../home/programs/firefox/firefox.nix)、[guard.py](../../home/programs/firefox/guard.py)、[sandbox.py](../../home/programs/firefox/sandbox.py)、[scope.c](../../home/programs/firefox/scope.c) | 不可变 policy/AutoConfig/字体、预检、外层命名空间、过滤总线、门户身份及 Landlock/seccomp。无 Gecko 源码 patch |
| Unbound / Chrony / 网络 | [default.nix](../../system/config/network/network.nix)、[time.nix](../../system/config/network/time.nix)、[网络模块](../../system/config/network) | 声明策略和仓库自有协调器。没有本地 Unbound/Chrony 源码补丁 |
| 链路身份 | [default.nix](../../system/config/network/identity/default.nix)、[link_identity.py](../../system/config/network/identity/link_identity.py)、[partition.nix](../../system/hardware/partition.nix) | 协调 profile 身份、私有迁移/恢复、原生有线生命周期与 DHCPv6 共存。无上游源码 patch |
| 防火墙生命周期 | [default.nix](../../system/config/network/network.nix)、[coordinator.nix](../../system/config/network/proxy/coordinator.nix)、[bootstrap.nix](../../system/config/network/firewall/bootstrap.nix) | 精确表归属、持锁原子替换、失败保留和显式表迁移 |
| Tailscale 传输 | [transport.nix](../../system/config/network/tailscale/transport.nix)、[tailscale_contract.py](../../system/config/network/tailscale/tailscale_contract.py) | 正常系统构建强制检查上游 socket mark 约定。升级时检查 mark/后备/socket 变化并跑真实 CLI VM。没有 Tailscale 源码补丁 |
| 直连例外 | [options.nix](../../system/config/network/options.nix)、[default.nix](../../system/config/network/network.nix)、[egress_policy.py](../../system/config/network/firewall/egress_policy.py) | 主体/socket 身份、NAT 前授权、规范 CIDR 校验、时间/DoT 范围及 root/GUI 共享私网排除。比较兼容和受限模式 |
| 访客信任 | [policy.nix](../../system/config/network/guests/policy.nix)、[guest_policy.py](../../system/config/network/guests/guest_policy.py)、[socket-guest.nix](../../system/config/network/dns/socket-guest.nix)、[default.nix](../../system/programs/virtualisation/default.nix) | 接口/源角色、规范授权、主/委派 UID 归属、NAT 前真实地址 DNS、双向撤销及 rootless 启动器 |
| Sunshine | [sunshine.nix](../../system/programs/sunshine.nix) | 随图形会话提供 Desktop，管理仅本机、关闭 UPnP。保留 CUDA，以禁止本地编译的方式验证所配置 CUDA 缓存中的精确包 |

## Mihomo

共享域名过滤位于 [dns_filter.go](../../system/programs/clash/mihomo/guard/dns_filter.go)、[target_guard.go](../../system/programs/clash/mihomo/guard/target_guard.go) 和 [blocklist.nix](../../system/config/network/dns/blocklist.nix)。

`dns.newHandler` 与 `Resolver.ExchangeContext` 接入点需保持在 hosts、fake-IP、缓存答案及引导上游查询之前，覆盖非地址 RR 类型。
应用目标先过滤后缀，再判断私网域名豁免。
保留与 dnsmasq 一致的最长后缀、同精度例外优先语义，拒绝不可读/无效名单。
过滤代码和测试须加入包 fileset，并保留 `./dns` Go 测试。

`patch_mihomo.py` 接入最终 `ParseRawConfig`、controller router/PATCH、direct adapter、DNS metadata/dialer、TCP dial、UDP mapping、geodata downloader 与 Global 路由日志。
升级须检查全部入口，集成检查覆盖实际拨号和控制写 API 上的策略执行。

Root 策略来自 [profile.nix](../../system/config/network/proxy/profile.nix)，[cvr-merge.nix](../../home/config/clash/cvr-merge.nix)、controller 输出及 [health.py](../../system/config/network/proxy/health.py) 必须一致。

新增 `RawTun` 字段须在 schema coverage 测试显式分类，检查监听/隧道字段、默认值、别名及 `GET /configs` 暴露值。
保留重复键/非规范 PATCH 拒绝，以及同份解码对象的验证/应用。

目标检查位于路由/嗅探后、真实拨号前，远程代理也须保持已验证 IP。
内部后缀与字面 IP 也需出口授权。
检查应在实际选中的子节点执行，不能先检查 `Unwrap` 再重新拨代理组。
复用 UDP association 时保留实际叶节点与已验证目标来源，检查新映射，缓存映射每包重查入口、端口和授权，不重复解析。

启用本地 DNSSEC 时，[validated_answers.go](../../system/programs/clash/mihomo/guard/validated_answers.go) 限制并合并公网拨号目标查询。
答案复用不得超过验证器 TTL，缓存和未完成查询须按策略隔离。
替换策略必须使旧工作失效，单个调用者取消则不能取消其他消费者的共享查询。
保留不同名称数量上限及 A/AAAA 共用截止，并覆盖过期、零 TTL、单族失败和策略替换回归。

保留 mapped IP、元数据、全部 RFC 6052 前缀长度、自定义 fake-IP 与基础设施范围测试。
`LFRuleDNS` 区分按规则 DNS 与内部引导，避免 `proxyOnly` 豁免应用 DNS。
Metadata、适配器命名和组叶子变化需对照直拨测试。

生产 helper 必须设置 `LF_MANAGED_PROXY_POLICY`。
未设置时保留上游行为，供开发 fixture 使用。
策略摘要与 root 快照摘要分开。
geodata 下载限制同时覆盖 parser 验证和损坏/缺失资源替换。

受管 fake-IP 必须保留地址合成和共享过滤，同时完整转发 HTTPS/SVCB 参数。
Rule 和统一代理均保持关闭嗅探，旧 profile 继承 root 策略，避免 ECH public name 替换请求目标。

可选访客 TPROXY 入口在 `listener/listener.go` 与 `listener/tproxy` 中单独处理绑定及 IPv4 socket 选择。
更新时保留 `allow-lan=false` 下 mixed/DNS/controller 的 loopback 行为、TPROXY 被动身份检查以及真实 TCP/UDP 访客测试。

## Mihomo 内的 sing-tun

补丁作用于 Mihomo 内嵌的 `vendor/github.com/metacubex/sing-tun`，版本由 Mihomo 依赖树确定。
检查 `go.mod`、replace 指令与实际 vendor 树。

补丁包含两个独立部分：

1. `redirect_nftables_rules.go`：受管非 mark auto-redirect 模式中，OUTPUT TCP DNAT 使用 TUN 自身地址代替 loopback，使 `SO_BINDTODEVICE(TUN)` 在 `route_localnet=0` 下正常。其它模式保留上游行为。
2. `redirect_nftables_rules.go`：PREROUTING 排除已由自有 B 模式访客 TPROXY 规则设置 `0x233` mark 的报文，保留选定透明 socket 与原始目标。该 mark 与 `network.nix` 共享，普通访客无法通过线上报文设置宿主 packet mark。

核对双栈、排除网段、hook 条件、访客 mark 和 redirect port。
访客 mark 的查表规则优先级为 100，须早于 TUN 规则。
C 自测应答例外与健康探针依赖该路径。
控制/网络 VM 覆盖内核重选路，属于 sing-tun 升级检查集。

## Gost

补丁作用于 `pkgs.gost` 的 vendored `github.com/go-gost/x/handler/http`。
升级同时检查 Gost 和 `go-gost/x` 版本。
`lfReadRequest` 只改写数字 IPv6、十进制非零端口的裸 CONNECT 目标。
`net/http.ReadRequest` 保留 HTTP 校验，返回的 reader 保留已缓冲隧道数据。

[gost_http_test.go](../../system/programs/gost/tests/gost_http_test.go) 应保留带括号 IPv6、普通请求、非法端口/framing、立即到达 payload 的测试。
Relay 唯一监听、旧进程回收和资源限制也要保留。
parser 修正不能替代生命周期保护。
实际服务使用 `my.proxy.gostPackage`，不能换成 PATH 中未打补丁的包。

## Clash Verge helper 与启动器

GUI `.unwrapped` 保持不变，可使用上游二进制缓存。
本地修改只重建核心、小型 Rust helper 和启动 wrapper，不重建 GUI。
独立 Rust helper、安装的 wrapper 和运行脚本各自保持集成。
检查 Nixpkgs helper derivation 和继承补丁。
覆盖 `postPatch` 或复制错 helper 会丢失集成行为。

Helper patch 涉及 `src/core/manager.rs`、`server.rs`、`logger.rs`、`reconcile.rs` 和 vendored Kode Bridge HTTP server。
每个请求须保留内核 Unix peer 身份。非 root 的停止和已运行核心的启动请求静默丢弃，受控来源须由 root 启动。
身份缺失或生命周期状态不可用时，不得授权 GUI 重启。
升级时枚举所有执行、log writer、socket chmod/unlink 和已存进程恢复入口。
受管执行须解析到 `LF_CLASH_CORE_PATH`，文件/祖先 root 所有，允许 root 所有 sticky store 祖先。
日志始终使用私有服务目录，客户端路径/PID 不得转为 root 文件或进程操作。

共同启动路径要求子进程存活，且固定控制 socket 在 15 秒内可连接。
候选解析和附加审计共享 11 秒准备预算，为核心启动或回退预留时间。
受管 IPC handler 的预算为 20 秒。
取消或就绪超时会结束本次启动的进程组，包括校验进程，避免超时后继续提交配置。
修改 helper 或 IPC 依赖时须保留这一关系。

GUI 保留上游 IPC 客户端及较短的请求超时。
Helper 串行处理启动，对相同请求复用最近 30 秒内完成的结果。
重试签名包含固定 GUI 文件的身份与变化时间。
复用成功还要求原 PID 仍在运行且固定 socket 可连接。
已授权的 root 兼容导入在输入变化时重新启动，显式授权或核心已停止时也须重新启动。
GUI 只可在已知核心停止时执行兼容模式下的首次授权启动。
root 来源激活先显式停止核心，再启动替代实例。
已授权的 root 停止请求等待 watchdog 退出并持久化停止意图，helper 监听保持可用。
配置拒绝不能触发 helper 重启。

[runtime.nix](../../system/programs/clash/service/runtime.nix)、[identity.nix](../../system/programs/clash/service/identity.nix)、launcher 和 stage 工具须同步。

顶层 runtime 组须匹配 systemd，避免重启递归改所有权。
可写核心缓存由独立 tmpfiles 创建，不加入 root helper 的 `StateDirectory`。
快照跨 helper 重启保留，授权/验证日志私有，并从真实 helper 启动的核心读取五类 capability 集。

启动默认值 helper 还负责规范化 `profiles.yaml`：只把已存在的整数 `update_interval` 从 1–1439 提升到 1440，不改 `allow_auto_update`、local profile、注释和未知字段。
在 GUI 已停止的前提下持每用户锁执行。
外层启动器持续持锁，直到规范化完成且 GUI 进程可观察，绝不创建缺失的 `option` 或 interval。

`clash-verge-stage.py` 负责候选检查、快照授权和健康接受。
[source_refresh.py](../../system/programs/clash/service/source_refresh.py) 在重启健康且已授权的核心前检查登记候选。
它使用隔离副本，复用导入时的非特权解析器与审计执行规则，不改写运行缓存，也不授予运行权限。
候选、来源版本、settled 基线和 root 策略的绑定须保持到实际切换。
符合策略的节点变化生成 root 私有、有效期五分钟的 `approval.json`，实际导入仍由原有批准读取器校验。
记录还绑定审计和验证器策略，结构类别及未确定结果不能参与自动批准。

启动时恢复符合当前受管策略的已接受来源，更新请求保持待处理，直到经验证的 B 持续健康 90 秒。
稳定期绑定本次启动、快照和策略，离开健康 B 后重置。
稳定的黑名单路径仍须指向构建时校验的不可变内容。恢复仍重复静态检查，进入 B 仍要求实际健康证明。
仅显式 `on` 与 C 中延期的自动任务创建 `source-refresh-pending`。服务在下载前消费请求，显式 `off` 时清除。
内部代理意图保护动作不排队更新。这些规则防止激活触发下载与重启循环。
自动任务在 C 中延期，不消耗下载重试，也不放宽应用防护。
该路径与刷新预检须保持一致。

定时下载在网络等待期间释放来源发布锁，重新加锁后核对登记和所选来源，再发布候选。
须保留这项复核，使前台能够启动，同时阻止已撤销输入被发布。

本次启动内，显式 on/off 操作保留健康失败 pin 的剩余 TTL。启动导入保留完整的 60 秒窗口，后续导入的连续新鲜完整失败证明可在配置的宽限期后触发回滚。成功接受始终保留完整观察期。

已有精确 `approval.json` 记录仍按原有绑定要求校验。
来源比较不提供批准或激活动作。

检查期间释放授权锁，重新加锁后再次核对运行健康和输入绑定。
接受或回滚事务尚未收尾时，本次刷新暂缓处理。
固定的 `clash-source-guard.service` 事务在重启前进入 C，`CAP_NET_ADMIN` 不授予下载和解析服务。
撤权失败必须阻止重启。实际导入仍重新解析、审计，再进行健康接受或回滚。

`profile-refresh-verify.timer` 每十秒运行。
模式监督器发布 `/run/proxy-mode/boot-id`，`refresh.nix` 定义五分钟用户通知 timer。
审计 worker 和状态工具使用由 Nix 显式提供的 PyYAML。

worker 保持非特权身份，输出固定 JSON schema 和原因词表。
`subscriptionAuditAllowedHosts` 从 root 配置传入，不能由订阅内容推导权限。

当前 Nixpkgs 以 `.verge-mihomo-wrapped` 暴露底层核心，包将公开 `verge-mihomo` 替换为授权启动器。
GUI/打包更新需检查 wrapper 布局、`clash-on/off` 使用的 GUI 进程身份和旧同用户 singleton 清理，防止保留旧包路径。
HTTPS delay 默认通过可写偏好设置。
启动 wrapper 拦截 gsettings、dconf 和 KDE helper 的 System Proxy 写入，包含 Nixpkgs 注入的绝对路径。
无法使用 mount namespace 时，使用 memory GSettings 与 KDE 5 helper 名称兜底。
控制器静默丢弃 mode/TUN PATCH 和配置重载，解析器以默认值加 root 策略生成 TUN。
升级 GUI 时运行 `proxy-gui` 和 `proxy-gui-vm`，在两种路由子模式下检查真实 IPC、页面控件、PAC/代理守护写入、启动/退出清理及核心连续性。

## DNS 与浏览器集成

运行时路由使用两组不可变策略/健康/审计文件，由 root 选择一个符号链接。
保留 operator → source → authorization → policy 的锁顺序；探针在 policy 锁外运行，提交前重新检查路由 epoch 与策略摘要。
应用子模式及续签内核租约均要求 mixed TCP、fake-IP TCP、DNS、监听与配置的完整证明，proxy-only 另需经核心验证器的真实地址 DNS 证明。
来源下载在释放 source 锁等待网络后，重新检查选择、登记、策略与 epoch。
原始 Rule 数据及固定节点保存在参与摘要的 `source-data.json`，每个来源和子模式分别保存已接受快照。
策略切换关闭旧核心/relay 流、清空解析缓存，并保留跨重启的 fake-IP 域名映射。
来源启动会等待刚启动的 helper 暴露 IPC，不能仅因启动延迟重启该 helper；既有核心的恢复仍保留 IPC 传输故障时的单元重启。
独立的 `lf_routing` 表在基础防火墙重载后仍须执行并核对声明的节点传输元组，已有直连回复不构成例外。
修改这些路径时运行 `proxy-routing`、`proxy-routing-vm`。

Dnsmasq patch 是 `network.nix` 的内联 `--replace-fail`。
Strict-order 重试中，最后服务器的 UDP SERVFAIL/REFUSED 应返回客户端并保留 EDE。
使用 sent-to 数组索引，支持重复端点。
对照新 `forward.c` 检查终端错误、重试和重复服务器，并保留网络 VM 的真实 DNS 应答测试。

Unbound 使用未改源码的 `pkgs.unbound-with-systemd`。
未来 override 必须保留 `--enable-systemd`，宿主 unit 为 `Type=notify`。
DNSSEC/CD、持久化锚初始化、TLS 认证、答案过滤、陈旧缓存上限和探针结论应与 [DNS](dns_CN.md) 一致。
Chrony 仍须强制 NTS 和证书时间校验。

启用 `localDnssec` 时，[core-validator.nix](../../system/config/network/dns/core-validator.nix) 在核心命名空间中分别运行应用与基础设施验证器。
保留独立查询/资源预算、持久信任锚，以及对客户端 CD 绕过的拒绝。
本地验证与上游选择分别配置：rules 应用 DNS 保留国内分流，`geosite:cn` 直连使用国内验证用 DoH 端点，其余使用经 `lf-remote` 传输的 Cloudflare/Google DoH，节点引导保留独立加密解析器。
Root 管理的 `lf-dns-transport` 使用独立上游列表（`mihomoDomesticValidatingDnsUpstreams`），不得继承 A 的 DoT 列表。
保留 `patch_mihomo.py` 中的配置解析及 executor 传输接入点、完整 DNSSEC 材料、TLS 校验，以及不复用普通缓存的原始转发。
Rules 应用查询保留此分流与完整 DNSSEC 材料。Proxy-only 将两个验证器的传输绑定到经剪枝远程兜底组的认证字面地址 DoH，禁用直连引导。来源/后端的真实地址 DNS 保留固定 Unix 桥接及独立请求预算，不回退宿主解析器。
私有 TUN 的 UID 排除必须与验证器仅向 loopback 查询的防火墙、应用访问全部验证器/传输端口的拒绝配套保留。
Mihomo 必须在公网 fake-IP 合成和公网拨号地址固定之前验证，清除合成地址的 AD，并保留 HTTPS/SVCB 参数。
已知 profile 迁移与外来值、controller 弱化写入的拒绝须保持独立。
修改这些路径时运行 `proxy-assurance-vm`。

Firefox 在打包层加入不可变 policy、AutoConfig、字体、preflight 和外层隔离。
最终包/wrapper 和偏好语义由 `browser-privacy` 检查，覆盖最终启动器、桌面/MIME、policy lock、DoH/代理值、沙箱环境及旧 profile/进程。

浏览器检查分为三类：

- `browser-privacy` 检查软件包中的策略、启动器和偏好锁定。
- `browser-sandbox-vm` 检查真实 Wayland 浏览器、系统 CA、文件与设备访问、音频、文档门户和 profile 复用。
- `browser-ech-vm` 与 `browser-ech-uniform-vm` 检查 HTTP 代理和透明路径、HTTPS 记录、ECH 重试及证书拒绝。

更新沙箱依赖或内核时，需要检查 bubblewrap、xdg-dbus-proxy、Landlock、seccomp 和门户身份识别。
保留各应用独立的文档视图及仅宿主可读的实例记录。
设备选择须拒绝普通文件和无关 GPU 节点。
系统 CA 应通过只读 bundle 生效，同时保持 `/etc/static` 隐藏。

更新 Firefox 或 NSS 时，对照[浏览器手册](browser_CN.md#ech-发现与代理兼容)检查 CONNECT 条件、原生 type-65 解析 API 和重试行为。

`dnsEncryption="strict"` 在协调器、broker 和静态防火墙中必须一致：不能自动或强制回退，不授予明文租约，不安装后备集合放行规则。
保留已批准 split DNS、MagicDNS 和字面门户访问。
两种传输策略都要验证，默认恢复预算仍是独立行为。

NVIDIA 系统还应使用全新 profile 检查真实 Wayland 合成器及 PipeWire/Pulse 后端。
在相同软件包与隐私策略下对照隔离内外的 `about:support`，选择 NVIDIA 权限时应保留预期的硬件 WebRender 与 DMA-BUF 能力，并确认沙箱内 EGL 平台配置可解析。
硬件视频解码另有驱动及 Gecko 要求，使用相同软件包与无外层隔离的基线单独检查解码。

## 防火墙归属与加载

[防火墙生命周期](network_CN.md#防火墙生命周期)保留精确的表 family/name 组合，激活、重载、模式切换和漂移修复在该归属范围内执行。
删除列表专用于 `proxy-control migrate-firewall`。
[迁移操作](operations_CN.md#防火墙表迁移)要求操作者在删除前确认每个目标的归属。

保留共享 policy 锁，以及静态规则和安全模式片段的单次事务。
显式迁移将清理追加到同一事务，不留下持久清理状态。
nft 拒绝事务时须保留原策略，首次完整加载失败须保留独立 bootstrap 基线。
停止 nftables unit 也须保留保护。
代理意图在健康证明允许 B 前必须经过 C，包括重载或迁移时核心仍存活的情况。

本地验证和 HTTP-only 配置按 `tunProbeNames` 顺序尝试。
仅合成地址的 TUN 配置使用新生成的名称。
客户端 DNS 探针在 20 秒探测预算内最多执行三轮。
超时或不可用答案会触发下一次尝试，耗尽预算仍是硬性失败。
HTTP-only 探针必须解析出 IPv4 答案，不能仅接受非空诊断或 CNAME。
`degraded` 仅用于全部硬性检查通过后的观察性警告，就绪检查仍须保留代次、boot、新鲜度和活动告警限制。

启用授权租约时，四条到期链均须保留内核超时及 C 的请求/回包守卫。
只有绑定本次启动、配置、策略代次和休眠 epoch 的新鲜完整证明可以续租，即使应用结果不确定也必须单次消费。
休眠撤权、新证明恢复，以及关闭租约时的清理，须与就绪检查和 nft 事件过滤保持一致。
`proxy-assurance-vm` 覆盖监督器冻结/停止、两种 IP 家族的已有 TCP/UDP、双向到期和保留恢复访问。

修改 nftables、systemd 加载或协调逻辑时，运行 `proxy-firewall-vm`、相关完整规则变体、`proxy-control-vm` 和 `proxy-network-vm`。
专用 VM 检查 A/C 表归属、外部集合与有序规则、失败事务、锁竞争及迁移目标名称复用。
控制 VM 检查真实 helper/核心、TUN 表保留、C 阻断与 B 恢复。
完整网络 VM 保留首次加载和漂移恢复覆盖。

[拒绝汇总](operations_CN.md#拒绝计数器)在 [inspect.py](../../system/config/network/diagnostics/inspect.py) 中把已有链和 nft JSON 表达式映射为原因/角色分组。
修改链名、访客接口顺序或源地址/端口表达式时须保持该映射一致。

修改汇总后运行 `proxy-regressions`、`proxy-control-vm` 和 `proxy-guest-vm`。
权限/解析错误须区别于零计数，诊断保持独立于策略执行。

## 链路身份

[default.nix](../../system/config/network/identity/default.nix) 和 [link_identity.py](../../system/config/network/identity/link_identity.py) 使用上游 NetworkManager/systemd/dhcpcd 接口，没有源码补丁。

升级时核对实际 nmcli 属性名及枚举格式、两个主机名开关、stable-id 替换量、DHCP 标识和 IPv6 地址生成。
保留仅含身份字段的备份白名单、私有状态、断开/自动连接守卫及拒绝覆盖手工修改的行为。
重建不自动应用 profile 目标。

扩展 dhcpcd 单接口命令、PID 文件和 reload 时，保留上游生成配置与服务加固。
其 DHCPv6 socket 必须能与 NetworkManager 的 Wi-Fi 客户端共存。
一起运行 `network-identity-config`、`network-identity-vm`、`proxy-control-vm` 和 `proxy-network-vm`。

身份 VM 对每次激活都要求新报文，包括 MAC 不变的连接。
持久化准备和重新授权见[链路身份指南](identity_CN.md)。

## 升级流程

### 源码兼容性检查

[sources.json](../../system/config/network/maintenance/sources.json) 按组件记录维护入口、不变量和回归检查。
[default.nix](../../system/config/network/maintenance/default.nix) 对实际选定包的源码、可用的 vendored 依赖摘要及主要 Nixpkgs 打包文件生成指纹。

正常系统构建要求这些输入匹配配置的源码基线。
即使版本号未变，源码或 recipe 改变仍须检查兼容性。
Mihomo 变化也会选中其 vendored sing-tun 和 SOCKS 组件。

在仓库中生成当前输入清单并查看计划：

```sh
nix eval --json .#nixosConfigurations.lfour.config.my.proxy.upgradeSources \
  > /tmp/network-upgrade-selected.json
python3 system/config/network/maintenance/upgrades.py plan \
  system/config/network/maintenance/sources.json /tmp/network-upgrade-selected.json \
  > /tmp/network-upgrade-plan.json
```

计划列出变化的组件、源码入口、不变量和检查。
可在命令末尾添加 `mihomo sing-tun socks`、`gost`、`clash`、`dns`、`firefox`、`tailscale`、`firewall` 或 `link-identity`，为本地定制变更或未变化的输入显式选择组件。

执行选定检查：

```sh
jq -r '.checks[] | ".#checks.x86_64-linux." + .' /tmp/network-upgrade-plan.json \
  | xargs -r nix build --no-link
```

先比较上游源码与打包配置并完成组件检查，再修改对应基线条目，随后运行 `proxy-upgrade-review` 并构建候选系统。
计划工具不会更新基线。
Schema 要求各组件保留最小源码与回归覆盖，flake 也检查所列检查是否存在。

该门检查源码版本与指纹。
精确补丁锚点、软件包测试及内核/浏览器集成测试检查对应行为。
Recipe 指纹只覆盖主要软件包表达式，不覆盖完整传递构建闭包。
仍需检查 lock 差异中的相关变化，包括部署内核和图形栈。
增加补丁、依赖或入口时同步更新组件清单。

### 升级步骤

1. 保留一个可用的系统 generation，再更新目标 flake input。
2. 比较 lock、Nixpkgs 打包表达式、上游源码和 vendored 依赖的变化。
3. 调整受影响的补丁入口。文件移动时同步更新 fileset 和构建阶段，已由上游修复的补丁可在保留回归检查后移除。
4. 如果配置约定改变，同步 root policy、GUI Merge、健康检查、控制 API 和监听身份。
5. 按下表运行受影响组件的检查，再构建候选系统。刷新、内容审计和启动默认值的变更还需对应的控制与刷新 VM。
6. 部署后重启旧浏览器，执行 `clash-off` 和 `clash-on`，检查就绪状态与实际流量。
7. 将源码基线、自定义清单及相关手册更新为最终配置。

## 验证

配置复制脚本有独立检查：

```sh
python3 tests/deployment/test_deployment.py scripts/push-to-dir.sh
```

它使用临时目录，需要 Python、Bash 和 rsync。

以下检查通过 [tests/checks.nix](../../tests/checks.nix) 接入 `checks.x86_64-linux.*`。
按变更涉及的组件选择即可。
VM 检查运行在隔离的虚拟机中，硬件加速需要 KVM 权限。

| 检查 | 范围 |
|---|---|
| `proxy-control` | 临时文件验证快照授权、过期、重启、恶意输入和权限，以及 B 租约证明单次消费、休眠/代次拒绝 |
| `proxy-refresh` | 离线审计、有界 HTTP 检查、本地更新状态和通知去重 |
| `proxy-source` | 登记来源下载与渲染、只读比较、自动批准绑定、重启导入、候选预检及运行恢复 |
| `proxy-source-config` | 来源与浏览器配置、私有 DNS 路径、下载和策略能力分离 |
| `proxy-refresh-vm` | 真实 auto/enforce helper 与核心：健康接受、坏候选回退、pin 重启、回滚及 desired-state 恢复 |
| `proxy-upgrade-review` | 所选源码/依赖/recipe 基线、组件覆盖、检查名称及源码兼容性检查。输入检查也接入正常系统构建 |
| `network-identity-config` | 身份迁移边界、实际 nmcli 解析/主机名恢复、有线变体、服务范围及持久化。正常系统构建也校验目标 |
| `network-identity-vm` | 真实有线/Wi-Fi DHCPv6 共存、新双栈身份报文、UUID/epoch/随机轮换、稳定/固定/硬件行为、profile 优先级、恢复及宿主密钥保留 |
| `proxy-regressions` | 健康、状态、规则、DNS 租约、有界客户端 DNS 重试、预算及监督器 |
| `proxy-profile`、`proxy-gui` | 生成配置变体、策略/模板一致、GUI 默认、进程 wrapper 与桌面命令拦截 |
| `proxy-gui-vm` | WebKit 以改名账户和非默认 home 自动化运行未修改的生产 GUI：mode/TUN/System Proxy/PAC/代理守护静默丢弃、OS 状态与核心 PID 保持、无控制错误提示、GUI 退出及 root 路由事务 |
| `proxy-routing` | 本次启动的不可变选择、完整证明与 epoch 绑定、准备失败、清理后的状态及真实 DNS 截止/回退拒绝 |
| `proxy-routing-vm` | 登记来源与生产命名空间：双向子模式切换、原始规则恢复、物理对端仅节点外发、真实 DNS/来源刷新、旧回复、访客拒绝、幂等、并发选择、off/on、重启及内核到期撤权 |
| `proxy-single-egress-vm` | 两个可控出口：rules 独立选组复现、固定叶子的双栈 TCP/UDP 与 DNS 观测、实际 Firefox 和严格入口、UDP 不支持/故障、旧流、并发刷新、上行/节点故障，以及路由与租约回归 |
| `proxy-rules`、`proxy-egress-rules` | 构建期 LKL nft 解析完整兼容/受限模式规则 |
| `proxy-firewall-vm` | A/C 表归属、外部表保留、加载被拒绝时的回滚、迁移锁、幂等及迁移目标名称复用 |
| `proxy-dns-strict-rules` | LKL 解析完整严格 DNS 防火墙规则 |
| `proxy-dns-config` | 默认、严格、双栈、IPv6-only 和 HTTP-only 配置中生成的 DNS 与验墙脚本语法 |
| `proxy-dns-strict-vm` | 真实 helper/核心、共享 DNS/代理过滤、严格故障/override/租约及残留集合拒绝、私网 split 权限与字面门户恢复 |
| `proxy-assurance-vm` | A/B 本地 DNSSEC、bogus/CD 与 fake-IP/真实目标、B 代理 DNS 不受 A 错误答案影响、独立 DNS 饱和/恢复，以及冻结/停止/休眠时 B 租约对双栈 TCP/UDP 的撤权 |
| `proxy-guest-config`、`proxy-guest-rules` | 生产/独立账户配置与 rootless 启动器语法。LKL 中的完整混合角色规则。正常系统构建也要求访客校验通过 |
| `proxy-guest-vm` | 三个真实转发网络、DHCP/DNS、IPv4/ULA、伪造源地址、实际 pasta/RootlessKit/subuid socket、fake-IP 拒绝、旧 DNS conntrack、双向撤销以及核心存活时的 C |
| `proxy-hermes-containers-vm` | 真实旧/新 Docker、socket ACL、离线镜像、账户授权、TCP/UDP、未支持 IPv6 拒绝、Hermes 宿主代理、A/B/C 与恢复 |
| `proxy-core-netns-vm` | 独立核心 namespace 原型：TUN/veth、双栈节点、原生/SOCKS UDP、DNS/fake-IP、mark/raw 包、宿主策略归属、撤权与重启 |
| `proxy-egress-config` | 规范 CIDR 与授权/时间目标范围。接入正常系统构建 |
| `proxy-egress-vm` | Tailscale VM 的受限策略版本。双栈私网/多播/DNS/DoT/NTS、UID/接口/端口拒绝、已有连接撤权与 C |
| `proxy-egress-control-vm` | 真实生产 TUN/NAT 之前的受限策略，包含私网允许与拒绝 |
| `tailscale-transport` | 所选上游 Linux bypass 常量与预期的 magicsock/STUN socket 打标路径。正常系统构建也强制执行 |
| `proxy-tailscale-vm` | 所选真实 Tailscale CLI、隔离双栈 STUN、双上行、socket/packet/UID 约束、B/C 恢复及远控端口策略 |
| `proxy-control-config` | 生产/测试身份、helper 能力与监听一致 |
| `proxy-core` | 定制核心构建，以及 parser、router、目标与缓存、实际适配器叶节点、DIRECT、UDP、DNS、SOCKS 报文和 sing-tun 区间端点的 Go 测试 |
| `proxy-target-vm` | 真实 helper/核心，宿主与节点 loopback 陷阱，HTTP/CONNECT/SOCKS、内部域名固定 IP、窄远程授权、Rule/Global 与 A/C 行为 |
| `proxy-gost` | 定制 relay 构建及 vendored HTTP 测试 |
| `browser-privacy` | 最终原生浏览器 policy lock 与离线启动 |
| `browser-sandbox-vm` | 真实 Wayland Firefox、系统根 TLS 与无效证书拒绝、父进程隔离、音频、门户授权、链接复用、代次/重启及总线代理故障阻断 |
| `browser-ech-vm` | 真实 Firefox/helper/核心、原生 HTTPS 发现、ECH 与 GREASE、密钥/无 ECH 重试、不可信拒绝、证书错误、掩护名不改目标及 A/C 行为 |
| `browser-ech-uniform-vm` | 在 `proxyOnly=true` 下验证相同真实浏览器路径及策略消费 |
| `proxy-managed-netns-vm` | 登记 TLS 来源、拒绝候选后保持核心、授权与已有 TLS 连接，自动轮换与结构阻断，以及生产核心 namespace 的完整就绪、严格入口、C 撤权和来源切换 |
| `proxy-control-vm` | 真实 helper/launcher、生产 netns/宿主权限边界、localhost 双栈 TCP/UDP、地址冲突、断链/恢复、授权、DNS、TUN 与 C→B 恢复 |
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

`proxy-control-vm` 与 `proxy-managed-netns-vm` 使用生产 helper 和核心命名空间。
`proxy-network-vm` 覆盖更广的故障注入，`proxy-core-netns-vm` 使用独立原型拓扑。
内存有限时，逐个构建 VM 目标，并使用 `--max-jobs 1`。
激活后在实际宿主上检查就绪状态和应用流量。
