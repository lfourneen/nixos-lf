# 网络补丁与自定义维护

[English](../maintenance.md) | 简体中文 | [文档目录](README_CN.md)

这套网络栈对 Mihomo、`sing-tun`、Gost、Clash Verge 和 `dnsmasq` 打了本地补丁，外加一层受管 Firefox 包装。下面列出这些定制的源码入口，以及升级依赖前要核对的兼容性要点。

## 构建接入

[flake.lock](../../flake.lock) 固定了上游源码：稳定版 Nixpkgs 提供 Gost、`dnsmasq`、Unbound 和 Firefox，Unstable 提供 Mihomo、Clash Verge GUI 和独立打包的 helper（三者版本各自独立）。

[clash-verge.nix](../../system/programs/clash/clash-verge.nix) 构建定制核心/helper 并装进 GUI 包，[package.nix](../../system/programs/gost/package.nix) 构建定制 relay。两者都用 `lib.fileset.toSource` 只收集 patcher、Go 代码和测试，避免改个无关文件就重编 Go 包——所以**新增补丁文件或测试时千万记得同步更新对应 fileset**，不然文件不会进 builder。Go patcher 在 `buildGoModule` 填好 `vendor/` 后的 `postConfigure` 跑，Rust helper 则在 `postPatch` 接着 Nixpkgs 的补丁往后打。锚点要求精确匹配，升级报锚点失败时去比对上游行为变化，别靠放宽匹配或跳过补丁糊弄过去。

## 自定义清单

| 对象 | 仓库入口 | 目的 / 升级关注点 |
|---|---|---|
| Mihomo parser | [patch_mihomo.py](../../system/programs/clash/mihomo/patches/patch_mihomo.py)、[managed_profile.go](../../system/programs/clash/mihomo/config/managed_profile.go) | 最终受管策略、摘要、已知旧配置规范化，拒绝无条件 `DIRECT` 兜底 |
| Mihomo controller | [managed_route.go](../../system/programs/clash/mihomo/controller/managed_route.go)、[policy_middleware.go](../../system/programs/clash/mihomo/controller/policy_middleware.go) | 静默丢弃 mode/TUN/重载与有限的运行时写白名单 |
| Mihomo 出站 / DNS / 日志 | [target_guard.go](../../system/programs/clash/mihomo/guard/target_guard.go)、`patch_mihomo.py` | 目标验证/固定 IP、本地与远程叶节点授权、可选直拨拒绝、rule DNS metadata、Global 实际链路日志、只用快照 geodata |
| Vendored `sing-tun` | [patch_sing_tun.py](../../system/programs/clash/mihomo/patches/patch_sing_tun.py) | TUN-bound OUTPUT TCP DNAT 与访客 TPROXY socket 保留 |
| Vendored Gost HTTP | [patch_gost.py](../../system/programs/gost/patches/patch_gost.py)、[gost_http.go](../../system/programs/gost/src/gost_http.go) | 兼容裸数字 IPv6 `CONNECT`，保留 Go HTTP 解析 |
| Clash Verge helper | [patch_clash_service.py](../../system/programs/clash/service/patch_clash_service.py) | 内核 peer 身份约束生命周期、固定 root 执行、日志及 socket/启动文件操作 |
| Clash Verge 打包/启动 | [clash-verge.nix](../../system/programs/clash/clash-verge.nix)、[核心启动器](../../system/programs/clash/service/clash-verge-core-launcher.sh)、[stage 工具](../../system/programs/clash/service/clash-verge-stage.py)、[GUI 启动器](../../system/programs/clash/gui/clash-verge-gui.py)、[GUI defaults](../../system/programs/clash/gui/clash-verge-defaults.sh)、[控制 shim](../../system/programs/clash/gui/control_shim.sh) | 授权快照、降权、singleton 处理、HTTPS 延迟默认、OS 代理写入拦截与仅控制字段变化时复用快照 |
| `dnsmasq` | [default.nix](../../system/config/network/network.nix) 内联替换 `src/forward.c` | 返回 `strict-order` 终端 UDP `SERVFAIL`/`REFUSED`，保留 DNSSEC EDE |
| Firefox 包 | [firefox.nix](../../home/programs/firefox/firefox.nix)、[guard.py](../../home/programs/firefox/guard.py)、[sandbox.py](../../home/programs/firefox/sandbox.py)、[scope.c](../../home/programs/firefox/scope.c) | 不可变 policy/AutoConfig/字体、预检、外层命名空间、过滤总线、门户身份及 Landlock/seccomp（无 Gecko 源码 patch） |
| Unbound / Chrony / 网络 | [default.nix](../../system/config/network/network.nix)、[time.nix](../../system/config/network/time.nix)、[网络模块](../../system/config/network) | 声明策略和仓库自有协调器（无本地 Unbound/Chrony 源码补丁） |
| 链路身份 | [link-identity.nix](../../system/config/network/identity/link-identity.nix)、[wired.nix](../../system/config/network/identity/wired.nix)、[link_identity.py](../../system/config/network/identity/link_identity.py)、[partition.nix](../../system/hardware/partition.nix) | 协调 profile 身份、私有迁移/恢复、原生有线生命周期与 DHCPv6 共存（无上游源码 patch） |
| 防火墙生命周期 | [default.nix](../../system/config/network/network.nix)、[coordinator.nix](../../system/config/network/proxy/coordinator.nix)、[bootstrap.nix](../../system/config/network/firewall/bootstrap.nix) | 精确表归属、持锁原子替换、失败保留和显式表迁移 |
| Tailscale 传输 | [transport.nix](../../system/config/network/tailscale/transport.nix)、[tailscale_contract.py](../../system/config/network/tailscale/tailscale_contract.py) | 正常系统构建强制检查上游 socket mark 约定；升级时检查 mark/后备/socket 变化并跑真实 CLI VM（无 Tailscale 源码补丁） |
| 直连例外 | [options.nix](../../system/config/network/options.nix)、[default.nix](../../system/config/network/network.nix)、[egress_policy.py](../../system/config/network/firewall/egress_policy.py) | 主体/socket 身份、NAT 前授权、规范 CIDR 校验、时间/DoT 范围及 root/GUI 共享私网排除（比较兼容和受限模式） |
| 访客信任 | [policy.nix](../../system/config/network/guests/policy.nix)、[guest_policy.py](../../system/config/network/guests/guest_policy.py)、[socket-guest.nix](../../system/config/network/dns/socket-guest.nix)、[docker.nix](../../system/programs/virtualisation/docker.nix) | 接口/源角色、规范授权、主/委派 UID 归属、NAT 前真实地址 DNS、双向撤销及 rootless 启动器 |
| Sunshine | [sunshine.nix](../../system/programs/sunshine.nix) | 随图形会话提供 Desktop，管理仅本机、关闭 UPnP；保留 CUDA，以禁止本地编译的方式验证所配置 CUDA 缓存中的精确包 |

