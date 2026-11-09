# 补丁与自定义维护

[English](../maintenance.md) | 简体中文 | [文档目录](README_CN.md)

## 构建接入

版本由 [flake.lock](../../flake.lock) 决定，不跟随桌面自更新。稳定 Nixpkgs 提供 Gost、dnsmasq、Unbound、Firefox；unstable 提供 Mihomo、Clash Verge GUI 及独立打包的 helper。当前锁定组合为 Mihomo 1.19.33、vendored MetaCubeX sing-tun 0.4.28、Gost 3.3.2、GUI 2.5.2、helper 2.3.3、dnsmasq 2.93、Unbound 1.26.2。GUI/helper/core 版本相互独立。

[clash-verge.nix](../../system/programs/clash/clash-verge.nix) 构建定制核心/helper 并装入 GUI 包，[gost-relay.nix](../../system/programs/systemd/gost-relay.nix) 构建定制 relay。两者以 `lib.fileset.toSource` 只收集 patcher、Go 代码和测试，避免无关仓库改动重建 Go 包。**新增补丁文件或测试时必须更新对应 fileset**，否则文件即使在仓库中也不会进入 builder。

Go patcher 在 `buildGoModule` 填充 `vendor/` 后的 `postConfigure` 执行；Rust helper 在 `postPatch` 追加修改，保留继承的 Nixpkgs 处理。锚点要求精确匹配，升级失败时应比较上游行为，不能放宽匹配或跳过补丁。

## 自定义清单

| 对象 | 仓库入口 | 目的 / 升级关注点 |
|---|---|---|
| Mihomo parser | [patch_mihomo.py](../../system/programs/systemd/netsec/patch_mihomo.py)、[managed_profile.go](../../system/programs/systemd/netsec/managed_profile.go) | 最终受管策略、摘要、已知旧配置规范化，拒绝无条件 DIRECT 兜底 |
| Mihomo controller | [managed_route.go](../../system/programs/systemd/netsec/managed_route.go)、[policy_middleware.go](../../system/programs/systemd/netsec/policy_middleware.go) | 同 schema PATCH 解码与运行时写白名单 |
| Mihomo 出站 / DNS / 日志 | [target_guard.go](../../system/programs/systemd/netsec/target_guard.go)、`patch_mihomo.py` | 域名验证/固定 IP、可选直拨拒绝、rule DNS metadata、Global 实际链路日志、只用快照 geodata |
| Mihomo SOCKS parser | [patch_socks.py](../../system/programs/systemd/netsec/patch_socks.py)、[socks_addr_test.go](../../system/programs/systemd/netsec/socks_addr_test.go) | 整数偏移避免长域名字节运算溢出 |
| Vendored sing-tun | [patch_sing_tun.py](../../system/programs/systemd/netsec/patch_sing_tun.py) | nft 最大地址区间端点，以及 TUN-bound OUTPUT TCP DNAT |
| Vendored Gost HTTP | [patch_gost.py](../../system/programs/systemd/netsec/patch_gost.py)、[gost_http.go](../../system/programs/systemd/netsec/gost_http.go) | 兼容裸数字 IPv6 CONNECT，保留 Go HTTP 解析 |
| Clash Verge helper | [patch_clash_service.py](../../system/programs/clash/patch_clash_service.py) | 固定 root 执行、日志及 socket/启动文件操作 |
| Clash Verge 打包/启动 | [clash-verge.nix](../../system/programs/clash/clash-verge.nix)、[核心启动器](../../system/programs/clash/clash-verge-core-launcher.sh)、[stage 工具](../../system/programs/clash/clash-verge-stage.py)、[GUI 启动器](../../system/programs/clash/clash-verge-gui.py)、[GUI defaults](../../system/programs/clash/clash-verge-defaults.sh) | 授权快照、降权、singleton 处理、HTTPS 延迟默认；复用原 GUI 二进制 |
| dnsmasq | [network.nix](../../system/config/network.nix) 内联替换 `src/forward.c` | 返回 strict-order 终端 UDP SERVFAIL/REFUSED，保留 DNSSEC EDE |
| Firefox 包 | [firefox.nix](../../home/programs/firefox/firefox.nix)、[guard.py](../../home/programs/firefox/guard.py) | 不可变 policy/AutoConfig/字体及受管启动；没有 Gecko 源码补丁 |
| Unbound / Chrony / 网络 | [network.nix](../../system/config/network.nix)、[network-time.nix](../../system/config/network-time.nix)、[systemd 模块](../../system/programs/systemd) | 声明策略和仓库自有协调器；没有本地 Unbound/Chrony 源码补丁 |
| Tailscale 传输 | [tailscale-transport.nix](../../system/config/tailscale-transport.nix)、[tailscale_contract.py](../../system/programs/systemd/netsec/tailscale_contract.py) | 正常系统构建强制检查上游 socket mark 约定；升级复核 mark/后备/socket 变化并跑真实 CLI VM；没有 Tailscale 源码补丁 |
| 直连例外 | [hardening-options.nix](../../system/config/hardening-options.nix)、[network.nix](../../system/config/network.nix)、[egress_policy.py](../../system/programs/systemd/netsec/egress_policy.py) | 主体/socket 身份、NAT 前授权、规范 CIDR 校验、时间/DoT 范围及 root/GUI 共享私网排除；比较兼容和受限模式 |
| Sunshine | [sunshine.nix](../../system/programs/sunshine.nix) | 随图形会话提供 Desktop，管理仅本机、关闭 UPnP；保留 CUDA，以禁止本地编译的方式验证所配置 CUDA 缓存中的精确包 |

