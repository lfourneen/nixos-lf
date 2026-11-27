# 受管 Firefox

[English](../browser.md) | 简体中文 | [文档目录](README_CN.md)

## 软件包与启动

这里的 Firefox 是原生 Nix 包。[firefox.nix](../../home/programs/firefox/firefox.nix) 把二进制、enterprise policies、AutoConfig 和固定字体打包进同一个不可变的 store closure，Home Manager 负责管理 `~/.config/mozilla/firefox/lfour` 这个独立 profile。更新跟着锁定的 Nixpkgs 和系统重建走，自带的二进制自更新是关掉的，旧 Flatpak profile 也不会自动导入或删除。

启动浏览器前，启动器会先跑一遍 [guard.py](../../home/programs/firefox/guard.py)。在 **B** 模式下，它会核对模式、DNS、relay 和验证状态是否新鲜且属于同一代次，再通过桌面控制 socket 核对监听身份和实时控制策略（开启运行时路由时，按当前生效的 `rules` 或 `proxy-only` 选择对应的快检；私有配置快照由 root 服务负责校验并发布结果）。

**C**、未知状态和 `unenforced` 都会直接拒绝启动。预检结束时还会再核对一遍策略代次、路由 epoch 和出口 epoch，往已经打开的浏览器实例里传链接也走同样的检查。浏览器跑起来之后，后续的状态变化由 relay 和防火墙负责兜底。

通过预检后，启动器会在严格隐私下检查字体，再把浏览器放进下面的外层沙箱里跑。宿主侧的实例记录会结合进程启动时间和包身份来拒绝复用旧包，stale profile lock 里的命名空间 PID 也不会被当成宿主桌面进程。桌面入口和 MIME 关联都指向它（原来的 Zen/Chrome 入口 ID 也重定到了 Firefox）。策略改动后需要完整重启浏览器；如果只想检查前提条件而不创建 profile 或开窗口，可以跑 `firefox --check` 或 `firefox-identity --check`。

## 外层沙箱

[sandbox.py](../../home/programs/firefox/sandbox.py) 保留桌面 UID，并用 `bubblewrap` 拉起独立的挂载、用户、PID、IPC 和 UTS 命名空间（严格浏览器还会额外隔离网络命名空间）。浏览器能看到的只有合成出来的家目录以及私有的 `/tmp` 和 `/dev`，capabilities 全部清空，环境变量也换成白名单重建，同时保留 Gecko 自己的内容进程沙箱。底层复用的还是原生 Firefox 二进制，靠 Nix 包装和一小段 [scope 辅助程序](../../home/programs/firefox/scope.c)提供外层约束，没有改 Gecko 源码。

| 资源 | 浏览器权限 |
|---|---|
| 持久文件 | 可读写该入口的受管 profile、缓存和下载目录；其它个人文件必须经文件选择器授权 |
| 运行文件与信任 | 只读 Nix store、选定的解析器/CA 文件及图形驱动；不挂载完整宿主根目录、个人主目录或 runtime 目录 |
| 显示与音频 | 指定 Wayland socket、可用的 Pulse socket 与 DRM render 节点（声明 NVIDIA 加速时另开放对应 primary 节点及渲染接口）；不开放 X11、输入设备、摄像头或 NVIDIA modeset 接口 |
| 会话总线 | 过滤代理仅允许文件选择、只读设置、请求/会话关闭、协议支持查询及当前精确 profile 的链接复用 |
| 其它宿主接口 | 不开放系统总线、SSH/GPG agent、容器或核心控制 socket；Landlock 禁止连接宿主抽象 Unix socket 和向作用域外发信号 |

### 证书与图形设备

系统证书信任使用 `/etc/ssl/certs/ca-certificates.crt` 下的 NixOS CA bundle（包含声明的系统 CA）。启动器会解析它的符号链接，把实际文件只读挂载到对应路径，让 NSS/p11-kit 能读到，同时不暴露 `/etc/static` 或整个宿主证书目录。找不到 CA bundle 就直接拒绝启动，证书校验也始终保持开启。

`my.firefox.nvidiaAcceleration` 默认跟随声明的 NVIDIA 驱动，把渲染需要的 GPU 节点和只读 EGL 配置放进来，藏掉无关设备和 modesetting 接口。设为 `false` 可以把权限进一步收到只留 render 节点；Gecko 沙箱继续生效，锁定隐私策略也依然禁用 WebGL。

### 系统接口与文件访问

