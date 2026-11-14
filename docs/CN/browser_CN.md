# 受管 Firefox

[English](../browser.md) | 简体中文 | [文档目录](README_CN.md)

## 软件包与启动

Firefox 是原生 Nix 包。[firefox.nix](../../home/programs/firefox/firefox.nix) 将二进制、enterprise policies、AutoConfig 和固定字体绑定在不可变 store closure 中，Home Manager 管理独立的 `~/.config/mozilla/firefox/lfour` profile。更新跟随锁定的 Nixpkgs 和重建 generation，浏览器二进制自更新禁用；旧 Flatpak profile 不自动导入或删除。

公开启动器先运行 [guard.py](../../home/programs/firefox/guard.py)，再打开指定 profile。B 要求新鲜、同代次的模式/DNS/relay/验证状态、被动监听身份检查，并通过桌面 GUI 前向 socket 核对实时 API 不变量。私有配置快照及内部控制口由 root 监督器验证，浏览器使用其发布的就绪结果，不读取私有文件。日常 Firefox 允许 A 和健康 B；身份入口要求健康 B，门户入口仅允许显式 A。C、未知和 `unenforced` 拒绝启动；二次状态读取拒绝预检中途的切换，启动后的变化由 relay 和内核策略控制。

启动器在严格隐私下检查字体，然后在下述外层沙箱中运行浏览器。宿主实例记录结合进程启动时间与包身份拒绝复用旧包；陈旧 profile 锁中的命名空间 PID 不作为桌面进程身份。桌面/MIME 入口均使用它，原 Zen/Chrome ID 转向 Firefox。策略更新后需完整重启浏览器。`firefox --check` 仅检查前提，不创建 profile 或开窗口。

## 外层沙箱

[sandbox.py](../../home/programs/firefox/sandbox.py) 保留桌面 UID；严格浏览器另有独立网络命名空间，由 bubblewrap 创建独立的挂载、用户、PID、IPC 和 UTS 命名空间。浏览器只看到合成主目录及私有 `/tmp`、`/dev`，清空 capabilities，以白名单重建环境，同时保留 Gecko 内容进程沙箱。复用原生 Firefox 二进制，由 Nix 包装和小型 [scope 辅助程序](../../home/programs/firefox/scope.c) 提供外层策略，不修改 Gecko 源码。

| 资源 | 浏览器权限 |
|---|---|
| 持久文件 | 可读写受管 profile、`~/.cache/firefox-managed` 和 `~/Downloads`；其它个人文件需经文件选择器授权 |
| 运行文件与信任 | 只读 Nix store、选定的解析器/CA 文件及图形驱动；不挂载完整宿主根目录、个人主目录或 runtime 目录 |
| 显示与音频 | 指定 Wayland socket、可用的 Pulse socket 与 DRM render 节点；声明 NVIDIA 加速时另开放对应 primary 节点及渲染接口。不开放 X11、输入设备、摄像头或 NVIDIA modeset 接口 |
| 会话总线 | 过滤代理仅允许文件选择、只读设置、请求/会话关闭、协议支持查询及当前精确 profile 的链接复用 |
| 其它宿主接口 | 不开放系统总线、SSH/GPG agent、容器或核心控制 socket；Landlock 禁止连接宿主抽象 Unix socket 和向作用域外发送信号 |

系统证书信任使用 `/etc/ssl/certs/ca-certificates.crt` 下的 NixOS CA bundle，包含声明的系统 CA。启动器解析其符号链接，将实际文件只读挂载到该路径，使 NSS/p11-kit 能读取它，同时不开放 `/etc/static` 或完整宿主证书目录。CA bundle 缺失时拒绝启动，证书校验保持开启。

`my.firefox.nvidiaAcceleration` 默认跟随 NixOS 的 NVIDIA 驱动选择。NVIDIA 用户态渲染器需要对应的 DRM primary 节点、带数字编号的 `/dev/nvidia*` 设备、`nvidiactl`、`nvidia-uvm` 及只读 EGL 外部平台配置。启动器只在宿主读取 sysfs，用于将 primary 节点与 NVIDIA render 设备配对；不挂载整个 `/sys` 或 `/dev`。其它 GPU 的 primary 节点、`nvidia-modeset`、UVM tools 与 capability 设备仍隐藏。将此选项设为 `false` 可保留仅 render 节点的设备权限。Gecko 沙箱继续启用，WebGL 仍由锁定偏好禁用。