## Mihomo

共享域名过滤位于 [dns_filter.go](../../system/programs/systemd/netsec/dns_filter.go)、[target_guard.go](../../system/programs/systemd/netsec/target_guard.go) 和 [dns-blocklist.nix](../../system/config/dns-blocklist.nix)。`dns.newHandler` 与 `Resolver.ExchangeContext` 接入点需保持在 hosts、fake-IP、缓存答案及引导上游查询之前，覆盖非地址 RR 类型；应用目标先过滤后缀，再判断私网域名豁免。保留与 dnsmasq 一致的最长后缀、同精度例外优先语义，拒绝不可读/无效名单。过滤代码和测试须加入包 fileset，并保留 `./dns` Go 测试。

`patch_mihomo.py` 接入最终 `ParseRawConfig`、controller router/PATCH、direct adapter、DNS metadata/dialer、TCP dial、UDP mapping、geodata downloader 与 Global 路由日志。升级须检查全部入口，parser 能编译不证明实际拨号和写 API 仍经过预期约束。

Root 策略来自 [proxy-profile.nix](../../system/config/proxy-profile.nix)，[cvr-merge.nix](../../home/config/cvr-merge.nix)、controller 输出及 [health.py](../../system/programs/systemd/netsec/health.py) 必须一致。新增 `RawTun` 字段须在 schema coverage 测试显式分类，检查监听/隧道字段、默认值、别名及 `GET /configs` 暴露值。保留重复键/非规范 PATCH 拒绝，以及同份解码对象的验证/应用。

目标检查位于路由/嗅探后、真实拨号前，远程代理也须保持已验证 IP。复用 UDP association 时检查新目的映射，不为每个后续包重解析。`LFRuleDNS` 区分按规则 DNS 与内部引导，避免 `proxyOnly` 豁免应用 DNS。Metadata、适配器命名和组叶子变化需对照直拨测试。

生产 helper 必须设置 `LF_MANAGED_PROXY_POLICY`；未设置时保留上游行为，供开发 fixture 使用。策略摘要与 root 快照摘要分开；geodata 下载限制同时覆盖 parser 验证和损坏/缺失资源替换。

`patch_socks.py` 修改 `adapter/inbound/util.go` 运算并复制边界测试，头部记录上游参考。上游实现修正后，应比较长域名解码和畸形输入行为，保留回归覆盖，只移除冗余 patch 调用与 payload 项。