## Mihomo

共享域名过滤放在 [dns_filter.go](../../system/programs/clash/mihomo/guard/dns_filter.go)、[target_guard.go](../../system/programs/clash/mihomo/guard/target_guard.go) 和 [blocklist.nix](../../system/config/network/dns/blocklist.nix)。`dns.newHandler` 与 `Resolver.ExchangeContext` 的接入点必须留在 hosts、fake-IP、缓存答案和引导上游查询前面（覆盖非地址 RR 类型），应用目标也得先过后缀过滤再看私网域名豁免；保留与 `dnsmasq` 一致的最长后缀、同精度例外优先语义，拒绝不可读/无效名单，记得把过滤代码和测试加进 fileset 并留着 `./dns` Go 测试。

`patch_mihomo.py` 接入最终 `ParseRawConfig`、controller router/`PATCH`、direct adapter、DNS metadata/dialer、TCP dial、实际 adapter leaf、UDP mapping、geodata downloader 与 Global 路由日志，升级版本时这些入口**全都要核对**，集成测试会直接打真实拨号和控制写接口。

Root 策略由 [profile.nix](../../system/config/network/proxy/profile.nix) 生成，[cvr-merge.nix](../../home/config/clash/cvr-merge.nix)、controller 输出和 [health.py](../../system/config/network/proxy/health.py) 必须保持一致。上游新增 `RawTun` 字段要在 schema coverage 测试里显式分类（检查监听/隧道字段、默认值、别名、`GET /configs` 暴露值，同时保留重复键/非规范 `PATCH` 拒绝以及同份解码对象的验证/应用）。

目标检查在路由/嗅探之后、真实拨号之前跑：已检查 IP 经远程代理也保持固定，内部后缀与字面 IP 也须过叶子授权，而且检查必须落在**实际选中的子节点**上，不能先查 `Unwrap` 再重新拨代理组。复用 UDP association 时保留实际叶节点与已验证目标来源，检查新映射，缓存映射每包重查入口、端口和授权但不重解析。