系统调用过滤还会拦掉 keyring、进程检查、文件句柄打开和部分用不到的内核接口，只留出 Gecko 沙箱需要的命名空间和 seccomp 操作。这要求内核支持 Landlock ABI 6+ 并允许非特权用户命名空间；如果隔离不可用或总线代理失败，浏览器会直接拒绝启动或终止，没有无隔离回退。

总线代理通过桌面门户的 Flatpak 身份协议把浏览器报成 `org.nixos.ManagedFirefox`，不需要装 Flatpak runtime。浏览器只能看到自己拿到的文档授权，在文件选择器里挑中哪个文件就只放行那个文件，不会把整个父目录都敞开；另外，浏览器的可写目录不能是符号链接。

### 下载目录

日常 Firefox 下载到 `~/Downloads`，它在 `~/Downloads/identity` 看到的只是一个空的只读挂载；身份 Firefox 则读写真正的 `~/Downloads/identity`。以前的身份下载会留在原路径，可以用身份 Firefox 或桌面文件管理器打开；如果确实要在日常 Firefox 里打开某个身份下载，通过桌面文件选择器挑中它即可（授权只归发出请求的那个浏览器身份）。门户浏览器则把 `~/Downloads` 挂在临时存储上，关掉会话就清理。

### 已有窗口与重启

正常启动器会通过当前精确 profile 的 D-Bus 名称把新链接交给已打开的窗口，代次记录和宿主启动锁都放在浏览器挂载范围之外。重建包之后记得先把旧窗口全关掉，再开新包，受管 profile 在重启后都会保留。`firefox --check` 也会顺带检查私有 runtime 目录、桌面 socket 和 Landlock 支持，但不会弹窗。

日常透明路径使用的是桌面 UID、系统 DNS 和 A/B/C 规则，保留对其他已授权本机服务的访问，Wayland、音频、`Downloads`、profile 和用户挑中的文档都属于明确放开的权限。不过也要注意，已经在同一个桌面 UID 下无隔离运行的其他程序仍属于信任基础，它们本来就能直接调用包里的私有二进制。

## 网络策略与身份

| 入口 | 可用模式 | Profile 与网络 |
|---|---|---|
| `firefox` | 默认允许 **A** 和健康 **B** | 持久化日常 profile，本机使用原生 socket |
| `firefox-identity` | 仅健康 **B** | 独立 profile、缓存和下载目录，只允许 uBlock Origin |
| `firefox-portal URL` | 仅显式 **A** | 临时 profile，只能通过 relay 连接一个字面 IP 的 80/443 端口，最多运行 15 分钟 |

保持 `my.firefox.dailyRequireProxy=false` 时，日常 Firefox 在 **A** 下直连，在 **B** 下按订阅 `Rule` 分流（里面可能包含 `DIRECT`）。如果设为 `true`，日常浏览器也会走跟身份浏览器一样的严格入口（旧选项 `my.firefox.requireProxy` 作为日常设置的兼容别名保留）。

严格入口需要开启 `my.proxy.strictBrowser=true`。浏览器里只有 loopback 网络，通过 `/run/clash-strict/proxy.sock` 访问宿主；宿主 relay 只在健康 **B** 下接流量，核对已连接核心 socket 的 UID，一旦失去就绪状态就立刻掐断连接（在监督器检测之外每 100 ms 轮询一次已发布状态），同时 `nftables` 在 **A** 和 **C** 下也会直接封死这个入口。

核心侧的固定入口 `lf-strict` 走 `lf-remote`，拒绝 `DIRECT`、`COMPATIBLE` 和私网目标。`rules` 模式下这个组可以单独选节点；到了运行时 `proxy-only` 下，它会被绑死到和普通应用、DNS 相同的固定叶子，只有 root 出口事务才能换。GUI 选组或 Global 模式都放宽不了这些规则，浏览器也没法直接碰宿主 TCP、LAN、本机 DNS 或公网。当然，已经排进队列或发出去的字节没法撤回，同桌面 UID 下其他未隔离的程序也依然受信任。