受管 fake-IP 必须保留地址合成和共享过滤，同时完整转发 HTTPS/SVCB 参数。Rule 和统一代理均保持关闭嗅探，旧 profile 继承 root 策略，避免 ECH public name 替换请求目标。

## Mihomo 内的 sing-tun

目标是 `vendor/github.com/metacubex/sing-tun`，不是独立安装的软件包；更新 Mihomo 可能同时改变它。检查 `go.mod`、replace 指令与实际 vendor 树。

补丁包含两个独立部分：

1. `redirect_nftables_exprs.go`：最大 IPv4/IPv6 地址没有有效后继，省略区间结束标记，避免倒置或空 nft interval。Patcher 记录 SagerNet 参考，移除前须比较 MetaCubeX fork 的实际代码。
2. `redirect_nftables_rules.go`：受管非 mark auto-redirect 模式中，OUTPUT TCP DNAT 使用 TUN 自身地址代替 loopback，使 `SO_BINDTODEVICE(TUN)` 在 `route_localnet=0` 下正常。PREROUTING 及其它模式保留上游行为。

核对双栈、排除网段、hook 条件和 redirect port；C 自测应答例外与健康探针依赖该路径。源码匹配和编译不能证明内核重选路，升级 sing-tun 时需包括控制/网络 VM。

## Gost

补丁作用于 `pkgs.gost` 的 vendored `github.com/go-gost/x/handler/http`。升级同时检查 Gost 和 `go-gost/x` 版本。`lfReadRequest` 只改写数字 IPv6、十进制非零端口的裸 CONNECT 目标；`net/http.ReadRequest` 保留 HTTP 校验，返回的 reader 保留已缓冲隧道数据。

[gost_http_test.go](../../system/programs/systemd/netsec/gost_http_test.go) 应保留带括号 IPv6、普通请求、非法端口/framing、立即到达 payload 的测试。Relay 唯一监听、旧进程回收和资源限制也要保留；parser 修正不能替代生命周期保护。实际服务使用 `my.proxy.gostPackage`，不能换成 PATH 中未打补丁的包。

## Clash Verge helper 与启动器

GUI `.unwrapped` 有意复用上游并断言相等。自定义位于独立 Rust helper、安装的 wrapper 和运行脚本。检查 Nixpkgs helper derivation 和继承补丁；覆盖 `postPatch` 或复制错 helper 会丢失集成行为。

Helper patch 涉及 `src/core/manager.rs`、`logger.rs`、`reconcile.rs`。升级时枚举所有执行、log writer、socket chmod/unlink 和已存进程恢复入口。受管执行须解析到 `LF_CLASH_CORE_PATH`，文件/祖先 root 所有，允许 root 所有 sticky store 祖先。日志始终使用私有服务目录，客户端路径/PID 不得转为 root 文件或进程操作。

[runtime.nix](../../system/programs/clash/runtime.nix)、[identity.nix](../../system/programs/clash/identity.nix)、launcher 和 stage 工具须同步。顶层 runtime 组须匹配 systemd，避免重启递归改所有权。可写核心缓存由独立 tmpfiles 创建，不加入 root helper 的 `StateDirectory`。快照跨 helper 重启保留，授权/验证日志私有，并从真实 helper 启动的核心读取五类 capability 集。

当前 Nixpkgs 以 `.verge-mihomo-wrapped` 暴露底层核心，包将公开 `verge-mihomo` 替换为授权启动器。GUI/打包更新需检查 wrapper 布局、`clash-on/off` 使用的 GUI 进程身份和旧同用户 singleton 清理，防止保留旧包路径。HTTPS delay 默认通过可写偏好设置，不重新编译 GUI。

## DNS 与浏览器集成