开启本地 DNSSEC 时，[validated_answers.go](../../system/programs/clash/mihomo/guard/validated_answers.go) 限制并合并公网拨号目标查询：答案复用不得超过验证器 TTL，缓存和在途工作按策略隔离（换策略使旧工作失效，单调用者取消不殃及其他消费者），保留不同名称上限和 A/AAAA 共用截止，以及过期、零 TTL、单族失败和策略替换回归测试。同时保留 mapped IP、元数据、全 RFC 6052 前缀长度、自定义 fake-IP 与基础设施范围测试（`LFRuleDNS` 区分按规则 DNS 与内部引导，防止 `proxyOnly` 豁免应用 DNS；metadata、适配器命名和组叶子变化要对照直拨测试）。受管模式依赖 `LF_MANAGED_PROXY_POLICY`（不设时保留上游行为供开发 fixture 用，生产 helper 必须带上），策略摘要与 root 快照摘要分开，geodata 下载限制覆盖 parser 验证和损坏/缺失库替换。

受管 fake-IP 必须完整转发 HTTPS/SVCB 参数同时保留地址合成和共享过滤，Rule 和统一模式都关掉嗅探，旧 profile 继承 root 策略以免 ECH public name 顶替请求目标。可选访客 TPROXY 入口在 `listener/listener.go` 与 `listener/tproxy` 单独处理绑定及 IPv4 socket 选择，更新时保留 `allow-lan=false` 下的 loopback 行为、TPROXY 被动身份检查和真实 TCP/UDP 访客测试。

## Mihomo 内的 sing-tun

补丁打在 Mihomo 内嵌的 `vendor/github.com/metacubex/sing-tun` 上（不是独立安装的 sing-tun 包），所以哪怕没动 sing-tun 设置，升 Mihomo 也可能碰到它——检查 `go.mod`、`replace` 指令和实际 vendor 树。补丁在 `redirect_nftables_rules.go` 里有两块：

1. 受管非 mark `auto-redirect` 模式下，OUTPUT TCP DNAT 改用 TUN 自身地址代替 loopback，让 `SO_BINDTODEVICE(TUN)` 在 `route_localnet=0` 下正常（其它模式保持上游）。
2. `PREROUTING` 排除由自有 B 模式访客 TPROXY 规则打上 `0x233` mark 的报文，保住透明 socket 和原始目标（该 mark 与 `network.nix` 共享，普通访客无法在线上伪造宿主 packet mark）。

升级时核对双栈、排除网段、hook 条件、访客 mark、redirect port 以及优先级 100 的访客 mark 查表规则（须早于 TUN 规则），**C** 自测应答例外和健康探针全靠它，控制/网络 VM 会测内核重选路。

## Gost

补丁打在 `pkgs.gost` 的 vendored `github.com/go-gost/x/handler/http` 上，升级时同时看 Gost 和 `go-gost/x` 版本。`lfReadRequest` 只改写数字 IPv6、十进制非零端口的裸 `CONNECT` 目标，`net/http.ReadRequest` 保留 HTTP 校验且返回的 reader 保留已缓冲隧道数据。[gost_http_test.go](../../system/programs/gost/tests/gost_http_test.go) 要保留带括号 IPv6、普通请求、非法端口/framing 和立即到达 payload 的测试，relay 唯一监听、旧进程回收和资源限制也都要保留，实际服务用 `my.proxy.gostPackage` 而不是 `PATH` 里未打补丁的包。

## Clash Verge helper 与启动器

GUI 直接复用 Nixpkgs 的 `.unwrapped` derivation（本地或二进制缓存有时直接复用，但 Nixpkgs 依赖一变就算 GUI 版本没变路径也会变），`clash-verge-service-ipc` 则是独立打包的 Rust 后台服务，本地补丁版和 Mihomo 缺缓存时从源码编译、由 wrapper 装入补丁版。升级时也别忘了查 Nixpkgs 的 helper derivation 和继承补丁，别把 `postPatch` 盖丢了。

Helper patch 涉及 `src/core/manager.rs`、`server.rs`、`logger.rs`、`reconcile.rs` 和 vendored Kode Bridge HTTP server：每个请求保留内核 Unix peer 身份（非 root 停止和重复启动静默丢弃，受控来源须 root 启动，身份缺失或状态不可用不许授权 GUI 重启），升级时把所有执行、log writer、socket `chmod`/`unlink` 和已存进程恢复入口过一遍（受管执行解析到 `LF_CLASH_CORE_PATH` 且文件/祖先 root 所有，允许 root 所有 sticky store 祖先；日志固定写私有服务目录；客户端路径/PID 绝不变成 root 文件/进程操作）。