日常和身份 profile 都锁定了 HTTPS-only 以及反钓鱼、恶意软件和下载保护，禁止绕过证书警告（不过 Safe Browsing 可能会披露查询或下载元信息，具体见 [Mozilla 说明](https://support.mozilla.org/en-US/kb/how-does-phishing-and-malware-protection-work)）。要用门户登录时，先用 `clash-off` 显式切到 **A**，再启动临时门户会话：启动器会核对已生效的防火墙策略，保证公网 DNS 离线时也能连上指定的字面 IP；relay 绑定这次直连授权并在结束时断开连接，随后启动器关闭浏览器并清理临时 profile、缓存和下载。

独立 profile 可以把 Cookie 和账号状态隔开，但不会让已经登录的账号变匿名。WebRTC、HTTP/3 和浏览器 DoH 全部关闭，代理域名解析遵循共享的 [DNS 与目标策略](dns_CN.md)。

## ECH 发现与代理兼容

ECH 会加密 TLS ClientHello 的内部内容（包括站点 SNI）。这套配置里，浏览器从 HTTPS DNS 记录里拿 `ECHConfig`（GREASE 会加上形似 ECH 的合成扩展，而真正的 ECH 握手会用服务器的 `ECHConfig` 去加密 `ClientHelloInner`）。

### 浏览器路径

| `my.firefox.networkPath` | 名称发现与传输 | ECH 行为 |
|---|---|---|
| `"transparent"`，本机默认 | 系统 DNS 与原生 HTTPS-RR 查询。**A** 使用直连 socket，**B** 使用宿主 TUN/防火墙路径 | NSS 可使用返回的 `ECHConfig`，与支持的站点进行真实 ECH 握手 |
| `"http-proxy"` | 严格本地 relay 或兼容 Gost HTTP 代理，目标解析委托核心 | 锁定的 Firefox 160.0 将 HTTPS `CONNECT` 排除在 HTTPS-RR 发现之外，保留 GREASE |

Firefox 的[连接门](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/netwerk/protocol/http/nsHttpChannel.cpp)会同时检查代理 DNS 策略和 `UsingConnect()`，单改 DNS 传输没法绕开它。本包把仅供测试的 `force_use_https_rr` override 保持关闭，透明路径直接复用打包好的浏览器，不改 Gecko 源码。

当 `my.proxy.transparentApplications = true` 且 `dailyRequireProxy = false` 时，日常 Firefox 默认用 `"transparent"`，兼容配置则默认用 `"http-proxy"`。如果想单独启用透明路径：

```nix
my.proxy.tunMode = true;
home-manager.users.lfour.my.firefox.dailyRequireProxy = false;
home-manager.users.lfour.my.firefox.networkPath = "transparent";
```

改完重建 generation 并完整重启 Firefox 即可（`tunMode=false` 时模块会拒绝透明路径）。网络路径是在构建软件包时定下来的，启动器照样会查网络就绪状态——**C** 会在内核阻断流量并拒绝新启动，**A** 则保留字面 HTTP 门户访问。`bootMode`、`proxyOnly`、指纹防护和 `dnsEncryption` 依然各自独立。

### 发现过程

正常加密公网 DNS 的流转如下，DNS 恢复和私网解析器例外遵循 [DNS 策略](dns_CN.md)：

```text
Firefox 原生 HTTPS 查询
  -> libc resolver -> systemd-resolved -> dnsmasq 后缀过滤
       A: Unbound -> 认证 DoT + 本地 DNSSEC 验证
       B: 核心后缀过滤 -> localDnssec=true 时使用本地验证器
                         否则使用声明的加密解析器
  <- 携带 ECHConfig 的 HTTPS RR

Firefox/NSS 构造加密 ClientHello
  -> A: 直连 TCP
  -> B: 受管 TUN/核心 -> Rule DIRECT 或选定代理 -> 站点
```

Unix 下的浏览器通过[原生解析 API](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/netwerk/dns/PlatformDNSUnix.cpp)查询 type 65 记录，浏览器 DoH 保持关闭并锁定——现有解析链路本来就能传递 HTTPS DNS 消息，没必要再开一个本地 DoH 入口。

核心在保留地址记录 fake-IP 行为的同时，会让 HTTPS 和 SVCB 记录穿过正常解析器。共享过滤会先跑，被阻断的域名直接返回本地 NXDOMAIN、不交付 `ECHConfig`；记录参数连同 ECH public name 一起返回给浏览器，核心既不使用浏览器的 `ECHConfig`，也不终止站点 TLS。**A** 会在本地验证 DNSSEC，**B** 在 `localDnssec=true` 时同样本地验证，而加密传输、未签名区域、已批准 split DNS 和 MagicDNS 各自保持[独立边界](dns_CN.md#各路径的责任)。

NSS 负责选配置、构造 `ClientHelloInner`/`Outer`、验证证书以及处理已认证的重试配置。注意 GREASE 和 `ECHAccepted` 是两码事：走普通 TLS 的服务端也能看到 GREASE，但同时会报告未接受 ECH。

### 路由与失败行为

受管核心在 `Rule` 和统一代理模式下都关闭了嗅探。ECH 外层 SNI 只是掩护名，不能拿来替代实际目标；域名规则需要的名字由显式代理目标和 fake-IP 映射提供，字面 IP 则按 IP 规则处理（root parser 对缓存的 GUI profile 也会套用这套策略）。

如果拿不到可用的 HTTPS/ECH 记录，正常验证证书的普通 TLS 依然能连，支持 ECH 的站点则会按配置启用。`network.dns.echconfig.fallback_to_origin_when_all_failed=false` 关掉了该偏好控制的 origin 回退，但不会禁止 NSS 独立的“认证后不带 ECH 重试”：当受信任服务器拒绝 ECH 且没给可用重试密钥时，NSS 仍可能发起带站点 SNI 的普通 TLS 新连接。已认证密钥的重试是允许的，而不可信拒绝或无效证书则不会授权重试——所以记录缺失/被剥离、DNS 故障和已认证拒绝的结果各不相同。另外，`dnsEncryption="strict"` 只约束 DNS 传输，不要求所有站点都支持 ECH。

显式 HTTP 代理总是能看到 `CONNECT` 目标（哪怕其他客户端能通过它用 ECH 也一样），透明路径下本机核心也能看到 DNS/fake-IP 映射和路由目标；ECH 不会隐藏目的 IP、时序、账号或站点本来就能看到的内容。协议的接受和重试规则见 [RFC 9849](https://www.rfc-editor.org/rfc/rfc9849.html)。

### 集成维护

Firefox、NSS 和解析器升级都可能改变 ECH 发现和重试行为，相关的浏览器与核心检查列在[维护手册](maintenance_CN.md#dns-与浏览器集成)。

## 隐私设置

模块固定开启了 `strictPrivacy`。AutoConfig 的 `lockPref` 覆盖了 enterprise policy 锁不住的命名空间，对该包打开的所有 profile 生效，也能顶住冲突的 `user.js`。

| 层 | 行为与边界 |
|---|---|
| 指纹 | RFP 和 letterboxing 减少 Canvas、时区、硬件和窗口尺寸信号；定制桌面仍不同于 Tor Browser |
| 跟踪与状态 | Strict protection、FPP、Total Cookie Protection 减少脚本和跨站状态关联 |
| URL 与分析 | 查询参数清理、bounce 清理、跨源 referrer 限制和 GPC；禁用建议、预连接、遥测、studies、赞助内容、Sync 与内置 IP Protection |
| API 与字体 | 禁用 WebRTC、WebGPU、地理位置、电池和传感器；严格隐私另禁 WebGL，仅暴露固定 DejaVu/Noto/CJK/emoji 字体 |
| ECH / TLS | 启用 ECH 和 GREASE；日常透明路径通过原生 HTTPS 记录发现 ECH |
| 出口地址 | 共用 A/B/C 与路由策略；默认 **B** 按 Rule 选择 `DIRECT` 或代理节点 |

严格扩展策略会拦住未声明的扩展：日常和身份 Firefox 都强制安装了 uBlock Origin，日常 Firefox 里的 KISS Translator 可以卸载，门户浏览器则禁止所有扩展。签名包及更新在联网时通过当前选定的网络路径从 Mozilla Add-ons 获取，但包策略和 Gecko 防护本身不依赖扩展下载完成。

Gecko 内容进程沙箱、外层策略和隐私偏好各自守着不同层面，彼此互不取代，也不会改变前面说的路由和解析器信任边界。软件包升级检查见[维护手册](maintenance_CN.md)，解析行为见 [DNS](dns_CN.md)。

### 为指定网站使用 Windows User-Agent

如果遇到必须让日常 Firefox 报成 Windows 版 Firefox 才能正常使用的网站，可以在 Home Manager 里加上下面这行，应用配置后重启浏览器：

```nix
my.firefox.windowsUserAgentDomains = [ "example.org" ];
```

按完整主机名逐个填写，需要的子域名也要单独列出来。这样配置后，Firefox 会在 HTTP `User-Agent` 请求头以及 JavaScript 的 `navigator.userAgent`、`navigator.platform`、`navigator.oscpu` 和 `navigator.appVersion` 里报告 Windows，版本号跟随打包的 Firefox 版本。

列表默认为空。每个写进去的主机名都会触发 Resist Fingerprinting（RFP）例外，同时放宽 Canvas、时区等信号的 RFP 防护，网站也仍有可能通过其他线索判断出真实系统。把列表改回 `[ ]` 并应用配置就能取消例外。