系统调用过滤还禁止 keyring、进程检查、文件句柄打开及部分无需使用的内核接口，保留 Gecko 沙箱需要的命名空间和 seccomp 操作。要求 Landlock ABI 至少为 6，并允许非特权用户命名空间。隔离不可用或总线代理失败时拒绝启动或终止浏览器，不回退到无隔离运行。

总线代理运行于另一份最小挂载命名空间。只读 `/.flatpak-info` 和宿主实例 PID 元数据通过桌面门户既有的 Flatpak 身份协议，将浏览器识别为受限应用 `org.nixos.ManagedFirefox`，不安装 Flatpak 应用或运行时。只将 `doc/by-app/org.nixos.ManagedFirefox` 挂载为浏览器的文档树，其它应用的授权保持隐藏。锁定的文件选择器使用门户：选择个人文件后只开放该文档，不开放整个父目录。挂载目录以外的文件不能仅靠传入宿主路径打开；可写目录本身不允许是符号链接。

正常启动器通过当前精确 profile 的 D-Bus 名称把新链接交给已有窗口。代次记录和宿主启动锁不在浏览器挂载范围内。重建包后需关闭全部旧窗口，再打开新包；受管 profile 在重启后保留。`firefox --check` 还检查私有 runtime 目录、桌面 socket 和 Landlock 支持，不打开窗口。

网络继续使用桌面 UID 及已有 A/B/C 规则，包括 Gost `33332`、系统 DNS 和可选的原生 ECH 路径。外层策略不为代理端口认证浏览器，也不隔离其它本地 TCP 服务。Wayland、音频、Downloads、profile 和用户选择的文档是明确开放的权限。已在桌面 UID 下无隔离运行的其它程序仍属于信任基础，可以自行调用包内私有二进制。

## 网络策略与身份

`my.firefox.dailyRequireProxy` 默认为 `false`，与 `my.proxy.strictBrowser` 独立。默认日常浏览器使用 Gost `33332`：A 直连，使用本地网络的公网 IP；健康 B 经核心按订阅规则分流，可能包含 DIRECT。代理失败不会自动选择 A，两种模式均保留外层文件沙箱、TLS 和隐私设置。旧选项 `my.firefox.requireProxy` 仅作为日常选项的兼容别名。

`firefox-identity` 始终要求 `my.proxy.strictBrowser=true` 提供的严格入口；将 `dailyRequireProxy=true` 也会为日常浏览器选择该入口。严格浏览器只有 loopback 网络，命名空间内 `127.0.0.1:18080` 的 HTTP relay 连接 systemd 创建的 Unix socket。`clash-strict.socket` 为桌面用户安装 `/run/clash-strict/proxy.sock`，`--check` 与启动均校验其存在、类型及所有权。宿主 relay 仅在共享就绪状态为健康 B 时接受连接，失去就绪后终止已有流。100 ms 轮询是在共享状态变化之后的额外上界，不代表瞬时发现故障。relay 在转发浏览器字节前核对已建立服务端 socket 的 UID，A/C 还在 nft 事务中切断严格入口流量。此命名空间不能访问任意宿主 TCP、本机 DNS、LAN 或直连公网 socket。

核心固定 `lf-strict` 入口，使用专用远程节点组，在实际叶子处拒绝 DIRECT/COMPATIBLE 和私网目标，GUI 选组或 Global 变化也不解除约束；普通桌面仍保留 Rule 路由。已排入 socket 或已发送到远端的数据无法撤回。相同桌面 UID 的其它未隔离进程仍受信任，可以自行调用包内私有二进制。

日常浏览器可在 `dailyRequireProxy=false` 时选择透明 ECH 路径，使用宿主 A/B/C 网络，不具备独立网络命名空间。该选择不改变身份及门户入口的网络方式。