共同启动路径要求子进程存活且控制 socket 15 秒内可连，候选解析与附加审计共用 11 秒准备预算，受管 IPC handler 预算 20 秒——取消或超时会杀掉本次启动的进程组（含校验进程）免得超时后提交配置。helper 串行处理启动并对相同请求复用 30 秒内完成的结果（要求固定 GUI 文件身份/时间戳匹配、原 PID 存活且 socket 可连），兼容 root 导入在输入变化时重启，GUI 只能在已知核心停掉时跑首次兼容启动，root 来源激活先停旧核心再起新实例，root 停止等待 watchdog 退出并持久化停止意图（helper 监听保持可用），配置拒绝不触发 helper 重启。

[runtime.nix](../../system/programs/clash/service/runtime.nix)、[identity.nix](../../system/programs/clash/service/identity.nix)、launcher 和 stage 工具须保持同步：顶层 runtime 组匹配 systemd 免得重启递归改属主，可写核心缓存不加进 root helper 的 `StateDirectory`，快照目录跨重启保留，授权/验证日志私有，并从真实 helper 启动的核心检查五类 capability 集。启动默认值 helper 还会在持每用户锁并确认 GUI 停止时规范化 `profiles.yaml`（只把 `1..1439` 的整数 `update_interval` 抬到 `1440`，不动 `allow_auto_update`、local profile、注释和未知字段，绝不新建缺失键），外层启动器持续持锁直到 GUI 进程可见。

`clash-verge-stage.py` 负责候选检查、快照授权和健康接受，[source_refresh.py](../../system/programs/clash/service/source_refresh.py) 则用隔离副本在重启健康核心前预检登记候选（不写运行缓存也不授运行权限）。保持候选、来源版本、settled 基线和 root 策略绑定直到实际切换，符合策略的节点变化生成 root 私有、五分钟有效的 `approval.json`（同时绑定审计和验证器策略，结构类和未确定结果不自动批准）交给原读取器复验；启动和回滚也重查来源修订、渲染参数、受管策略和当前审计（旧修订保留原标签比较）。