Dnsmasq patch 是 `network.nix` 的内联 `--replace-fail`。Strict-order 重试中，最后服务器的 UDP SERVFAIL/REFUSED 应返回客户端并保留 EDE；使用 sent-to 数组索引，因为端点可能重复。对照新 `forward.c` 检查终端错误、重试和重复服务器，并保留网络 VM 的真实 DNS 应答测试。

Unbound 使用未改源码的 `pkgs.unbound-with-systemd`。未来 override 必须保留 `--enable-systemd`，unit 为 `Type=notify`。DNSSEC/CD、持久化锚初始化、TLS 认证、答案过滤、陈旧缓存上限和探针结论应与 [DNS](dns_CN.md) 一致；Chrony 仍须强制 NTS 和证书时间校验。

Firefox 在打包层加入不可变 policy、AutoConfig、字体和 preflight。Home Manager 最终包/wrapper 或偏好语义变化可能不会触发源码 patch 失败，需用 `browser-privacy` 检查最终启动器、桌面/MIME、policy lock、DoH/代理值、沙箱环境及旧 profile/进程。

默认 HTTP 和可选透明路径还需运行 `browser-ech-vm`，复核所选 Firefox 的 CONNECT 门、原生 type-65 API 和 NSS 重试/证书行为。自动化的原生 DNS 测试开关只放在临时 profile，区分认证后不带 ECH 重试与证书验证失败。[浏览器手册](browser_CN.md#ech-发现与代理兼容)介绍这些路径；本地 CA 与密钥只属于隔离 fixture。

`dnsEncryption="strict"` 在协调器、broker 和静态防火墙中必须一致：不能自动或强制回退，不授予明文租约，不安装后备集合放行规则。保留已批准 split DNS、MagicDNS 和字面门户访问；两种传输策略都要验证，默认恢复预算仍是独立行为。

## 其它桌面自定义

以下项目独立于网络安全策略，更新对应包时也需维护：

| 入口 | 自定义与维护 |
|---|---|
| [overlays/default.nix](../../overlays/default.nix) | Niri SHM screencast 固定 hash diff，此 override 禁用包检查；移除前比较上游并测试录屏 |
| [dsh-desktop-appimage.nix](../../overlays/dsh-desktop-appimage.nix) | ASAR 菜单、内置 pnpm git 安装、解包/执行位和 Landlock 启动器；逐版本检查 JS 锚点、pnpm、sharp WASM 与可执行资源 |
| [bilibili-appimage.nix](../../overlays/bilibili-appimage.nix) | 桌面命令替换与 Wayland 启动器；核对 desktop 布局和沙箱参数 |
| [wemeet-cursor-hook.nix](../../overlays/wemeet-cursor-hook.nix) | 固定外部 cursor hook，源码构建；合成器更新后检查 preload/光标 |
| [layout.nix](../../system/config/layout.nix) | 将 Rime Ice suggestion YAML 安装为默认；核对上游文件名 |

## 升级流程

1. 确认所属 flake input，在仓库更新目标 input，比较 lock、Nixpkgs 包表达式、源码和 vendor 变化，保留可用 generation。
2. 比较上述全部受影响入口，重定位本地行为或在保留回归覆盖后移除冗余修复；保持精确锚点，文件移动时更新 fileset/构建阶段。
3. 约定变化时同步 root policy、Merge、health/controller、监听身份和 VM fixture；小控制 VM 继续使用生产身份和包。
4. 修改完成后统一运行相关集合，构建候选系统而不激活，通过后再部署。激活后重启旧浏览器，主动 `clash-off` → `clash-on` 导入，再检查就绪和真实流量。
5. 更新清单、版本及相关手册，使其与最终实现一致。

## 验证

部署脚本在仓库中通过 `python3 tests/test_deployment.py scripts/push-to-dir.sh` 单独验证，以临时目录检查构建输入保留、文档排除、权限和输入缺失行为，无须 sudo 或宿主目标。需要 Python、Bash、rsync 和脚本使用的标准命令行工具；由于 `scripts/` 不部署，该检查与 flake checks 分开。

以下为 `checks.x86_64-linux.*` 属性。Nix 构建通常使用本机 daemon，测试本身不要求宿主 root 网络操作；VM 使用隔离内核，可能需要本机 KVM 访问。首次依赖构建成本较高，按受影响行为选择后统一运行。

| 检查 | 范围 |
|---|---|
| `proxy-control` | 临时文件验证快照授权、过期、重启、恶意输入和权限 |
| `proxy-regressions` | 健康、状态、规则、DNS 租约、预算及监督器 |
| `proxy-profile`、`proxy-gui` | 生成配置变体、策略/模板一致、GUI 默认与进程 wrapper |
| `proxy-rules`、`proxy-egress-rules` | 构建期 LKL nft 解析完整兼容/受限模式规则 |
| `proxy-dns-strict-rules` | LKL 解析完整严格 DNS 防火墙规则 |
| `proxy-dns-config` | 默认/严格 DNS 协调器生成脚本语法 |
| `proxy-dns-strict-vm` | 真实 helper/核心、共享 DNS/代理过滤、严格故障/override/租约及残留集合拒绝、私网 split 权限与字面门户恢复 |
| `proxy-egress-config` | 规范 CIDR 与授权/时间目标范围；接入正常系统构建 |
| `proxy-egress-vm` | Tailscale VM 的受限策略版本；双栈私网/多播/DNS/DoT/NTS、UID/接口/端口拒绝、已有连接撤权与 C |
| `proxy-egress-control-vm` | 真实生产 TUN/NAT 之前的受限策略，包含私网允许与拒绝 |
| `tailscale-transport` | 所选上游 Linux bypass 常量与已审查的 magicsock/STUN socket 打标路径；正常系统构建也强制执行 |
| `proxy-tailscale-vm` | 所选真实 Tailscale CLI、隔离双栈 STUN、双上行、socket/packet/UID 约束、B/C 恢复及远控端口策略 |
| `proxy-control-config` | 生产/测试身份、helper 能力与监听一致 |
| `proxy-core` | 定制核心构建和 parser/router/目标/direct/UDP/DNS/SOCKS Go 测试 |
| `proxy-gost` | 定制 relay 构建及 vendored HTTP 测试 |
| `browser-privacy` | 最终原生浏览器 policy lock 与离线启动 |
| `browser-ech-vm` | 真实 Firefox/helper/核心、原生 HTTPS 发现、ECH 与 GREASE、密钥/无 ECH 重试、不可信拒绝、证书错误、掩护名不改目标及 A/C 行为 |
| `proxy-control-vm` | 真实生产 helper/launcher、能力、授权、DNS、TUN/C 恢复 |
| `proxy-network-vm` | 广泛内核故障注入：模式、DNS、租约、休眠、访客、TPROXY 和恢复 |

例如将相关包与快速检查合并构建：

```sh
nix build --no-link .#checks.x86_64-linux.proxy-core \
  .#checks.x86_64-linux.proxy-gost .#checks.x86_64-linux.proxy-profile \
  .#checks.x86_64-linux.proxy-control .#checks.x86_64-linux.proxy-control-config \
  .#checks.x86_64-linux.proxy-regressions .#checks.x86_64-linux.proxy-gui
nix build --no-link .#checks.x86_64-linux.proxy-control-vm \
  .#checks.x86_64-linux.proxy-network-vm
nix build --no-link .#checks.x86_64-linux.proxy-egress-config \
  .#checks.x86_64-linux.proxy-egress-rules .#checks.x86_64-linux.proxy-egress-vm \
  .#checks.x86_64-linux.proxy-egress-control-vm
nix build --no-link .#nixosConfigurations.lfour.config.system.build.toplevel
```

广泛 VM 含有意的模拟/root-core fixture，生产启动权限由小控制 VM 验证；两者均不改宿主路由/防火墙。包测试证明 parser/policy，不能替代内核路径测试。宿主激活后另需特权观测实际身份/能力、监听所有者、快照状态与 A → B/C 转换。
