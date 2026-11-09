# 受管 Firefox

[English](../browser.md) | 简体中文 | [文档目录](README_CN.md)

## 软件包与启动

Firefox 是原生 Nix 包。[firefox.nix](../../home/programs/firefox/firefox.nix) 将二进制、enterprise policies、AutoConfig 和固定字体绑定在不可变 store closure 中，Home Manager 管理独立的 `~/.config/mozilla/firefox/lfour` profile。更新跟随锁定的 Nixpkgs 和重建 generation，浏览器二进制自更新禁用；旧 Flatpak profile 不自动导入或删除。

公开启动器先运行 [guard.py](../../home/programs/firefox/guard.py)，再打开指定 profile。B 要求新鲜、同代次的模式/DNS/relay/验证状态、被动监听身份检查，并通过桌面 GUI 前向 socket 核对实时 API 不变量。私有配置快照及内部控制口由 root 监督器验证，浏览器使用其发布的就绪结果，不读取私有文件。A 允许在公共 DNS 尚不可用时打开字面 HTTP 门户。C、未知和 `unenforced` 拒绝启动；二次状态读取拒绝预检中途的切换，启动后的变化由 relay 和内核策略控制。

启动器在严格隐私下检查字体，强制 Wayland，去掉禁用沙箱的继承开关，并拒绝复用占有 profile 锁的旧包进程。桌面/MIME 入口均使用它，原 Zen/Chrome ID 转向 Firefox。策略更新后需完整重启浏览器。`firefox --check` 仅检查前提，不创建 profile 或开窗口。

## 网络策略

默认 `my.firefox.networkPath="http-proxy"` 将 HTTP/HTTPS 锁定为 Gost `127.0.0.1:33332`，bypass/PAC/SOCKS 为空，localhost 也走代理，禁用直连 failover。A 直通，B 进入 Mihomo 路由，C 关闭应用路径。下方可选透明路径使用原生 socket，并遵循同一套宿主策略。隐私设置不改变 `bootMode` 或 `proxyOnly`。

WebRTC 和 HTTP/3 禁用，使网页传输使用 TCP。DoH 禁用并锁定（`network.trr.mode=5`），浏览器自身 DNS 走系统解析器。显式 HTTP 代理可委托 Gost/Mihomo 解析目标；核心在 DNS 应答及应用拨号前执行同一份域名快照、附加名单与例外。DNSSEC 和加密取决于解析路径及 `dnsEncryption`，见 [DNS](dns_CN.md)。门户检测与 NetworkManager 连通性检查保留，HTTPS-First 仍允许本地 HTTP。

## ECH 发现与代理兼容

ECH 加密 TLS ClientHello 的内部内容，包括站点 SNI。本配置由浏览器从 HTTPS DNS 记录取得 ECHConfig。GREASE 只添加形似 ECH 的合成扩展，不代表内部加密握手成立。

### 浏览器路径

| `my.firefox.networkPath` | 名称发现与传输 | ECH 行为 |
|---|---|---|
| `"http-proxy"`（默认） | 锁定 Gost HTTP 代理，目标解析可委托核心 | 锁定的 Firefox 159.0.1 将 HTTPS CONNECT 排除在 HTTPS-RR 发现之外；保留 GREASE，不承诺真实 ECH |
| `"transparent"` | 系统 DNS 与原生 HTTPS-RR 查询；A 使用直连 socket，B 使用宿主 TUN/防火墙路径 | NSS 可使用返回的 ECHConfig，与支持的站点进行真实 ECH 握手 |

Firefox 的[连接门](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_159_0_1_RELEASE/netwerk/protocol/http/nsHttpChannel.cpp)同时检查代理 DNS 策略和 `UsingConnect()`，单改 DNS 传输不会改变这个门。本包禁用仅供测试的 `force_use_https_rr` override，透明选项复用打包浏览器，不修改 Gecko 源码。

在系统配置中显式选择：

```nix
my.proxy.tunMode = true;
home-manager.users.lfour.my.firefox.networkPath = "transparent";
```

重建 generation 并完整重启 Firefox。模块拒绝 `tunMode=false` 下的透明路径。这是固定的软件包策略，不是 HTTP 代理失败后的自动直连。启动器继续检查网络就绪；C 在内核阻断流量并拒绝新启动，A 保留字面 HTTP 门户。`bootMode`、`proxyOnly`、浏览器指纹防护和 `dnsEncryption` 均独立设置。

### 发现过程

下图展示正常加密公网 DNS 路径，DNS 恢复和私网解析器例外遵循 [DNS 策略](dns_CN.md)。

```text
Firefox 原生 HTTPS 查询
  -> libc resolver -> systemd-resolved -> dnsmasq 后缀过滤
       A: Unbound -> 认证 DoT + 本地 DNSSEC 验证
       B: 核心后缀过滤 -> 声明的加密解析器
  <- 携带 ECHConfig 的 HTTPS RR

Firefox/NSS 构造加密 ClientHello
  -> A: 直连 TCP
  -> B: 受管 TUN/核心 -> Rule DIRECT 或选定代理 -> 站点
```