启动与 root timer 排队下载请求，**B** 经验证并连续健康 90 秒后协调器标记就绪（绑定本次启动、快照、策略、路由选择和授权 epoch，离开健康 **B** 重置，**C** 保持排队，显式 `off` 清空下载和应用队列）。下载服务先复核保护条件再拉取，应用服务把下载代绑定到选择、修订、摘要和路由选择（导入繁忙时释放操作/来源锁等待重试，绑定变了就丢弃请求）。定时下载、已映射 GUI 请求和显式 `add`/`use` 在网络等待期间释放来源发布锁，发布前重新加锁核对登记、选择和桌面索引；**A**/**B** 下的显式操作跳过 90 秒稳定期直接等 verifier（不持操作锁或来源锁）。本次启动内健康失败 pin 跨 `clash-on`/`off` 保留剩余 TTL；启动导入保满 60 秒窗口，后续导入连续新鲜完整失败可在宽限期后提前回滚（成功接受始终走满 60 秒）。

检查期间释放授权锁、重加锁复核健康和输入绑定（接受/回滚未收尾时暂缓刷新）。`clash-source-guard.service` 重启前先入 **C**（下载和解析服务不拿 `CAP_NET_ADMIN`），撤权失败阻止重启，实际导入重跑校验审计再健康接受或回滚。发布快照授权先持授权锁再拿策略锁，协调器在放策略锁前验传输防火墙并把健康观测绑到该快照摘要。模式协调器验完初始 **A** 或 **C** 防火墙就报 systemd 就绪，运行循环里再查完整 **B** 健康证明；**A** 中切换导致路由代次更新时，未启用路由选择回到新代次的 `rules`、代理意图保持关闭。

`profile-refresh-verify.timer` 每 10 秒跑一次，模式监督器发 `/run/proxy-mode/boot-id`，`refresh.nix` 定五分钟通知 timer，审计 worker 和状态工具用 Nix 提供的 PyYAML（保持 worker 无特权、固定 JSON schema 和原因词表，`subscriptionAuditAllowedHosts` 只从 root 配置传）。Nixpkgs 目前以 `.verge-mihomo-wrapped` 暴露底层核心、包把它换成授权启动器 `verge-mihomo`，升级 GUI 时核对 wrapper 布局、`clash-on`/`off` 进程身份、旧 singleton 清理、HTTPS 延迟默认、System Proxy 拦截、控制器丢弃/TUN 派生，并跑通两个路由子模式下的 `proxy-gui` 与 `proxy-gui-vm`。

## DNS 与浏览器集成

运行时路由用两组不可变策略/健康/审计包加一个 root 选定的符号链接，守住 `operator → source → authorization → policy` 锁顺序（探针在 policy 锁外跑，提交前重查路由 epoch 和策略摘要）。应用子模式和续内核租约都要完整 mixed TCP、fake-IP TCP、DNS、监听和配置证明（`proxy-only` 外加经核心验证器的真实地址 DNS 证明）。常规下载在网络等待后重查选择、登记和策略/epoch，显式恢复则在有界下载期间持登记锁并维持 **C**。保留带摘要 `source-data.json` 里的原始 `Rule` 数据和固定节点，按来源和子模式分存已接受快照；切换关旧核心/relay 流、清解析缓存并跨重启留住 fake-IP 映射；来源启动等新 helper 暴露 IPC 而不因启动延迟重启它（既有核心恢复保留 IPC 故障重起）。独立 `lf_routing` 表在基础防火墙重载后仍须检查节点传输元组（已有直连回复无例外），恢复路径双向核对同一到期元组，改这些路径记得跑 `proxy-routing` 和 `proxy-routing-vm`。

`dnsmasq` 补丁是 `network.nix` 里的内联 `--replace-fail`：确保 `strict-order` 重试时最后一台服务器的 UDP `SERVFAIL`/`REFUSED` 和 DNSSEC EDE 能回到客户端，并用 `sent-to` 数组索引支持重复端点（升 `forward.c` 时核对并保留网络 VM 真实 DNS 测试）。Unbound 用未改源码的 `pkgs.unbound-with-systemd`（留着 `--enable-systemd` 配合 `Type=notify`，并与 [DNS](dns_CN.md) 保持一致；Chrony 保留 NTS 和证书时间校验）。

启用 `localDnssec` 时，[core-validator.nix](../../system/config/network/dns/core-validator.nix) 在核心命名空间分跑应用和基础设施验证器，保留各自预算、持久信任锚和对客户端 CD 绕过的拒绝。验证与上游选择分开：`rules` 保留国内分流（`geosite:cn` 直连国内验证 DoH，其余经 `lf-remote` 走 Cloudflare/Google DoH，节点引导走独立加密解析器，`lf-dns-transport` 独立用 `mihomoDomesticValidatingDnsUpstreams` 而不继承 **A** 的 DoT 列表），`proxy-only` 则把两个验证器绑到经剪枝远程兜底组的认证字面地址 DoH 并禁直连引导，来源/后端真实地址 DNS 留固定 Unix 桥接和预算。私有 TUN 的 UID 排除必须和验证器仅查 loopback 的防火墙、应用拒绝访问验证器/传输端口配套保留，确保 Mihomo 在公网 fake-IP 合成和拨号固定前完成验证（清除合成 AD、保留 HTTPS/SVCB 参数），修改这些路径跑 `proxy-assurance-vm`。

Firefox 定制在打包层加不可变 policy、AutoConfig、字体、preflight 和外层隔离（Home Manager 或 Firefox 升级可能在不触发补丁失败的情况下改掉偏好或 wrapper 语义），分三类检查验证：

- `browser-privacy`：打包策略、启动器、桌面/MIME 入口、偏好锁定、DoH/代理值、沙箱环境及旧 profile/进程处理。
- `browser-sandbox-vm`：真实 Wayland 浏览器、系统 CA 信任（只读 bundle 生效且隐藏 `/etc/static`）、文件与设备访问（拒绝普通文件和无关 GPU 节点）、音频、文档门户（各应用独立视图）、两个入口在测试路由下的表现、窗口复用和门户撤权清理。
- `browser-ech-vm` 与 `browser-ech-uniform-vm`：HTTP 代理和透明路径、HTTPS 记录、ECH 与 GREASE、密钥/无 ECH 重试、不可信/证书拒绝、掩护名不改目标及 **A**/**C** 行为。