日常 Firefox 与 `firefox-identity` 启用 HTTPS-only、禁止绕过证书/安全警告，并锁定反钓鱼、恶意软件和下载保护。身份包无扩展，profile、缓存、下载及文档门户身份独立，不复制已有 Cookie、账号或书签。Safe Browsing 更新和信誉查询可能披露查询/下载元信息，见 [Mozilla 保护说明](https://support.mozilla.org/en-US/kb/how-does-phishing-and-malware-protection-work)。合法证书不认证站点业务身份；HTTPS-only 仍有浏览器例外语义，不能宣称禁止所有可能的 HTTP 字节。

`firefox-portal URL` 仅在显式 A 接受一个字面 IP、80/443 端口的 HTTP(S) 地址。它使用独立临时 profile，无扩展、无已有凭据，独立网络命名空间只能连接单地址 relay，15 分钟后终止。日常浏览器关闭自身自动明文门户探测，系统 NetworkManager 连通性检测保持独立；遇到门户不会自动换 profile 或绕过策略。

普通浏览使用日常 profile，选定账号使用 `firefox-identity`；独立 profile 减少状态混用，但不能让已登录账号匿名或阻止流量关联。匿名活动还需独立选择受支持的匿名客户端，评估更新及出口策略；这些入口没有启用 Tor 或多跳保证。

WebRTC、HTTP/3 和浏览器 DoH 仍关闭。显式代理的域名解析由核心处理，经过共享后缀/目标检查；DNSSEC 与加密传输保留各自的 [DNS 边界](dns_CN.md)。

## ECH 发现与代理兼容

ECH 加密 TLS ClientHello 的内部内容，包括站点 SNI。本配置由浏览器从 HTTPS DNS 记录取得 ECHConfig。GREASE 添加形似 ECH 的合成扩展；真实 ECH 握手使用服务器的 ECHConfig 加密 ClientHelloInner。

### 浏览器路径

| `my.firefox.networkPath` | 名称发现与传输 | ECH 行为 |
|---|---|---|
| `"http-proxy"`，默认 | 严格本地 relay 或兼容 Gost HTTP 代理，目标解析委托核心 | 锁定的 Firefox 160.0 将 HTTPS CONNECT 排除在 HTTPS-RR 发现之外；保留 GREASE，HTTPS-RR 发现使用下方透明路径 |
| `"transparent"` | 系统 DNS 与原生 HTTPS-RR 查询；A 使用直连 socket，B 使用宿主 TUN/防火墙路径 | NSS 可使用返回的 ECHConfig，与支持的站点进行真实 ECH 握手 |

Firefox 的[连接门](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/netwerk/protocol/http/nsHttpChannel.cpp)同时检查代理 DNS 策略和 `UsingConnect()`，单改 DNS 传输不会改变这个门。本包禁用仅供测试的 `force_use_https_rr` override，透明选项复用打包浏览器，不修改 Gecko 源码。

在系统配置中显式选择：

```nix
my.proxy.tunMode = true;
home-manager.users.lfour.my.firefox.dailyRequireProxy = false;
home-manager.users.lfour.my.firefox.networkPath = "transparent";
```

重建 generation 并完整重启 Firefox。模块拒绝 `tunMode=false` 下的透明路径。网络路径在构建软件包时确定。启动器继续检查网络就绪；C 在内核阻断流量并拒绝新启动，A 保留字面 HTTP 门户。`bootMode`、`proxyOnly`、浏览器指纹防护和 `dnsEncryption` 均独立设置。

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

Unix 浏览器通过[原生解析 API](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/netwerk/dns/PlatformDNSUnix.cpp)查询 type 65。浏览器 DoH 保持关闭并锁定；现有解析链路已能传递 HTTPS DNS 消息，无须另开本地 DoH 入口。

核心保留地址记录的 fake-IP 行为，同时让 HTTPS 和 SVCB 记录经过正常解析器。共享过滤先执行，阻断域返回本地 NXDOMAIN，不交付 ECHConfig。记录参数连同 ECH public name 返回浏览器，核心不使用浏览器的 ECHConfig，也不终止站点 TLS。A 的 DNSSEC、B 的加密传输、已批准 split DNS 和 MagicDNS 保留[各自边界](dns_CN.md#各路径的责任)。

NSS 选择配置、构造 ClientHelloInner/Outer、验证证书并处理已认证的重试配置。GREASE 与 `ECHAccepted` 是不同观测：普通 TLS 可以携带 GREASE，而服务端仍报告未接受 ECH。

### 路由与失败行为

受管核心在 Rule 和统一代理模式均关闭嗅探。ECH 外层 SNI 是掩护名，不能替代实际目标；显式代理目标和 fake-IP 映射提供域名规则需要的名称，字面 IP 按 IP 规则处理。Root parser 也会将此策略应用到旧 GUI 配置。

没有可用 HTTPS/ECH 记录时，仍允许正常验证证书的普通 TLS，ECH 按站点提供的配置启用。`network.dns.echconfig.fallback_to_origin_when_all_failed=false` 禁止该偏好控制的 origin 回退，但不禁止 NSS 独立的“认证后不带 ECH 重试”：受信任服务器拒绝 ECH 且不提供可用重试密钥时，可以触发携带站点 SNI 的普通 TLS 新连接。允许已认证密钥重试，不可信拒绝或无效证书不授权这种重试。记录缺失/被剥离、DNS 故障与已认证拒绝的结果因此不同；`dnsEncryption="strict"` 约束 DNS 传输，不要求所有站点支持 ECH。

显式 HTTP 代理能看到 CONNECT 目标，其它客户端即使能通过代理使用 ECH 也不改变这一点。透明路径的本机核心可见 DNS/fake-IP 映射与路由目标。ECH 不隐藏目的 IP、时序、账户或站点可见的内容；协议接受和重试规则见 [RFC 9849](https://www.rfc-editor.org/rfc/rfc9849.html)。

### 集成维护

`browser-ech-vm` 在外层沙箱中运行所选生产 Firefox 包及真实 helper/核心，配合本地 DNS、临时 CA、ECH 服务端及另一个地址上的掩护名陷阱。它区分已接受 ECH、普通 TLS/GREASE、密钥重试、认证后不带 ECH 重试、不可信拒绝和证书失败，检查掩护 SNI 不改变拨号目标，并保留 A 门户/C 启动检查；只使用全新 profile，不连接个人浏览器。

浏览器或核心变更后，将 `browser-ech-vm`、`browser-ech-uniform-vm` 与 `browser-sandbox-vm`、`browser-privacy`、`proxy-core`、`proxy-profile` 一同运行。两种路由策略都检查真实锁定偏好与两条浏览器网络路径。检查范围包括浏览器的 CONNECT 门、原生解析、重试处理，以及核心 fake-IP 下的服务记录处理；使用[源码兼容性检查](maintenance_CN.md#源码兼容性检查)，并同步所选版本的路径说明。

## 隐私设置

模块固定启用 `strictPrivacy`。AutoConfig `lockPref` 覆盖 enterprise policy 无法锁定的命名空间，对该包打开的所有 profile 生效，抵抗冲突 `user.js`。

| 层 | 行为与边界 |
|---|---|
| 指纹 | RFP 和 letterboxing 减少 Canvas、时区、硬件和窗口尺寸信号；定制桌面仍不同于 Tor Browser |
| 跟踪与状态 | Strict protection、FPP、Total Cookie Protection 减少脚本和跨站状态关联 |
| URL 与分析 | 查询参数清理、bounce 清理、跨源 referrer 限制和 GPC；禁用建议、预连接、遥测、studies、赞助内容、Sync 与内置 IP Protection |
| API 与字体 | 禁用 WebRTC、WebGPU、地理位置、电池和传感器；严格隐私另禁 WebGL，仅暴露固定 DejaVu/Noto/CJK/emoji 字体 |
| ECH / TLS | 启用原生 HTTPS-RR、ECH 和 GREASE；透明模式支持 ECH 发现，锁定浏览器的默认 CONNECT 路径不支持 |
| 出口地址 | 共用 A/B/C 与路由策略；默认 B 按 Rule 选择 DIRECT 或代理节点 |

严格扩展策略阻止未声明扩展，强制安装 uBlock Origin，KISS Translator 可移除。签名包及更新经选定网络路径从 Mozilla Add-ons 获取，需要联网；包策略和 Gecko 保护不依赖扩展下载完成。

Gecko 内容进程沙箱与外层策略约束不同层次：外层策略限制原生父进程及其后代，隐私偏好约束浏览器行为，两者都不改变前述路由和解析器的信任边界。

软件包升级与检查见[维护手册](maintenance_CN.md)，解析行为见 [DNS](dns_CN.md)。