Unix 浏览器通过[原生解析 API](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_159_0_1_RELEASE/netwerk/dns/PlatformDNSUnix.cpp)查询 type 65。浏览器 DoH 保持关闭并锁定；现有解析链路已能传递 HTTPS DNS 消息，无须另开本地 DoH 入口。

核心保留地址记录的 fake-IP 行为，同时让 HTTPS 和 SVCB 记录经过正常解析器。共享过滤先执行，阻断域返回本地 NXDOMAIN，不交付 ECHConfig。记录参数（含 ECH public name）返回浏览器，核心不使用浏览器的 ECHConfig，也不终止站点 TLS。A 的 DNSSEC、B 的加密传输、已批准 split DNS 和 MagicDNS 保留[各自边界](dns_CN.md#各路径的责任)。

NSS 选择配置、构造 ClientHelloInner/Outer、验证证书并处理已认证的重试配置。GREASE 与 `ECHAccepted` 是不同观测：普通 TLS 可以携带 GREASE，而服务端仍报告未接受 ECH。

### 路由与失败行为

受管核心在 Rule 和统一代理模式均关闭嗅探。ECH 外层 SNI 是掩护名，不能替代实际目标；显式代理目标和 fake-IP 映射提供域名规则需要的名称，字面 IP 按 IP 规则处理。Root parser 也会将此策略应用到旧 GUI 配置。

没有可用 HTTPS/ECH 记录时，仍允许正常验证证书的普通 TLS，因此 ECH 按站点能力使用，并非每个站点的强制条件。`network.dns.echconfig.fallback_to_origin_when_all_failed=false` 禁止该偏好控制的 origin 回退，但不禁止 NSS 独立的“认证后不带 ECH 重试”：受信任服务器拒绝 ECH 且不提供可用重试密钥时，可以触发携带站点 SNI 的普通 TLS 新连接。允许已认证密钥重试，不可信拒绝或无效证书不授权这种重试。记录缺失/被剥离、DNS 故障与已认证拒绝的结果因此不同；`dnsEncryption="strict"` 约束 DNS 传输，不要求所有站点支持 ECH。

显式 HTTP 代理能看到 CONNECT 目标，其它客户端即使能通过代理使用 ECH 也不改变这一点。透明路径的本机核心可见 DNS/fake-IP 映射与路由目标。ECH 不隐藏目的 IP、时序、账户或站点可见的内容；协议接受和重试规则见 [RFC 9849](https://www.rfc-editor.org/rfc/rfc9849.html)。

### 集成维护

`browser-ech-vm` 使用所选生产 Firefox 包和真实 helper/核心，配合本地 DNS、临时 CA、ECH 服务端及另一个地址上的掩护名陷阱。它区分已接受 ECH、普通 TLS/GREASE、密钥重试、认证后不带 ECH 重试、不可信拒绝和证书失败，检查掩护 SNI 不改变拨号目标，并保留 A 门户/C 启动检查；只使用全新 profile，不连接个人浏览器。

浏览器或核心变更后，与 `browser-privacy`、`proxy-core`、`proxy-profile` 一同运行。浏览器升级可能改变 CONNECT 门、原生解析和重试处理，核心升级可能改变 fake-IP 下的服务记录处理；同步维护[维护手册](maintenance_CN.md)与所选版本的路径说明。

## 隐私设置

模块中的 `strictPrivacy` 默认 true。AutoConfig `lockPref` 覆盖 enterprise policy 无法锁定的命名空间，对该包打开的所有 profile 生效，抵抗冲突 `user.js`。

| 层 | 行为与边界 |
|---|---|
| 指纹 | RFP 和 letterboxing 减少 Canvas、时区、硬件和窗口尺寸信号；定制桌面仍不同于 Tor Browser |
| 跟踪与状态 | Strict protection、FPP、Total Cookie Protection 减少脚本和跨站状态关联 |
| URL 与分析 | 查询参数清理、bounce 清理、跨源 referrer 限制和 GPC；禁用建议、预连接、遥测、studies、赞助内容、Sync 与内置 IP Protection |
| API 与字体 | 禁用 WebRTC、WebGPU、地理位置、电池和传感器；严格隐私另禁 WebGL，仅暴露固定 DejaVu/Noto/CJK/emoji 字体 |
| ECH / TLS | 启用原生 HTTPS-RR、ECH 和 GREASE；透明模式支持 ECH 发现，锁定浏览器的默认 CONNECT 路径不支持 |
| 出口地址 | 共用 A/B/C 与路由策略；默认 B Rule 仍可能 DIRECT |

严格扩展策略阻止未声明扩展，强制安装 uBlock Origin，KISS Translator 可移除。签名包及更新经选定网络路径从 Mozilla Add-ons 获取，需要联网；包策略和 Gecko 保护不依赖扩展下载完成。

Gecko 内容进程沙箱保留，原生父进程仍有普通桌面用户的文件/socket 权限。同 UID 程序或包内私有 unchecked 二进制可绕过启动预检，不构成外层文件系统/会话总线沙箱。`strictPrivacy=false` 放宽指纹、字体、WebGL 与扩展限制，选定网络路径、系统 DNS 和启动前提保留。

软件包升级与检查见[维护手册](maintenance_CN.md)，解析行为见 [DNS](dns_CN.md)。