更新沙箱依赖或内核时复核 `bubblewrap`、`xdg-dbus-proxy`、Landlock、seccomp 和门户身份；升 Firefox/NSS 时对照[浏览器手册](browser_CN.md#ech-发现与代理兼容)检查 `CONNECT` 条件、原生 type-65 解析 API 和重试规则。还要确认 `dnsEncryption="strict"` 在协调器、broker 和静态防火墙三处一致（保留 split DNS、MagicDNS 和字面门户，并测试两种传输策略）。NVIDIA 机器上用全新 profile 在真实 Wayland 合成器和 PipeWire/Pulse 下对比隔离内外 `about:support`（确认硬件 WebRender、DMA-BUF 和沙箱内 EGL 可解析；硬件视频解码用同包无隔离基线另测）。

## 防火墙归属与加载

[防火墙生命周期](network_CN.md#防火墙生命周期)保留精确表 family/name 组合，只有 `proxy-control migrate-firewall` 在操作者确认归属后删 `inet filter`、`ip proxymode_nat` 和 `ip6 proxymode_nat` 并把清理并入同一持锁事务（不留持久清理状态）。保留共享 policy 锁、静态+安全模式单事务加载、失败保留原策略/bootstrap 基线、停 unit 保留保护，以及代理意图在开 **B** 前先过 **C**（重载或迁移时核心存活也一样）。`inet lf_policy_guard` 独立于 `inet lf_filter`（用临时报文 priority 位记经过情况并在受监控 hook 两端清理，与 B/C 链同事务安装）。

本地验证和 HTTP-only 按 `tunProbeNames` 顺序试（纯合成地址 TUN 用新生成名字），客户端 DNS 探针在 20 秒预算内最多三轮（HTTP-only 须解析出 IPv4 答案，不能只认 CNAME 或非空诊断），`degraded` 只留给全硬检通过后的观察性警告。启用授权租约时，四条到期链保留内核超时和 **C** 请求/回包守卫，新鲜完整证明绑定启动/配置/策略代次/休眠 epoch 且单次消费，休眠撤权与关闭清理须对齐就绪检查和 `nft` 事件过滤（`proxy-assurance-vm` 覆盖监督器冻结/停止、双栈已有 TCP/UDP、双向到期和保留恢复）。动 `nftables`、systemd 加载或协调逻辑时，跑 `proxy-firewall-vm`、完整规则变体、`proxy-control-vm` 和 `proxy-network-vm`。

[拒绝汇总](operations_CN.md#拒绝计数器)在 [inspect.py](../../system/config/network/diagnostics/inspect.py) 把链和 `nft` JSON 表达式映成原因/角色分组，改链名、访客接口顺序或源/端口表达式时保持映射一致，并跑 `proxy-regressions`、`proxy-control-vm` 和 `proxy-guest-vm`（权限/解析错误与零计数区分开）。

## 链路身份

[link-identity.nix](../../system/config/network/identity/link-identity.nix)、[wired.nix](../../system/config/network/identity/wired.nix) 和 [link_identity.py](../../system/config/network/identity/link_identity.py) 直接用上游 NetworkManager/systemd/dhcpcd 接口、没有源码补丁。升级时核对 `nmcli` 属性名与枚举、两个主机名开关、`stable-id` 替换量、DHCP 标识和 IPv6 地址生成，保留身份白名单备份、私有状态、断开/自动连接守卫和拒绝覆盖手工修改（重建不自动应用 profile 目标）。扩展 `dhcpcd` 单接口命令/PID/reload 时保住上游生成配置和服务加固，确保其 DHCPv6 socket 能和 NetworkManager Wi-Fi 客户端共存，一起跑 `network-identity-config`、`network-identity-vm`、`proxy-control-vm` 和 `proxy-network-vm`（持久化准备见[链路身份指南](identity_CN.md)）。

## 升级流程

### 源码兼容性检查

[sources.json](../../system/config/network/maintenance/sources.json) 按组件记维护入口、不变量和回归检查，[fingerprints.nix](../../system/config/network/maintenance/fingerprints.nix) 对选定包源码、vendored 依赖摘要和主要 Nixpkgs 配方算指纹。正常系统构建要求匹配基线——就算版本号没变，源码或配方变了也得查兼容性（Mihomo 变动会连带选中 vendored `sing-tun` 和 `socks`）。

先预览锁定输入和网络基线变动（单升一个应用用 `nix flake update <input>`，提前看下载和本地编译路径用 `nix build --dry-run .#nixosConfigurations.lfour.config.system.build.toplevel`）：

```sh
python3 scripts/check-maintenance.py --plan-pins
```

生成详细网络组件计划（命令末尾可加 `mihomo sing-tun socks`、`gost`、`clash`、`dns`、`firefox`、`tailscale`、`firewall` 或 `link-identity` 手动选组件）：

```sh
nix eval --json .#nixosConfigurations.lfour.config.my.proxy.upgradeSources \
  > /tmp/network-upgrade-selected.json
python3 system/config/network/maintenance/upgrades.py plan \
  system/config/network/maintenance/sources.json /tmp/network-upgrade-selected.json \
  > /tmp/network-upgrade-plan.json
```

跑选出来的检查：

```sh
jq -r '.checks[] | ".#checks.x86_64-linux." + .' /tmp/network-upgrade-plan.json \
  | xargs -r nix build --no-link
```

复核完上游源码和打包配置后跑 `python3 scripts/check-maintenance.py --sync-pins`（核对版本和哈希差异后输 `y`；网络指纹没变时只更新根台账），再跑受影响组件检查、`proxy-upgrade-review` 和候选系统构建。注意配方指纹只覆盖主包表达式不包全传递闭包，lock 差异里的内核和图形栈变化照样得看，增删补丁/依赖/入口时同步更新清单。

### 升级步骤

1. 先留一个可用的系统 generation，再更新目标 flake input。
2. 比对 lock、Nixpkgs 打包表达式、上游源码和 vendored 依赖变化。
3. 调整受影响的补丁入口，文件挪动了就同步改 fileset 和构建阶段，上游已修的补丁可在保留回归检查的前提下撤掉。
4. 配置约定有变时，同步调整 root policy、GUI Merge、健康检查、控制 API 和监听身份。
5. 按下表跑受影响组件检查（涉及刷新、内容审计或启动默认值的加上对应控制/刷新 VM），再构建候选系统。
6. 部署后重启旧浏览器，跑 `clash-off` 和 `clash-on` 检查就绪状态和实际流量（`auto` 模式下顺便验一下接受和坏候选回退）。
7. 把源码基线、自定义清单和相关手册同步更新到最终状态。

## 验证

配置复制脚本有单独检查（需要 Python、Bash 和 `rsync`）：

```sh
python3 tests/deployment/test_deployment.py scripts/push-to-dir.sh
```

以下检查通过 [tests/checks.nix](../../tests/checks.nix) 挂在 `checks.x86_64-linux.*` 下（VM 检查在隔离虚拟机里跑，硬件加速需要 KVM 权限）：

| 检查 | 范围 |
|---|---|
| `proxy-control` | 临时文件验证快照授权、过期、重启、恶意输入和权限，以及 B 租约证明单次消费、休眠/代次拒绝 |
| `proxy-refresh` | 离线审计、有界 HTTP 检查、本地更新状态和通知去重 |
| `proxy-source` | 登记来源下载与渲染、只读比较、自动批准绑定、重启导入、候选预检及运行恢复 |
| `proxy-source-config` | 来源与浏览器配置、私有 DNS 路径、下载和策略能力分离 |
| `proxy-refresh-vm` | 真实 auto/enforce helper 与核心：健康接受、坏候选回退、pin 重启、回滚及 desired-state 恢复 |
| `proxy-upgrade-review` | 所选源码/依赖/recipe 基线、组件覆盖、检查名称及源码兼容性检查（输入检查也接入正常系统构建） |
| `network-identity-config` | 身份迁移边界、实际 `nmcli` 解析/主机名恢复、有线变体、服务范围及持久化（正常系统构建也校验目标） |
| `network-identity-vm` | 真实有线/Wi-Fi DHCPv6 共存、新双栈身份报文、UUID/epoch/随机轮换、稳定/固定/硬件行为、profile 优先级、恢复、宿主密钥保留以及按 profile 清理 ARP 邻居 |
| `proxy-arp-vm` | ARP 应答范围与计数、网关冲突、B 撤租、链路恢复及 IPv6 回归 |
| `proxy-regressions` | 健康、状态、规则、DNS 租约、有界客户端 DNS 重试、预算及监督器 |
| `proxy-profile`、`proxy-gui` | 生成配置变体、策略/模板一致、GUI 默认、进程 wrapper 与桌面命令拦截 |
| `proxy-gui-vm` | WebKit 以改名账户和非默认 home 自动化运行未修改的生产 GUI：mode/TUN/System Proxy/PAC/代理守护静默丢弃、OS 状态与核心 PID 保持、无控制错误提示、GUI 退出及 root 路由事务 |
| `proxy-routing` | 本次启动的不可变选择、完整证明与 epoch 绑定、准备失败、清理后的状态及真实 DNS 截止/回退拒绝 |
| `proxy-routing-vm` | 登记来源与生产命名空间：双向子模式切换、原始规则恢复、物理对端仅节点外发、真实 DNS/来源刷新、旧回复、访客拒绝、幂等、并发选择、off/on、重启及内核到期撤权 |
| `proxy-routing-recovery-vm` | C 中应用缓存候选、DNS 与 HTTP 叶子轮换，以及回到 A 后结束旧路由代次 |
| `proxy-single-egress-vm` | 两个可控出口：`rules` 独立选组复现、固定叶子的双栈 TCP/UDP 与 DNS 观测、实际 Firefox 和严格入口、UDP 不支持/故障、旧流、并发刷新、上行/节点故障，以及路由与租约回归 |
| `proxy-ip-tunnel-vm` | 双栈 WireGuard echo、固定 TCP/UDP 出口、relay 限于所声明对端、来源验收、租约撤销和对端换代 |
| `proxy-rules`、`proxy-egress-rules` | 构建期 LKL `nft` 解析完整兼容/受限模式规则 |
| `proxy-firewall-vm` | A/C 表归属、主表或关键链丢失及修复失败时的 C 隔离、外部表保留、加载失败回滚和迁移 |
| `proxy-dns-strict-rules` | LKL 解析完整严格 DNS 防火墙规则 |
| `proxy-dns-config` | 默认、严格、双栈、IPv6-only 和 HTTP-only 配置中生成的 DNS 与验墙脚本语法 |
| `proxy-dns-strict-vm` | 真实 helper/核心、共享 DNS/代理过滤、严格故障/override/租约及残留集合拒绝、私网 split 权限与字面门户恢复 |
| `proxy-assurance-vm` | A/B 本地 DNSSEC、DNS 切换与探测失败、独立 DNS 预算，以及规则丢失、租约到期和休眠时的双栈 B 隔离 |
| `proxy-guest-config`、`proxy-guest-rules` | 生产/独立账户配置与 rootless 启动器语法；LKL 中的完整混合角色规则（正常系统构建也要求访客校验通过） |
| `proxy-guest-vm` | 三个真实转发网络、DHCP/DNS、IPv4/ULA、伪造源地址、实际 `pasta`/RootlessKit/subuid socket、fake-IP 拒绝、旧 DNS conntrack、双向撤销以及核心存活时的 C |
| `proxy-hermes-containers-vm` | 真实旧/新 Docker、socket ACL、离线镜像、账户授权、TCP/UDP、未支持 IPv6 拒绝、Hermes 宿主代理、A/B/C 与恢复 |
| `proxy-core-netns-vm` | 独立核心 namespace 原型：TUN/veth、双栈节点、原生/SOCKS UDP、DNS/fake-IP、mark/raw 包、宿主策略归属、撤权与重启 |
| `proxy-egress-config` | 规范 CIDR 与授权/时间目标范围（接入正常系统构建） |
| `proxy-egress-vm` | Tailscale VM 的受限策略版本：双栈私网/多播/DNS/DoT/NTS、UID/接口/端口拒绝、已有连接撤权与 C |
| `proxy-egress-control-vm` | 真实生产 TUN/NAT 之前的受限策略，包含私网允许与拒绝 |
| `tailscale-transport` | 所选上游 Linux bypass 常量与预期的 `magicsock`/STUN socket 打标路径（正常系统构建也强制执行） |
| `proxy-tailscale-vm` | 真实 Tailscale 监听与 CLI、双上行双栈 UDP/STUN、监听重启和替换、端口保留、租约到期、严格模式阻断及远控端口策略 |
| `proxy-control-config` | 生产/测试身份、helper 能力与监听一致 |
| `proxy-core` | 定制核心构建，以及 parser、router、目标与缓存、实际适配器叶节点、DIRECT、UDP、DNS、SOCKS 报文和 `sing-tun` 区间端点的 Go 测试 |
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

比如把相关包和快速检查合在一条命令里跑：

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

`proxy-control-vm` 和 `proxy-managed-netns-vm` 跑的是生产 helper 和核心命名空间，`proxy-network-vm` 覆盖更广的故障注入，`proxy-core-netns-vm` 则是独立原型拓扑。内存吃紧时就一个个构建 VM 目标并加上 `--max-jobs 1`，激活后记得在真实宿主机上顺手测一下就绪状态和应用流量。
